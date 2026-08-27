"""codeverse/proc.py — subprocess primitives (Batch-1 foundations)."""

from __future__ import annotations

import json
import os
import resource
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from codeverse.proc import (
    STREAM_BUDGET_BYTES,
    ManagedProcess,
    ProcResult,
    append_jsonl_line,
    iter_jsonl_lines,
    read_json_or_none,
    read_jsonl_lenient,
    run_subprocess,
    scrub_secrets,
    tail,
    write_json_atomic,
)


def test_run_subprocess_captures_output(tmp_path: Path):
    r = run_subprocess(["bash", "-c", "echo out; echo err >&2; exit 3"], cwd=tmp_path, timeout_s=10)
    assert isinstance(r, ProcResult)
    assert r.returncode == 3
    assert r.stdout.strip() == "out"
    assert r.stderr.strip() == "err"
    assert not r.timed_out
    assert r.duration_ms >= 0


def test_run_subprocess_stdin(tmp_path: Path):
    r = run_subprocess(["cat"], cwd=tmp_path, timeout_s=10, stdin_text="hello")
    assert r.stdout == "hello"


def _assert_pid_gone(pid: int, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return  # gone — the group kill reached it
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} survived the group kill")


def test_timeout_kills_the_whole_process_group(tmp_path: Path):
    """A timed-out child AND its grandchildren die (killpg on the new session)."""
    r = run_subprocess(["bash", "-c", "sleep 30 & echo $!; wait"], cwd=tmp_path, timeout_s=0.4)
    assert r.timed_out
    assert r.returncode != 0
    _assert_pid_gone(int(r.stdout.strip().splitlines()[0]))


def test_a_leader_that_exits_0_still_takes_its_grandchildren_down(tmp_path: Path):
    """``__exit__`` asked "is the LEADER still running?", so a leader that exited 0 left
    its same-group grandchildren (pgid == leader pid) alive — the exact incident this
    module exists to end.  Detached-stdio shape: nothing holds the pipes."""
    pid_file = tmp_path / "pid"
    r = run_subprocess(["bash", "-c", f"sleep 300 >/dev/null 2>&1 & echo $! > {pid_file}; exit 0"],
                       cwd=tmp_path, timeout_s=10)
    assert r.returncode == 0 and not r.timed_out
    _assert_pid_gone(int(pid_file.read_text()))


def test_a_leader_that_exits_0_does_not_burn_the_drain_window(tmp_path: Path, monkeypatch):
    """Same shape with the grandchild holding the INHERITED pipes: the drain used to
    wait out DRAIN_TIMEOUT_S for a process nobody had signalled, then blame a
    "detached descendant" that had never left the group."""
    import codeverse.proc as proc_mod

    monkeypatch.setattr(proc_mod, "DRAIN_TIMEOUT_S", 3.0)
    pid_file = tmp_path / "pid"
    t0 = time.monotonic()
    r = run_subprocess(["bash", "-c", f"sleep 300 & echo $! > {pid_file}; echo done"],
                       cwd=tmp_path, timeout_s=10)
    assert r.returncode == 0 and "done" in r.stdout
    assert time.monotonic() - t0 < 2.5, "the drain window was burned on a live group member"
    assert "detached descendant" not in r.stderr
    _assert_pid_gone(int(pid_file.read_text()))


def test_a_detached_descendant_holding_the_pipes_cannot_extend_the_timeout(tmp_path: Path, monkeypatch):
    """CP-4: after TimeoutExpired the code called a SECOND communicate() with no
    timeout.  killpg reaps only the child's own session, so a descendant that
    setsid'd while inheriting stdout keeps the pipe open and that call blocked until
    IT exited — unbounded.  A caller passing timeout_s=N was never released at N.
    """
    import codeverse.proc as proc_mod

    # raising=False so this test still RUNS (and fails on the hang) against the
    # pre-fix module, which has no such constant
    monkeypatch.setattr(proc_mod, "DRAIN_TIMEOUT_S", 1.0, raising=False)
    t0 = time.monotonic()

    r = run_subprocess(["bash", "-c", "setsid sleep 20 & echo started; sleep 20"],
                       cwd=tmp_path, timeout_s=1.0)

    elapsed = time.monotonic() - t0
    assert r.timed_out and r.returncode != 0
    # timeout_s + one drain window + slack — NOT the 20 s the escaped descendant lives
    assert elapsed < 8.0, f"run_subprocess returned only after {elapsed:.1f}s"


def test_a_same_group_grandchild_still_has_its_output_collected(tmp_path: Path):
    """The bound must not cost us the normal case: a grandchild inside the group is
    killed by killpg, so the drain completes and the output survives."""
    r = run_subprocess(["bash", "-c", "echo hello; sleep 30 & wait"], cwd=tmp_path, timeout_s=0.4)
    assert r.timed_out and "hello" in r.stdout


