"""run_with_watchdog's clocks; the process lifecycle underneath is tests/core/test_proc.py's."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from codeverse3d.agents.cli_common import run_with_watchdog

PY = sys.executable


def test_hard_timeout_kills_active_process(tmp_path: Path):
    code = "import time,sys\nwhile True:\n print('tick'); sys.stdout.flush(); time.sleep(0.2)"
    t0 = time.time()
    res = run_with_watchdog([PY, "-c", code], cwd=tmp_path, env=None, soft_timeout_s=1, idle_grace_s=60,
                            hard_timeout_s=2, poll_s=0.2)
    assert res.timed_out and res.killed_reason == "hard_timeout"
    assert time.time() - t0 < 15




@pytest.mark.timeout(60, method="thread")
def test_child_that_never_reads_a_big_stdin_still_times_out(tmp_path: Path):
    """A child that never reads >64 KiB of stdin once blocked stdin.write and both clocks."""
    t0 = time.monotonic()
    res = run_with_watchdog([PY, "-c", "import time; time.sleep(300)"], cwd=tmp_path, env=None,
                            soft_timeout_s=0.5, idle_grace_s=0.5, hard_timeout_s=8, poll_s=0.2,
                            stdin="x" * 1_000_000)
    assert res.timed_out and res.killed_reason == "idle"
    assert time.monotonic() - t0 < 30
