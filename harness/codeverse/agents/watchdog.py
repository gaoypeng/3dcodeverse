"""Activity-aware subprocess runner for headless agent CLIs.

Three clocks govern a run:

* ``soft_timeout_s`` — the nominal budget.  After it elapses the process may
  keep going *only while it is visibly working*.
* ``idle_grace_s`` — past the soft timeout, if nothing happened (no stdout /
  stderr line, no file mtime change under ``activity_dirs``) for this long,
  the process group is killed (``killed_reason="idle"``).
* ``hard_timeout_s`` — absolute ceiling; kill regardless (``"hard_timeout"``).

The child is started in its own session so ``os.killpg`` takes the whole
tree (node → chrome, blender, mcp servers ...).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".gemini", ".gemini_home"}


@dataclass
class CompletedProc:
    """Outcome of :func:`run_with_watchdog`."""

    rc: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    killed_reason: str = ""  # "" | idle | hard_timeout
    stdout_lines: list[str] = field(default_factory=list, repr=False)
    stderr_lines: list[str] = field(default_factory=list, repr=False)


class ActivityTracker:
    """Thread-safe 'last time something happened' clock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = time.monotonic()

    def touch(self) -> None:
        with self._lock:
            self._last = time.monotonic()

    def idle_for(self) -> float:
        with self._lock:
            return time.monotonic() - self._last


def _latest_mtime(dirs: Iterable[Path]) -> float:
    latest = 0.0
    for d in dirs:
        if not d.is_dir():
            continue
        for root, subdirs, files in os.walk(d):
            subdirs[:] = [s for s in subdirs if s not in _SKIP_DIRS]
            for f in files:
                try:
                    latest = max(latest, os.stat(os.path.join(root, f)).st_mtime)
                except OSError:
                    continue
    return latest


def _pump(stream, sink: list[str], tracker: ActivityTracker, on_line: Callable[[str, str], None] | None, tag: str) -> None:
    for raw in iter(stream.readline, b""):
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        sink.append(line)
        tracker.touch()
        if on_line is not None:
            with contextlib.suppress(Exception):  # observer bugs must not kill the pump
                on_line(tag, line)
    stream.close()


def kill_process_group(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """SIGTERM the child's process group, then SIGKILL after ``grace_s``."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


def run_with_watchdog(
    cmd: Sequence[str],
    *,
    cwd: Path | str,
    env: Mapping[str, str] | None,
    soft_timeout_s: float,
    idle_grace_s: float = 300.0,
    hard_timeout_s: float | None = None,
    on_line: Callable[[str, str], None] | None = None,
    stdin: str | None = None,
    activity_dirs: Sequence[Path] | None = None,
    poll_s: float = 1.0,
    scan_s: float = 5.0,
) -> CompletedProc:
    """Run ``cmd`` streaming output; kill the process group on idle/hard timeout.

    ``on_line(stream, line)`` is called for every line (stream is ``"stdout"``
    or ``"stderr"``).  ``activity_dirs`` (default ``cwd/src``) are polled for
    mtime changes (every ``scan_s``) that also count as activity.
    """
    if hard_timeout_s is None:
        hard_timeout_s = max(soft_timeout_s * 1.5, soft_timeout_s + 600.0)
    cwd = Path(cwd)
    dirs = list(activity_dirs) if activity_dirs is not None else [cwd / "src"]
    tracker = ActivityTracker()
    t0 = time.monotonic()
    _reject_control_chars(cmd, cwd)
    proc = subprocess.Popen(
        list(cmd), cwd=str(cwd), env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    out: list[str] = []
    err: list[str] = []
    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, out, tracker, on_line, "stdout"), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err, tracker, on_line, "stderr"), daemon=True),
    ]
    for t in threads:
        t.start()
    if stdin is not None:
        try:
            proc.stdin.write(stdin.encode("utf-8"))  # type: ignore[union-attr]
            proc.stdin.close()  # type: ignore[union-attr]
        except BrokenPipeError:
            pass

    killed = ""
    last_mtime = _latest_mtime(dirs)
    next_scan = t0 + scan_s
    while proc.poll() is None:
        now = time.monotonic()
        elapsed = now - t0
        if now >= next_scan:
            m = _latest_mtime(dirs)
            if m > last_mtime:
                last_mtime = m
                tracker.touch()
            next_scan = now + scan_s
        if elapsed >= hard_timeout_s:
            killed = "hard_timeout"
        elif elapsed >= soft_timeout_s and tracker.idle_for() >= idle_grace_s:
            killed = "idle"
        if killed:
            kill_process_group(proc)
            break
        time.sleep(poll_s)
    for t in threads:
        t.join(timeout=10)
    rc = proc.returncode if proc.returncode is not None else -9
    return CompletedProc(
        rc=rc, stdout="\n".join(out), stderr="\n".join(err), duration_s=time.monotonic() - t0,
        timed_out=bool(killed), killed_reason=killed, stdout_lines=out, stderr_lines=err,
    )


def _reject_control_chars(cmd: Sequence[str], cwd: object) -> None:
    """Fail with an argv position and an excerpt instead of a bare "embedded null byte".

    ``subprocess.Popen`` raises ``ValueError: embedded null byte`` naming nothing at all —
    not the argument, not the offset, not the value.  On 2026-08-24 that cost an hour to
    trace back to five ``\\u0000`` escapes a planner had written into plan.json four stages
    earlier (see ``schema_utils.strip_control_chars``, which is the actual cure).  This is
    the backstop: if one ever leaks again, the error says where.
    """
    for i, arg in enumerate(cmd):
        text = str(arg)
        j = text.find("\x00")
        if j != -1:
            raise ValueError(
                f"argv[{i}] contains a NUL at offset {j} of {len(text)} — a model almost "
                f"certainly emitted \\u0000 in structured output and it was not sanitised "
                f"(cwd={cwd}).  Context: {text[max(0, j - 60):j + 40]!r}"
            )