def test_preexec_fn_runs_in_the_child(tmp_path: Path):
    soft = 256

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, soft))

    r = run_subprocess(
        ["python3", "-c", "import resource, sys; sys.stdout.write(str(resource.getrlimit(resource.RLIMIT_NOFILE)[0]))"],
        cwd=tmp_path, timeout_s=20, preexec_fn=limit,
    )
    assert r.stdout.strip() == str(soft)



def test_tail_caps_lines_and_chars():
    text = "\n".join(f"line{i}" for i in range(100))
    t = tail(text, max_lines=5)
    assert t.splitlines() == [f"line{i}" for i in range(95, 100)]
    assert len(tail("x" * 10000, max_chars=100)) == 100


def test_write_json_atomic_no_partial_file(tmp_path: Path):
    p = tmp_path / "deep" / "out.json"
    write_json_atomic(p, {"a": 1, "p": Path("b")})
    assert json.loads(p.read_text()) == {"a": 1, "p": "b"}
    assert not p.with_suffix(".json.tmp").exists()


def test_workspace_write_json_delegates(tmp_ws):
    from pydantic import BaseModel

    class M(BaseModel):
        x: int = 2

    p = tmp_ws.root / "artifacts" / "m.json"
    tmp_ws.write_json(p, M())
    assert json.loads(p.read_text()) == {"x": 2}
    assert not p.with_suffix(".json.tmp").exists()


_WRITER = """
import json, sys
from pathlib import Path
from codeverse.proc import write_json_atomic

path, tag, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
payload = {"writer": tag, "blob": [tag * 40] * 900}
bad = 0
for _ in range(n):
    write_json_atomic(path, payload)          # a second writer must not break this one
    try:
        json.loads(path.read_text())          # ... and readers must never see a partial file
    except ValueError:
        bad += 1
print(bad)
"""


