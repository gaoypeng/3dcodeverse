"""One writer per run directory.

Two ``3dcv`` on one run both write ``run_state.json``, both snapshot ``src/`` into the
same git repo and both spend the budget (measured 2026-08-25: two ``3dcv resume`` ran
four minutes side by side on tsr_scn_temple_night).

:func:`exclusive` is the ONE authority: an ``fcntl.flock`` on
``<runs>/.locks/<slug>.lock``, beside the run dirs because ``--force`` ``rmtree``s the
run root and a lock deleted mid-hold is no lock.  The kernel drops it when the holder
dies, so a crash never wedges a run.  Its ``{pid, started, what}`` record (also read by
:func:`holder_of`, without locking) is what a refusal prints, so a human can ``kill``
THAT pid instead of ``pkill -f 3dcv`` — which took out thirteen unrelated runs.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: directory (beside the run dirs, never inside one) holding the flock files
LOCKS_DIR = ".locks"


class RunLocked(RuntimeError):
    """Another live holder — process or thread — is already running this run."""


def _held_message(run_root: Path | str, held: dict, *, action: str) -> str:
    started = time.strftime("%H:%M:%S", time.localtime(held.get("started", 0)))
    name = Path(run_root).name
    return (
        f"refusing to {action} run {name}: it is being run right now by pid {held.get('pid', '?')} "
        f"(started {started}: {held.get('what') or '3dcv'}).  Two processes on one run "
        f"corrupt each other's state.  Look at it with `3dcv status {name}`, or stop that "
        f"ONE process with `kill {held.get('pid', '?')}` — never `pkill -f 3dcv`, which kills every "
        f"other run on this machine too."
    )


def flock_path(run_root: Path | str) -> Path:
    """The flock file for this run: ``<runs>/.locks/<slug>.lock``."""
    root = Path(run_root).resolve()
    return root.parent / LOCKS_DIR / f"{root.name}.lock"


_HELD: dict[str, list] = {}  # flock path -> [fd, owning thread ident] held by THIS process
_HELD_GUARD = threading.Lock()


def _record_of(fd: int) -> dict | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096).decode("utf-8", "replace").strip()
        rec = json.loads(raw) if raw else None
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _write_record(fd: int, rec: dict) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps(rec).encode())
    os.fsync(fd)


def holder_of(run_root: Path | str) -> dict | None:
    """The ``{pid, started, what}`` of whoever holds this run — without taking it.

    ``None`` when nobody does, including after a SIGKILL: the kernel dropped the flock but
    the record is still in the file, so a probe (shared, non-blocking, released at once)
    tells the difference and ``3dcv status`` never names a pid that is already gone."""
    try:
        fd = os.open(flock_path(run_root), os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return _record_of(fd)  # refused: somebody is holding it right now
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


@contextmanager
def exclusive(run_root: Path | str, *, what: str = "", action: str = "enter") -> Iterator[None]:
    """THE mutex for one run: hold it around every mutation of the run directory.

    Raises :class:`RunLocked`, naming the holder, when anyone else has it — another
    process, or another THREAD of this one (the bench drivers run runs in a pool).  Only
    the SAME thread re-enters, so the CLI can take it at the mutation boundary and the
    code under it can take it again."""
    key, me = str(flock_path(run_root)), threading.get_ident()
    with _HELD_GUARD:
        entry = _HELD.get(key)
    if entry is not None:
        if entry[1] != me:
            raise RunLocked(_held_message(run_root, holder_of(run_root) or {}, action=action))
        yield  # the SAME thread re-entering: the outermost `with` owns the release
        return
    path = Path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        held = _record_of(fd) or {}
        os.close(fd)
        raise RunLocked(_held_message(run_root, held, action=action)) from None
    except BaseException:
        os.close(fd)
        raise
    try:  # inside the try that owns the fd: a failed write must not leak an unreleasable lock
        _write_record(fd, {"pid": os.getpid(), "started": time.time(), "what": what})
        with _HELD_GUARD:
            _HELD[key] = [fd, me]
        yield
    finally:
        with _HELD_GUARD:
            _HELD.pop(key, None)
        try:
            os.ftruncate(fd, 0)  # the file stays (unlinking it races a waiter's open)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)
