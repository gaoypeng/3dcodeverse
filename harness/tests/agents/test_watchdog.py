"""run_with_watchdog: streaming, idle kill, hard kill, process-group kill, stdin."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from codeverse.agents.watchdog import run_with_watchdog

PY = sys.executable


def test_normal_run_streams_lines(tmp_path: Path):
    seen = []
    res = run_with_watchdog(
        [PY, "-c", "import sys; print('a'); print('b', file=sys.stderr); print('c')"],
        cwd=tmp_path, env=os.environ, soft_timeout_s=10, idle_grace_s=5, hard_timeout_s=20,
        on_line=lambda stream, text: seen.append((stream, text)),
    )
    assert res.rc == 0 and not res.timed_out
    assert res.stdout.splitlines() == ["a", "c"]
    assert res.stderr.strip() == "b"
    assert ("stdout", "a") in seen and ("stderr", "b") in seen


def test_stdin_is_delivered(tmp_path: Path):
    res = run_with_watchdog([PY, "-c", "import sys; print(sys.stdin.read().upper())"], cwd=tmp_path, env=None,
                            soft_timeout_s=10, idle_grace_s=5, stdin="hello")
    assert res.stdout.strip() == "HELLO"


def test_idle_kill_after_soft_timeout(tmp_path: Path):
    t0 = time.time()
    res = run_with_watchdog([PY, "-c", "import time; time.sleep(60)"], cwd=tmp_path, env=None,
                            soft_timeout_s=1, idle_grace_s=1, hard_timeout_s=30, poll_s=0.2)
    assert res.timed_out and res.killed_reason == "idle"
    assert time.time() - t0 < 15


def test_activity_extends_past_soft_timeout(tmp_path: Path):
    # prints every 0.3s for ~2.5s: soft=1, grace=1 → must NOT be killed (activity keeps it alive)
    code = "import time,sys\nfor i in range(8):\n print(i); sys.stdout.flush(); time.sleep(0.3)\nprint('done')"
    res = run_with_watchdog([PY, "-c", code], cwd=tmp_path, env=None, soft_timeout_s=1, idle_grace_s=1,
                            hard_timeout_s=30, poll_s=0.2)
    assert not res.timed_out and res.stdout.strip().endswith("done")


def test_hard_timeout_kills_active_process(tmp_path: Path):
    code = "import time,sys\nwhile True:\n print('tick'); sys.stdout.flush(); time.sleep(0.2)"
    t0 = time.time()
    res = run_with_watchdog([PY, "-c", code], cwd=tmp_path, env=None, soft_timeout_s=1, idle_grace_s=60,
                            hard_timeout_s=2, poll_s=0.2)
    assert res.timed_out and res.killed_reason == "hard_timeout"
    assert time.time() - t0 < 15


def test_process_group_is_killed(tmp_path: Path):
    """A grandchild (sleep) must die with the parent on timeout."""
    marker = tmp_path / "child.pid"
    code = (
        "import subprocess, time, sys\n"
        f"p = subprocess.Popen(['sleep', '300']); open({str(marker)!r}, 'w').write(str(p.pid)); sys.stdout.flush()\n"
        "time.sleep(300)"
    )
    res = run_with_watchdog([PY, "-c", code], cwd=tmp_path, env=None, soft_timeout_s=1, idle_grace_s=1,
                            hard_timeout_s=30, poll_s=0.2)
    assert res.timed_out
    pid = int(marker.read_text())
    time.sleep(0.5)
    alive = True
    try:
        os.kill(pid, 0)
        # zombie check: /proc/<pid>/status State
        st = Path(f"/proc/{pid}/status").read_text() if Path(f"/proc/{pid}/status").exists() else "State:\tZ"
        alive = "State:\tZ" not in st
    except ProcessLookupError:
        alive = False
    assert not alive


def test_file_mtime_counts_as_activity(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    # silent process that touches a file every 0.5s for 3s; soft=1 grace=1.5 → alive until done
    code = (
        "import time\n"
        f"p = {str(src / 'a.txt')!r}\n"
        "for i in range(6):\n open(p, 'a').write('x'); time.sleep(0.5)\n"
    )
    res = run_with_watchdog([PY, "-c", code], cwd=tmp_path, env=None, soft_timeout_s=1, idle_grace_s=1.5,
                            hard_timeout_s=30, poll_s=0.2, scan_s=0.3, activity_dirs=[src])
    assert res.rc == 0 and not res.timed_out


def _raise_ki(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


@pytest.mark.timeout(60, method="thread")  # thread method: the test drives SIGALRM itself
def test_keyboard_interrupt_kills_the_group(tmp_path: Path):
    """A KI in the poll loop used to leak the whole start_new_session tree (no
    try/finally anywhere) — and Ctrl-C's SIGINT never reaches that tree on its own.
    The group must die and the KI must still propagate."""
    pid_file = tmp_path / "pid"
    code = (
        "import os, pathlib, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(300)\n"
    )
    old = signal.signal(signal.SIGALRM, _raise_ki)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.8)  # lands in time.sleep(poll_s)
        with pytest.raises(KeyboardInterrupt):
            run_with_watchdog([PY, "-c", code, str(pid_file)], cwd=tmp_path, env=None,
                              soft_timeout_s=120, idle_grace_s=120, hard_timeout_s=300, poll_s=0.1)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old)
    pgid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return  # gone and reaped
        time.sleep(0.05)
    raise AssertionError(f"process group {pgid} survived the KeyboardInterrupt")


@pytest.mark.timeout(60, method="thread")
def test_child_that_never_reads_a_big_stdin_still_times_out(tmp_path: Path):
    """The prompt used to be written SYNCHRONOUSLY in the main thread before the
    poll loop started: a child that doesn't read >64 KiB of stdin blocked
    proc.stdin.write() forever and BOTH timeouts were dead.  Reachable for real:
    codex delivers prompts via stdin exactly when they exceed STDIN_PROMPT_BYTES
    (100 kB).  1 MB of stdin into a child that never reads it must still die on
    the clocks."""
    t0 = time.monotonic()
    res = run_with_watchdog([PY, "-c", "import time; time.sleep(300)"], cwd=tmp_path, env=None,
                            soft_timeout_s=0.5, idle_grace_s=0.5, hard_timeout_s=8, poll_s=0.2,
                            stdin="x" * 1_000_000)
    assert res.timed_out and res.killed_reason in ("idle", "hard_timeout")
    assert time.monotonic() - t0 < 30
