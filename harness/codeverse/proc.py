"""Subprocess + atomic-JSON primitives shared across the harness.

One home for the run-a-child-process pattern (own process group, wall-clock
timeout, group kill, captured output) and the tmp+rename JSON write.  Peer of
``workspace.py``; stdlib-only — imports nothing from ``codeverse`` so wrappers,
spatial helpers and agents can all use it without layering back-edges.

``languages/_common.py`` currently carries the same helpers for its runtime
callers; it will become a re-export of this module.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcResult:
    """Outcome of one subprocess run."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


def run_subprocess(
    cmd: list[str],
    *,
    cwd: Path | str,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    preexec_fn: Callable[[], None] | None = None,
) -> ProcResult:
    """Run ``cmd`` in its own process group; kill the whole group on timeout.

    Never raises on non-zero exit — callers inspect :attr:`ProcResult.returncode`.
    ``preexec_fn`` runs in the child before exec (e.g. rlimits); the child is
    always started in a new session so :func:`kill_group` reaps grandchildren.
    """
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=preexec_fn,
    )
    timed_out = False
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_group(proc)
        out, err = proc.communicate()
    return ProcResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out or "",
        stderr=err or "",
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL ``proc``'s whole process group (fall back to just the child)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def tail(text: str, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    """Last ``max_lines`` lines of ``text``, capped at ``max_chars`` characters."""
    lines = text.splitlines()[-max_lines:]
    s = "\n".join(lines)
    return s[-max_chars:] if len(s) > max_chars else s


def unique_tmp(path: Path) -> Path:
    """A temp sibling of ``path`` unique to this process AND thread.

    A FIXED ``<name>.tmp`` is a race: two writers of the same destination both
    create it and the second ``replace()`` finds its source already renamed away,
    raising FileNotFoundError.  The harness fans work out over thread pools, so
    "one writer per destination" does not hold — see ``tracks/scene_asset_gen``,
    where the losing thread's asset gate was silently skipped.
    """
    return path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")


def write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """tmp + rename so readers never see a partial file, safe under concurrency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = unique_tmp(path)
    try:
        tmp.write_text(text, encoding=encoding)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # a failed write must not leave litter behind
        raise
    return path


def write_json_atomic(path: Path, data: Any) -> None:
    """tmp + rename so readers never see a partial file — including when several
    writers race for the same path.

    Delegates to :func:`write_text_atomic`, whose tmp name is private to this writer
    (pid + thread id).  One shared ``<path>.tmp`` was NOT atomic across writers: A could
    rename B's half-written tmp into place — readers then observed truncated or
    zero-byte JSON at the published path — and B's own ``replace()`` died with
    FileNotFoundError.  That is not hypothetical here: the scene track fans zone agents
    out over ONE workspace root and every agent's MCP ``build`` tool writes
    ``artifacts/build_last.json`` and ``artifacts/measurement.json`` through this
    function, and a clobbered ``run_state.json`` is an unresumable run
    (``RunState.load`` refuses to guess).  Last writer to rename still wins.
    """
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))
