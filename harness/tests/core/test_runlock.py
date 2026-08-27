"""One writer per run directory — see codeverse/runlock.py."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from codeverse.runlock import (
    LOCK_NAME,
    LOCKS_DIR,
    RunLocked,
    _lock_path,
    exclusive,
    flock_path,
    run_lock,
)

HARNESS_ROOT = Path(__file__).resolve().parents[2]


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


# ------------------------------------------------------- the flock mutex (2026-08-27)
def _child_script(run_root: Path, ready: Path, go: Path) -> str:
    """A second PROCESS that takes the run mutex, announces it, and waits to be told to let go."""
    return (
        "import sys, time, pathlib\n"
        f"sys.path.insert(0, {str(HARNESS_ROOT)!r})\n"
        "from codeverse.runlock import exclusive, RunLocked\n"
        f"root, ready, go = pathlib.Path({str(run_root)!r}), pathlib.Path({str(ready)!r}), pathlib.Path({str(go)!r})\n"
        "try:\n"
        "    with exclusive(root, what='child'):\n"
        "        ready.write_text('held')\n"
        "        while not go.exists():\n"
        "            time.sleep(0.02)\n"
        "except RunLocked as e:\n"
        "    ready.write_text('refused: ' + str(e))\n"
    )


def test_two_processes_cannot_hold_one_run(tmp_path: Path):
    """The PID file could be passed by BOTH racers (check, then write); the flock cannot."""
    import subprocess
    import sys

    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    ready, go = tmp_path / "ready", tmp_path / "go"
    child = subprocess.Popen([sys.executable, "-c", _child_script(run_root, ready, go)])
    try:
        for _ in range(500):  # wait for the child to actually hold it
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.read_text() == "held", "child never took the lock"
        with pytest.raises(RunLocked) as got, exclusive(run_root):
            pass
        assert str(child.pid) in str(got.value)   # names the holder, so a human can kill THAT pid
    finally:
        go.write_text("release")
        child.wait(timeout=10)
    with exclusive(run_root):   # free once the holder exits
        pass


def test_a_dead_holder_never_wedges_a_run(tmp_path: Path):
    """The kernel drops a flock when the holder dies — a killed run stays resumable."""
    import signal
    import subprocess
    import sys

    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    ready, go = tmp_path / "ready", tmp_path / "go"
    child = subprocess.Popen([sys.executable, "-c", _child_script(run_root, ready, go)])
    for _ in range(500):
        if ready.exists():
            break
        time.sleep(0.02)
    assert ready.read_text() == "held"
    child.send_signal(signal.SIGKILL)
    child.wait(timeout=10)
    with exclusive(run_root):   # no stale-PID reasoning needed: the kernel already released it
        pass


def test_the_mutex_is_reentrant_in_one_process(tmp_path: Path):
    """The CLI holds it at the mutation boundary and the track's run_lock nests inside."""
    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    with exclusive(run_root, what="outer"):
        with run_lock(run_root, what="inner"):      # would self-refuse without re-entrancy
            assert (run_root / ".3dcv" / LOCK_NAME).is_file()
        with exclusive(run_root, what="inner2"):
            pass
    with exclusive(run_root):                        # fully released after the outermost exit
        pass


def test_the_lock_file_lives_outside_the_run_directory(tmp_path: Path):
    """A --force wipe rmtree's the run root; a lock inside it would be deleted mid-hold."""
    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    with exclusive(run_root):
        lock = flock_path(run_root)
        assert lock.is_file()
        assert run_root not in lock.parents
        assert lock.parent == run_root.parent / LOCKS_DIR
