"""Activity-aware subprocess runner for headless agent CLIs.

Three clocks govern a run:

* ``soft_timeout_s`` — the nominal budget.  After it elapses the process may
  keep going *only while it is visibly working*.
* ``idle_grace_s`` — past the soft timeout, if nothing happened (no stdout /
  stderr line, no file mtime change under ``activity_dirs``) for this long,
  the process group is killed (``killed_reason="idle"``).
* ``hard_timeout_s`` — absolute ceiling; kill regardless (``"hard_timeout"``).

The child is started in its own session so ``os.killpg`` takes the whole
tree (node → chrome, blender, mcp servers ...).  The process lifecycle itself
(pipes, pump threads, stdin writer, kill-on-exception, drain, reap) lives in
:class:`codeverse.proc.ManagedProcess`; this module owns only the clocks.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from codeverse.proc import ManagedProcess

_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".gemini", ".gemini_home"}


@dataclass
class CompletedProc:
    """Outcome of :func:`run_with_watchdog`.

    ``stdout``/``stderr`` are bounded to ``codeverse.proc.STREAM_BUDGET_BYTES`` per
    stream (head + tail kept, truncation marker in between); the ``*_lines`` lists
    are derived from those bounded texts — streaming consumers that must see every
    line unconditionally use ``on_line``.
    """

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

    Since 2026-08-27 the process lifecycle lives in
    :class:`codeverse.proc.ManagedProcess`; this function keeps only the clocks.
    Fixed by that move: a KeyboardInterrupt anywhere in the poll loop kills the
    group before propagating (it used to orphan the whole node → chrome tree —
    SIGINT never reaches a ``start_new_session`` child); stdin is written from a
    helper thread, so a child that never reads a >64 KiB prompt (codex pipes
    prompts >100 kB via stdin) can no longer wedge the main thread before the
    clocks start; captured output is bounded per stream (head+tail + marker).
    """
    if hard_timeout_s is None:
        hard_timeout_s = max(soft_timeout_s * 1.5, soft_timeout_s + 600.0)
    cwd = Path(cwd)
    dirs = list(activity_dirs) if activity_dirs is not None else [cwd / "src"]
    tracker = ActivityTracker()
    t0 = time.monotonic()
    _reject_control_chars(cmd, cwd)

    def observe(stream: str, line: str) -> None:
        tracker.touch()
        if on_line is not None:
            on_line(stream, line)  # ManagedProcess suppresses observer exceptions

    killed = ""
    with ManagedProcess(cmd, cwd=cwd, env=env, stdin_text=stdin, on_line=observe) as mp:
        last_mtime = _latest_mtime(dirs)
        next_scan = t0 + scan_s
        while mp.poll() is None:
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
                mp.terminate()  # TERM → 5 s grace → KILL: the kill_process_group contract
                break
            time.sleep(poll_s)
    rc = mp.returncode if mp.returncode is not None else -9
    out_text, err_text = mp.stdout_text, mp.stderr_text
    return CompletedProc(
        rc=rc, stdout=out_text, stderr=err_text, duration_s=time.monotonic() - t0,
        timed_out=bool(killed), killed_reason=killed,
        stdout_lines=out_text.splitlines(), stderr_lines=err_text.splitlines(),
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
