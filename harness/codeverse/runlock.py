"""One writer per run directory.

Nothing stopped two ``3dcv`` processes from entering the same run.  Both write
``run_state.json``, both snapshot ``src/`` into the same git repo, both append to the
same event log and both spend the run's budget.  ``workspace.py`` already saw the
symptom and retried around a contended ``.git/index.lock``; this is the cause.

Measured 2026-08-25: a lane launched ``3dcv resume tsr_scn_temple_night`` three times
after mistaking a surviving process for someone else's, and two of them ran concurrently
against the same workspace for four minutes.

Deliberately a PID file and not ``fcntl.flock``: the useful message here is *which*
process holds the run, so a human can look at it or kill that one PID — the lesson from
the same day's ``pkill -f "3dcv make"``, which killed thirteen unrelated runs because the
operator had no way to name just theirs.  A lock that says "held by pid 2992370, started
15:56:25" is worth more than one that only says "busy".
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "run.lock"


class RunLocked(RuntimeError):
    """Another live process is already running this run."""


def _lock_path(run_root: Path) -> Path:
    return Path(run_root) / ".3dcv" / LOCK_NAME


def _alive(pid: int) -> bool:
    """Is this PID a live process we could signal?

    ``os.kill(pid, 0)`` raising ``ProcessLookupError`` means gone; ``PermissionError``
    means alive but owned by someone else, which still counts as held.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _holder(path: Path) -> dict | None:
    """The live holder recorded in ``path``, or None when absent/stale/unreadable.

    An unreadable or truncated lock is treated as stale rather than fatal: a crashed
    process must never leave a run permanently unenterable.
    """
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    pid = rec.get("pid")
    if not isinstance(pid, int) or not _alive(pid):
        return None
    return rec


def assert_free(run_root: Path | str, *, action: str = "overwrite") -> None:
    """Raise :class:`RunLocked` if a live process holds this run.

    Call this BEFORE destroying or recreating a run directory.  ``--force`` must mean
    "overwrite a DEAD run", never "evict a running one": ``create_workspace(force=True)``
    does ``shutil.rmtree`` on the run root, which deletes the lock file itself, so without
    this check a forced run silently wipes a live holder's workspace out from under it and
    then takes a fresh lock with no error.  Measured 2026-08-25 on
    tsr_scn_boat_workshop_v2: two independent ``3dcv make`` on one slug, 33 seconds apart.

    This narrows the window rather than closing it completely — two ``--force`` calls that
    interleave between this check and the wipe can still both proceed.  Closing that
    properly needs a lock held outside the directory being deleted; the check here removes
    the case that actually happens, which is a force against a run someone is already
    using.
    """
    if (held := _holder(_lock_path(run_root))) is not None:
        raise RunLocked(_held_message(run_root, held, action=action))


def _held_message(run_root: Path | str, held: dict, *, action: str) -> str:
    started = time.strftime("%H:%M:%S", time.localtime(held.get("started", 0)))
    name = Path(run_root).name
    return (
        f"refusing to {action} run {name}: it is being run right now by pid {held['pid']} "
        f"(started {started}: {held.get('what') or '3dcv'}).  Two processes on one run "
        f"corrupt each other's state.  Look at it with `3dcv status {name}`, or stop that "
        f"ONE process with `kill {held['pid']}` — never `pkill -f 3dcv`, which kills every "
        f"other run on this machine too."
    )


@contextmanager
def run_lock(run_root: Path | str, *, what: str = "") -> Iterator[None]:
    """Hold the run directory for this process, or raise :class:`RunLocked`.

    Takes over a lock whose PID is gone (a killed or crashed run must stay resumable).
    Releasing is best-effort and never masks the body's own exception.
    """
    path = _lock_path(run_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if (held := _holder(path)) is not None:
        raise RunLocked(_held_message(run_root, held, action="enter"))
    rec = {"pid": os.getpid(), "started": time.time(), "what": what}
    tmp = path.with_suffix(".lock.tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, path)  # atomic publish; last writer in a race still leaves ONE holder
    try:
        yield
    finally:
        try:
            if (cur := _holder(path)) is not None and cur.get("pid") == os.getpid():
                path.unlink(missing_ok=True)
        except OSError:
            pass
