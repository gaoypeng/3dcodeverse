"""run_with_watchdog's CLOCKS: idle kill, activity extension, hard timeout.

The process lifecycle underneath (pipes, stdin writer thread, group kill,
KeyboardInterrupt cleanup, drain/reap) is ``proc.ManagedProcess`` and is owned by
tests/core/test_proc.py; this module keeps one integration smoke plus the clocks.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from codeverse3d.agents.cli_common import run_with_watchdog
from tests.conftest import assert_pid_gone

PY = sys.executable

#: spawns a grandchild (pid to argv[1]), prints nothing, then hangs — the shape that
#: proves a timeout kill takes the whole session, not just the leader.
_SPAWN_THEN_HANG = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen(['sleep', '300'])\n"
    "open(sys.argv[1], 'w').write(str(p.pid))\n"
    "time.sleep(300)\n"
)


def test_normal_run_streams_lines_and_delivers_stdin(tmp_path: Path):
    """The integration smoke: stdin reaches the child, every line reaches ``on_line``
    and the bounded texts, and stdout/stderr stay separate."""
    seen = []
    res = run_with_watchdog(
        [PY, "-c", "import sys; print(sys.stdin.read().upper()); print('b', file=sys.stderr); print('c')"],
        cwd=tmp_path, env=None, soft_timeout_s=10, idle_grace_s=5, hard_timeout_s=20,
        stdin="hello", on_line=lambda stream, text: seen.append((stream, text)),
    )
    assert res.rc == 0 and not res.timed_out
    assert res.stdout.splitlines() == ["HELLO", "c"]
    assert res.stderr.strip() == "b"
    assert ("stdout", "HELLO") in seen and ("stderr", "b") in seen


def test_idle_kill_after_soft_timeout_takes_the_whole_group(tmp_path: Path):
    """Silent past soft+grace → ``killed_reason="idle"``, and the grandchild dies with
    it (``start_new_session`` means only a group kill can reach it)."""
    marker = tmp_path / "child.pid"
    t0 = time.time()
    res = run_with_watchdog([PY, "-c", _SPAWN_THEN_HANG, str(marker)], cwd=tmp_path, env=None,
                            soft_timeout_s=1, idle_grace_s=1, hard_timeout_s=30, poll_s=0.2)
    assert res.timed_out and res.killed_reason == "idle"
    assert time.time() - t0 < 15
    assert_pid_gone(int(marker.read_text()))


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


@pytest.mark.timeout(60, method="thread")
def test_child_that_never_reads_a_big_stdin_still_times_out(tmp_path: Path):
    """The prompt used to be written synchronously before the poll loop: a child that
    never reads >64 KiB of stdin blocked ``stdin.write`` forever and BOTH clocks were
    dead (codex pipes prompts over 100 kB this way).  1 MB in, must still die."""
    t0 = time.monotonic()
    res = run_with_watchdog([PY, "-c", "import time; time.sleep(300)"], cwd=tmp_path, env=None,
                            soft_timeout_s=0.5, idle_grace_s=0.5, hard_timeout_s=8, poll_s=0.2,
                            stdin="x" * 1_000_000)
    assert res.timed_out and res.killed_reason in ("idle", "hard_timeout")
    assert time.monotonic() - t0 < 30
