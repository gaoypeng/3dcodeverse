"""One writer per run directory — see codeverse/runlock.py."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from codeverse.runlock import (
    LOCKS_DIR,
    RunLocked,
    exclusive,
    flock_path,
    holder_of,
)

HARNESS_ROOT = Path(__file__).resolve().parents[2]


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
    """Measured 2026-08-25: a lane launched `3dcv resume tsr_scn_temple_night` three
    times and two ran concurrently on the same workspace for four minutes, both writing
    run_state.json, both snapshotting src/ into the same git repo, both spending the
    run's budget."""
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
        msg = str(got.value)
        assert str(child.pid) in msg  # names the holder, so a human can kill THAT pid
        assert "pkill" in msg, "and must warn against the pattern kill that took out 13 runs"
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
    assert holder_of(run_root) is None, "a SIGKILLed holder cannot leave a record behind"
    with exclusive(run_root):   # no stale-PID reasoning needed: the kernel already released it
        pass


def test_a_second_thread_is_refused_and_the_first_keeps_the_lock(tmp_path: Path):
    """The bench drivers run runs in a THREAD pool of one process.

    Keying the held set on the path alone made a second thread walk straight into a run
    the first was inside — and worse, whichever left first unlocked and closed the fd
    while the other was still working, handing the run to any outside process.
    """
    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    entered, refused = threading.Event(), []
    release = threading.Event()

    def t2() -> None:
        try:
            with exclusive(run_root, what="T2"):
                refused.append("ENTERED WHILE T1 HELD IT")
        except RunLocked as e:
            refused.append(str(e))

    with exclusive(run_root, what="T1"):
        with exclusive(run_root, what="T1 again"):  # the SAME thread re-enters
            pass
        th = threading.Thread(target=t2)
        th.start()
        th.join(timeout=10)
        entered.set()
        assert refused and "refusing to enter" in refused[0], refused
        # T2's refusal must not have released T1's lock: the run is still held
        assert (holder_of(run_root) or {}).get("what") == "T1"
    release.set()
    assert entered.is_set()
    with exclusive(run_root):   # fully released after the owning thread exits
        pass


def test_a_failed_record_write_leaks_nothing(tmp_path: Path, monkeypatch):
    """The record write lives INSIDE the try that owns the fd.

    Outside it, a failure left an fd holding the flock with nothing registered to release
    it — every process was refused for the lifetime of this one, by a message naming an
    empty holder.
    """
    import codeverse.runlock as R

    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    monkeypatch.setattr(R, "_write_record", lambda fd, rec: (_ for _ in ()).throw(OSError("ENOSPC")))
    with pytest.raises(OSError, match="ENOSPC"), R.exclusive(run_root):
        pass
    monkeypatch.undo()
    assert not R._HELD, "nothing may stay registered after a failed acquisition"
    with R.exclusive(run_root):   # still enterable, in this process and any other
        pass


def test_holder_of_names_the_holder_and_is_empty_otherwise(tmp_path: Path):
    """`3dcv status` prints this so a human can kill THAT pid, never `pkill -f 3dcv`."""
    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    assert holder_of(run_root) is None
    with exclusive(run_root, what="3dcv make slug"):
        held = holder_of(run_root)
        assert held is not None and held["pid"] == os.getpid()
        assert held["what"] == "3dcv make slug"
    assert holder_of(run_root) is None, "the record is truncated on release, never left to lie"


def test_the_lock_file_lives_outside_the_run_directory(tmp_path: Path):
    """A --force wipe rmtree's the run root; a lock inside it would be deleted mid-hold."""
    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    with exclusive(run_root):
        lock = flock_path(run_root)
        assert lock.is_file()
        assert run_root not in lock.parents
        assert lock.parent == run_root.parent / LOCKS_DIR


def test_force_refuses_to_wipe_a_run_another_holder_is_using(tmp_path: Path):
    """`--force` means "overwrite a DEAD run", never "evict a running one": create_workspace
    rmtree's the run root, and `make` holds the mutex around it (cli/main.py)."""
    import subprocess
    import sys

    from codeverse.cli._common import CliError, mutating

    run_root = tmp_path / "runs" / "held_run"
    run_root.mkdir(parents=True)
    (run_root / "spec.json").write_text("{}")  # non-empty, so --force would rmtree it
    ready, go = tmp_path / "ready", tmp_path / "go"
    child = subprocess.Popen([sys.executable, "-c", _child_script(run_root, ready, go)])
    try:
        for _ in range(500):
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.read_text() == "held"
        with pytest.raises(CliError) as ei, mutating(run_root, what="3dcv make held_run"):
            pytest.fail("the mutation boundary must not be entered")
        assert ei.value.exit_code == 2
        assert (run_root / "spec.json").exists(), "the live holder's workspace must survive"
    finally:
        go.write_text("release")
        child.wait(timeout=10)
