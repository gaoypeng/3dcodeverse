"""Subprocess lifecycle, bounded capture, atomic writes, and tolerant reads."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from codeverse3d.proc import (
    STREAM_BUDGET_BYTES,
    ManagedProcess,
    append_jsonl_line,
    iter_jsonl_lines,
    read_jsonl_lenient,
    run_subprocess,
    scrub_secrets,
)
from tests.conftest import assert_pid_gone


def test_timeout_kills_the_whole_process_group(tmp_path: Path):
    """A timed-out child AND its grandchildren die (killpg), and their output is still collected."""
    r = run_subprocess(["bash", "-c", "echo hello; sleep 30 & echo $!; wait"], cwd=tmp_path, timeout_s=0.4)
    assert r.timed_out and r.returncode != 0
    assert r.stdout.splitlines()[0] == "hello"
    assert_pid_gone(int(r.stdout.strip().splitlines()[1]), timeout_s=5.0)


def test_a_leader_that_exits_0_does_not_burn_the_drain_window(tmp_path: Path, monkeypatch):
    """A clean exit takes same-group grandchildren down without burning the drain window."""
    import codeverse3d.proc as proc_mod

    monkeypatch.setattr(proc_mod, "DRAIN_TIMEOUT_S", 3.0)
    pid_file = tmp_path / "pid"
    t0 = time.monotonic()
    r = run_subprocess(["bash", "-c", f"sleep 300 & echo $! > {pid_file}; echo done"],
                       cwd=tmp_path, timeout_s=10)
    assert r.returncode == 0 and "done" in r.stdout
    assert time.monotonic() - t0 < 2.5, "the drain window was burned on a live group member"
    assert "detached descendant" not in r.stderr
    assert_pid_gone(int(pid_file.read_text()), timeout_s=5.0)


def test_a_detached_descendant_holding_the_pipes_cannot_extend_the_timeout(tmp_path: Path, monkeypatch):
    """A detached descendant holding stdout cannot extend the caller's timeout."""
    import codeverse3d.proc as proc_mod

    monkeypatch.setattr(proc_mod, "DRAIN_TIMEOUT_S", 1.0)
    t0 = time.monotonic()

    r = run_subprocess(["bash", "-c", "setsid sleep 20 & echo started; sleep 20"],
                       cwd=tmp_path, timeout_s=1.0)

    elapsed = time.monotonic() - t0
    assert r.timed_out and r.returncode != 0
    # timeout_s + one drain window + slack — NOT the 20 s the escaped descendant lives
    assert elapsed < 8.0, f"run_subprocess returned only after {elapsed:.1f}s"


# --------------------------------------------------------------------------- atomic writes
def test_concurrent_writers_of_one_destination_all_succeed(tmp_path: Path):
    """Barrier-synchronised writers (one pid, many threads) all publish one destination, no temp litter."""
    from codeverse3d.proc import write_text_atomic

    out = tmp_path / "shared.txt"
    n = 8
    bar = threading.Barrier(n)
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            bar.wait()
            write_text_atomic(out, "payload\n")
        except BaseException as e:  # noqa: BLE001 — the point of the test
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert out.read_text() == "payload\n"
    assert list(tmp_path.iterdir()) == [out]  # no temp litter left behind


# --------------------------------------------------------------------------- tolerant reads
def test_jsonl_helpers_round_trip_and_skip_bad_lines(tmp_path: Path):
    p = tmp_path / "log.jsonl"
    assert list(iter_jsonl_lines(p)) == [] and read_jsonl_lenient(p) == []
    lock = threading.Lock()
    append_jsonl_line(p, {"k": "é", "p": tmp_path}, lock)  # default=str for the Path
    append_jsonl_line(p, [1, 2], lock)
    with p.open("ab") as fh:  # what a SIGKILL mid-append leaves behind
        fh.write(b'\n{"k": "b\xff\n{"k": "tr')
    assert [i for i, _ in iter_jsonl_lines(p)] == [1, 2, 4, 5]  # 1-based, blank line 3 skipped
    rows = read_jsonl_lenient(p)
    assert rows == [{"k": "é", "p": str(tmp_path)}, [1, 2]]
    assert read_jsonl_lenient(p, dicts_only=True) == rows[:1]


