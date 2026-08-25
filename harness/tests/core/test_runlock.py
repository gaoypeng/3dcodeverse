"""One writer per run directory — see codeverse/runlock.py."""

from __future__ import annotations

import json
import os

import pytest

from codeverse.runlock import RunLocked, _lock_path, run_lock


def test_a_second_process_cannot_enter_a_locked_run(tmp_path):
    """Measured 2026-08-25: a lane launched `3dcv resume tsr_scn_temple_night` three
    times and two ran concurrently on the same workspace for four minutes, both writing
    run_state.json, both snapshotting src/ into the same git repo, both spending the
    run's budget."""
    with run_lock(tmp_path, what="3dcv make demo"):
        with pytest.raises(RunLocked) as ei:
            with run_lock(tmp_path):
                pytest.fail("the second entry must not be granted")
    msg = str(ei.value)
    assert str(os.getpid()) in msg, "the message must name the PID so ONE process can be killed"
    assert "pkill" in msg, "and must warn against the pattern kill that took out 13 runs"


def test_the_lock_is_released_on_the_way_out(tmp_path):
    with run_lock(tmp_path):
        pass
    with run_lock(tmp_path):  # must not raise
        pass
    assert not _lock_path(tmp_path).exists()


def test_an_exception_still_releases_the_lock(tmp_path):
    with pytest.raises(ZeroDivisionError):
        with run_lock(tmp_path):
            raise ZeroDivisionError
    with run_lock(tmp_path):
        pass


def test_a_dead_holder_is_taken_over_not_honoured(tmp_path):
    """A killed or crashed run must stay resumable — the pkill incident left 13 runs
    that all needed to be re-entered."""
    p = _lock_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # PID 2^22 is above /proc/sys/kernel/pid_max on any normal box, so it cannot exist
    p.write_text(json.dumps({"pid": 4194303, "started": 0, "what": "3dcv make ghost"}))
    with run_lock(tmp_path):
        assert json.loads(p.read_text())["pid"] == os.getpid()


@pytest.mark.parametrize("junk", ["", "not json", '{"no pid": 1}', '{"pid": "abc"}'])
def test_an_unreadable_lock_never_bricks_a_run(tmp_path, junk):
    """A truncated write from a crash must not make the run permanently unenterable."""
    p = _lock_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(junk)
    with run_lock(tmp_path):
        pass
