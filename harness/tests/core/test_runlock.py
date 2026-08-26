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
    with (
        run_lock(tmp_path, what="3dcv make demo"),
        pytest.raises(RunLocked) as ei,
        run_lock(tmp_path),  # raises on __enter__, so the body below never runs
    ):
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
    with pytest.raises(ZeroDivisionError), run_lock(tmp_path):
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


# --------------------------------------------------------- --force must not evict a live run
def test_force_refuses_to_wipe_a_run_a_live_process_is_holding(tmp_path):
    """`--force` means "overwrite a DEAD run", never "evict a running one".

    create_workspace(force=True) does shutil.rmtree on the run root, which deletes the
    lock file itself — so before this check a forced run wiped a live holder's workspace
    out from under it and then took a fresh lock, with no error and no message.
    Reproduced 2026-08-25 on tsr_scn_boat_workshop_v2: two independent `3dcv make` on one
    slug, 33 seconds apart, both writing.
    """
    from codeverse.cli._common import CliError, create_workspace

    root = tmp_path / "held_run"
    root.mkdir()
    (root / "spec.json").write_text("{}")  # non-empty, so --force would rmtree it
    with run_lock(root, what="3dcv make held_run"):
        with pytest.raises(CliError) as ei:
            create_workspace(root, force=True)
        assert str(os.getpid()) in str(ei.value)
        assert (root / "spec.json").exists(), "the live holder's workspace must survive"


def test_force_still_overwrites_a_dead_run(tmp_path):
    """The check must not make a crashed run un-forceable — that would be worse than the
    bug it fixes, because 13 runs needed exactly this after the pkill incident."""
    import json

    from codeverse.cli._common import create_workspace

    root = tmp_path / "dead_run"
    (root / ".3dcv").mkdir(parents=True)
    (root / "spec.json").write_text("{}")
    _lock_path(root).write_text(json.dumps({"pid": 4194303, "started": 0, "what": "3dcv make ghost"}))
    create_workspace(root, force=True)  # must not raise
    assert not (root / "spec.json").exists(), "a dead run's directory is replaced as before"