# --------------------------------------------------------------------------- scrub_secrets
def test_scrub_secrets_drops_credentials_and_keeps_what_generated_code_needs():
    secrets = {
        "GEMINI_API_KEYS": "k1,k2", "GEMINI_API_KEY": "k", "FOO_API_KEY": "x",
        "MY_SERVICE_TOKEN": "t", "DB_PASSWORD": "p", "DEPLOY_PRIVATE_KEY": "s",
        "AWS_SECRET_ACCESS_KEY": "a", "CLIENT_SECRET": "c", "X_AUTH_TOKEN": "z",
    }
    env = {"PATH": "/usr/bin", "HOME": "/home/u", "DISPLAY": ":0", "NODE_PATH": "/nm",
           "MESA_LOADER_DRIVER_OVERRIDE": "d3d12", "GALLIUM_DRIVER": "llvmpipe",
           "C3D_RENDER_GPU": "off", "PYTHONUNBUFFERED": "1",
           "TOKENIZERS_PARALLELISM": "false"}  # _TOKEN is a SUFFIX match, not a substring
    assert scrub_secrets({**env, **secrets}) == env


# --------------------------------------------------------------------------- ManagedProcess lifecycle
#: spawns a grandchild, writes its OWN pid (== the new session's pgid) to argv[1] and
#: hangs — so the probe below is a real GROUP probe, not just "the leader died".
_PID_THEN_SLEEP = (
    "import os, pathlib, subprocess, sys, time\n"
    "subprocess.Popen(['sleep', '300'])\n"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
    "time.sleep(300)\n"
)


def _raise_ki(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


@pytest.mark.timeout(60, method="thread")  # thread method: the test drives SIGALRM itself
def test_keyboard_interrupt_kills_the_group(tmp_path: Path):
    """KeyboardInterrupt kills the separate child process group and propagates."""
    pid_file = tmp_path / "pid"
    old = signal.signal(signal.SIGALRM, _raise_ki)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.8)  # fires while run_subprocess blocks in wait()
        with pytest.raises(KeyboardInterrupt):
            run_subprocess([sys.executable, "-c", _PID_THEN_SLEEP, str(pid_file)],
                           cwd=tmp_path, timeout_s=120)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old)
    assert_pid_gone(int(pid_file.read_text()), group=True)


def test_invalid_utf8_replaces_instead_of_raising(tmp_path: Path):
    """One bad byte is replaced instead of discarding the subprocess result."""
    r = run_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end')"],
        cwd=tmp_path, timeout_s=30,
    )
    assert r.returncode == 0 and not r.timed_out
    assert r.stdout == "ok �� end"


@pytest.mark.timeout(120)
def test_huge_output_is_bounded_head_and_tail(tmp_path: Path):
    """Large output is bounded while preserving its head, tail, and truncation marker."""
    code = (
        "import sys\n"
        "w = sys.stdout.buffer.write\n"
        "w(b'HEADSTART\\n')\n"
        "chunk = b'x' * (1 << 20)\n"
        "for _ in range(48):\n"
        "    w(chunk)\n"
        "w(b'\\nTAILEND\\n')\n"
    )
    r = run_subprocess([sys.executable, "-c", code], cwd=tmp_path, timeout_s=110)
    assert r.returncode == 0 and not r.timed_out
    assert len(r.stdout) <= STREAM_BUDGET_BYTES + 200  # the budget plus the truncation marker
    assert r.stdout.startswith("HEADSTART")            # head survived
    assert r.stdout.rstrip("\n").endswith("TAILEND")   # tail survived
    assert "bytes dropped" in r.stdout                 # marker sits between them
    cut = r.stdout.index("bytes dropped")
    assert "x" * 1000 in r.stdout[:cut] and "x" * 1000 in r.stdout[cut:]


def test_managed_process_reaps_on_exit_no_zombie(tmp_path: Path):
    with ManagedProcess([sys.executable, "-c", "print('hi')"], cwd=tmp_path) as mp:
        mp.wait(timeout=30)
        pid = mp.pid
    assert mp.stdout_text.strip() == "hi"
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)  # __exit__ already reaped it — no zombie left