def test_write_json_atomic_survives_concurrent_writers(tmp_path: Path):
    """RS-1: `tmp = path.with_suffix('.tmp')` was ONE name for every writer, so writer A
    renamed B's half-written tmp into place (readers saw truncated / zero-byte JSON at the
    published path) and B's own replace() then died with FileNotFoundError.  Real callers
    share a path: the scene track fans zone agents out over one workspace root and each
    agent's MCP `build` tool writes artifacts/build_last.json through this function.

    Three processes, same path.  With a private tmp per writer this cannot fail; on the old
    code it fails within a few dozen iterations."""
    import subprocess

    harness = Path(__file__).resolve().parents[2]
    target = tmp_path / "shared.json"
    procs = [subprocess.Popen([sys.executable, "-c", _WRITER, str(target), tag, "40"],
                              cwd=harness, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for tag in ("A", "B", "C")]
    outs = [(p.wait(), *p.communicate()) for p in procs]
    for rc, out, errtext in outs:
        assert rc == 0, f"a writer crashed: {errtext[-500:]}"
        assert out.strip() == "0", f"a reader saw a partial file {out.strip()} time(s)"
    assert json.loads(target.read_text())["writer"] in ("A", "B", "C")  # last writer wins, whole
    assert not list(tmp_path.glob("*.tmp")), "no temp file left behind"
# --------------------------------------------------------------------------- atomic writes
def test_unique_tmp_is_per_process_and_per_thread(tmp_path: Path):
    """A FIXED '<name>.tmp' is what made concurrent writers of one destination race.

    The threads are held at a BARRIER so all 8 are alive when they name their temp file.
    That is the property CQ-2 actually needs — two writers racing on one destination at
    the same instant must not choose the same name — and it is the only one
    ``threading.get_ident()`` promises: an ident is unique among *living* threads and is
    explicitly documented as recyclable once a thread exits.  Without the barrier these
    8 one-line threads finish before the next starts, CPython hands out the same ident
    every time, and the assertion reduces to 1 != 8 — which is exactly how this failed
    on the Python 3.10 floor job (it happened to pass on 3.13) on the sign-off clean
    clone, 2026-08-24.  The fix under test was never wrong; the test was.
    """
    from codeverse.proc import unique_tmp

    out = tmp_path / "cache" / "checker.mjs"
    seen: list[Path] = []
    lock = threading.Lock()
    gate = threading.Barrier(8, timeout=30)

    def name_it() -> None:
        gate.wait()  # every thread is alive and running past this point
        got = unique_tmp(out)
        with lock:
            seen.append(got)

    threads = [threading.Thread(target=name_it) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(seen) == 8
    assert len({str(p) for p in seen}) == 8, "two live threads chose the same temp name"
    assert all(p.parent == out.parent and p.name.startswith("checker.mjs.") for p in seen)
    assert str(os.getpid()) in seen[0].name


def test_concurrent_writers_of_one_destination_all_succeed(tmp_path: Path):
    """CQ-2: the second writer's replace() used to find its source already renamed
    away -> FileNotFoundError.  Barrier-synchronised, this failed ~half the time."""
    from codeverse.proc import write_text_atomic

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


def test_write_json_atomic_round_trips(tmp_path: Path):
    out = tmp_path / "d" / "x.json"
    write_json_atomic(out, {"a": 1})
    assert json.loads(out.read_text()) == {"a": 1}
    assert [p.name for p in out.parent.iterdir()] == ["x.json"]


def test_read_json_or_none_is_none_unless_a_dict_parses(tmp_path: Path):
    assert read_json_or_none(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert read_json_or_none(tmp_path / "bad.json") is None
    (tmp_path / "list.json").write_text("[1, 2]")
    assert read_json_or_none(tmp_path / "list.json") is None
    (tmp_path / "ok.json").write_bytes(b'{"a": "caf\xc3\xa9", "b": "\xff"}')
    assert read_json_or_none(tmp_path / "ok.json") is None  # undecodable byte -> ValueError
    assert read_json_or_none(tmp_path / "ok.json", errors="replace") == {"a": "café", "b": "\ufffd"}


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
def test_scrub_secrets_drops_credential_shaped_vars():
    env = {
        "GEMINI_API_KEYS": "k1,k2", "GEMINI_API_KEY": "k", "FOO_API_KEY": "x",
        "MY_SERVICE_TOKEN": "t", "DB_PASSWORD": "p", "DEPLOY_PRIVATE_KEY": "s",
        "AWS_SECRET_ACCESS_KEY": "a", "CLIENT_SECRET": "c", "X_AUTH_TOKEN": "z",
    }
    assert scrub_secrets(env) == {}


def test_scrub_secrets_keeps_everything_generated_code_needs():
    env = {"PATH": "/usr/bin", "HOME": "/home/u", "DISPLAY": ":0", "NODE_PATH": "/nm",
           "MESA_LOADER_DRIVER_OVERRIDE": "d3d12", "GALLIUM_DRIVER": "llvmpipe",
           "CV3D_RENDER_GPU": "off", "PYTHONUNBUFFERED": "1",
           "TOKENIZERS_PARALLELISM": "false"}  # _TOKEN is a SUFFIX match, not a substring
    assert scrub_secrets(dict(env)) == env



def _assert_group_gone(pgid: int, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return  # every member is dead AND reaped
        time.sleep(0.05)
    raise AssertionError(f"process group {pgid} is still alive")


#: writes its own pid to argv[1], then hangs — a child that ignores nothing.
_PID_THEN_SLEEP = (
    "import os, pathlib, sys, time\n"
    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
    "time.sleep(300)\n"
)


@pytest.mark.timeout(60, method="thread")  # thread method: the test drives SIGALRM itself
def test_keyboard_interrupt_kills_the_group(tmp_path: Path):
    """``start_new_session`` puts the child outside the terminal's foreground group,
    so Ctrl-C's SIGINT NEVER reaches it — the parent's KeyboardInterrupt must kill
    the group itself.  Pre-fix there was no try/finally around communicate(): every
    interrupt during a blender/node run orphaned the whole tree (the wedged-browser
    incident).  The KI must still propagate to the caller."""
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
    _assert_group_gone(int(pid_file.read_text()))


def test_invalid_utf8_replaces_instead_of_raising(tmp_path: Path):
    """text=True with a strict decode raised UnicodeDecodeError out of communicate()
    — one bad byte lost the whole result.  Now: U+FFFD, like the JSONL readers here."""
    r = run_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end')"],
        cwd=tmp_path, timeout_s=30,
    )
    assert r.returncode == 0 and not r.timed_out
    assert r.stdout == "ok �� end"


@pytest.mark.timeout(120)
def test_huge_output_is_bounded_head_and_tail(tmp_path: Path):
    """Output used to be collected unbounded in RAM (and written whole to the
    trajectory).  ~48 MB in → at most the budget (+ marker) out, keeping both ends."""
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


# --------------------------------------------------------------------------- ManagedProcess lifecycle
def _raise_ki(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def test_managed_process_reaps_on_exit_no_zombie(tmp_path: Path):
    with ManagedProcess([sys.executable, "-c", "print('hi')"], cwd=tmp_path) as mp:
        mp.wait(timeout=30)
        pid = mp.pid
    assert mp.stdout_text.strip() == "hi"
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)  # __exit__ already reaped it — no zombie left
