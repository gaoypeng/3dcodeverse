"""Subprocess + atomic-JSON primitives shared across the harness.

One home for the run-a-child-process pattern (own process group, wall-clock
timeout, group kill, captured output), the tmp+rename JSON write, and the
tolerant JSON / JSONL readers every "best effort" side-car consumer needs.  Peer
of ``workspace.py``; stdlib-only — imports nothing from ``codeverse`` so wrappers,
spatial helpers and agents can all use it without layering back-edges.

``languages/_common.py`` currently carries the same helpers for its runtime
callers; it will become a re-export of this module.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
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
        out, err = _drain_after_kill(proc)
    return ProcResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out or "",
        stderr=err or "",
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


#: how long to keep draining the pipes after the group kill.  Matches
#: ``agents/watchdog.py``'s ``thread.join(timeout=10)``.
DRAIN_TIMEOUT_S = 10.0
_ABANDONED = "(output abandoned: a detached descendant still holds the pipe)"


def _drain_after_kill(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """Collect whatever the killed child wrote, WITHOUT waiting forever.

    ``kill_group`` killpg's only the child's own session, so a descendant that
    setsid'd or daemonised while inheriting stdout/stderr keeps those pipes open —
    and an unbounded ``communicate()`` then blocks until IT exits, which has no upper
    bound.  That silently voided ``timeout_s`` for every build/render path (blender,
    node, cadquery, urdf, gl_render, joints_export, doctor) that funnels through here.
    """
    try:
        return proc.communicate(timeout=DRAIN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        # The child itself is already SIGKILLed; close our ends of the pipes so the
        # escaped descendant cannot hold us, then reap the child (it is a zombie now).
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=DRAIN_TIMEOUT_S)
        return "", _ABANDONED


def kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL ``proc``'s whole process group (fall back to just the child)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


# ------------------------------------------------------------------ env scrubbing
#: COPY of the credential patterns in ``agents/cli_common.is_secret_env`` (its sibling —
#: keep the two in sync; tests/core/test_proc.py pins them together).  Duplicated because
#: proc.py imports nothing from ``codeverse``: the agents package sits above this layer.
_SECRET_EXACT: frozenset[str] = frozenset({
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENAI_ORG_ID", "HF_TOKEN", "HUGGINGFACE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "SUPABASE_SERVICE_ROLE_KEY", "CODEX_API_KEY",
})
_SECRET_SUFFIXES: tuple[str, ...] = (
    "_API_KEY", "_SECRET", "_SECRET_KEY", "_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_PRIVATE_KEY",
)


def scrub_secrets(env: dict[str, str]) -> dict[str, str]:
    """``env`` minus credential-shaped variables (exact names + ``*_API_KEY``-style suffixes).

    Model-generated code runs in subprocesses that inherit the harness environment
    (blender, the cadquery python, node) and nothing generated ever legitimately needs
    a credential — so the keys must not be there to leak into logs, artifacts or child
    processes.  Everything else (PATH, HOME, DISPLAY, MESA_*/GALLIUM_* render config...)
    passes through untouched.
    """
    return {
        k: v for k, v in env.items()
        if k not in _SECRET_EXACT and not k.endswith(_SECRET_SUFFIXES)
    }


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


# ------------------------------------------------------------------ tolerant reads
def read_json_or_none(path: Path | str, *, errors: str | None = None) -> dict[str, Any] | None:
    """Parse ``path`` as a JSON object; ``None`` when absent, unreadable, malformed
    or not a dict at the top level.

    The one tolerant reader behind every "use it if it is there" side-car: it
    replaced identical ``try: json.loads(read_text()) except (OSError, ValueError)``
    copies in ``flywheel/telemetry.read_json``, ``bench/complexity_report._read_json``,
    ``gallery/index._measurement_complexity`` / ``_spec_fields``,
    ``cli/main._best_round_of_record``, ``runlock._holder``,
    ``languages/threejs/lint._load_plan``, ``cost/reconstruct._read_json``,
    ``spatial/_render_common.read_json`` and ``cli/_judge.plan_summary_for`` — all
    the same algorithm, differing only in whether they also checked ``isinstance(dict)``
    (which every caller then relied on anyway).  Strict readers that must raise on
    a corrupt input (``Workspace.read_json``, ``judges/calibration._read_json``)
    deliberately do NOT use this.

    ``errors`` is ``read_text``'s decode policy (``"replace"`` for files another
    process may still be writing).  Reads UTF-8, the encoding :func:`write_json_atomic`
    writes.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors=errors))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def iter_jsonl_lines(path: Path | str) -> Iterator[tuple[int, str]]:
    """``(line_no, line)`` for every non-blank line of a JSONL file, 1-based.

    Missing file → nothing; decoded UTF-8 with ``errors="replace"`` so one bad byte
    (or a SIGKILL mid-append) costs at most that line.  Shared by
    :func:`read_jsonl_lenient` and by the model-validating readers that keep their own
    per-line parse (``cost/ledger.load_ledger``, ``bench/_jsonl.read_jsonl``).
    """
    p = Path(path)
    if not p.is_file():
        return
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        if line.strip():
            yield i + 1, line


def read_jsonl_lenient(
    path: Path | str, *, log: logging.Logger | None = None, dicts_only: bool = False
) -> list[Any]:
    """Every parseable JSON line of ``path``; unparseable lines are skipped (debug-logged
    to ``log`` when given), ``dicts_only`` also drops non-object rows.

    Replaced the same skip-bad-lines loop in ``events.EventLog.read``,
    ``cost/reconstruct._read_jsonl``, ``flywheel/trajectories.read_events``,
    ``cli/skills_cmd`` (skills report) and ``agents/transcript.read_transcript`` —
    a truncated last line must never lose the rest of the file (the contract
    ``cost.ledger.load_ledger`` has always had).
    """
    out: list[Any] = []
    for i, line in iter_jsonl_lines(path):
        try:
            row = json.loads(line)
        except ValueError as e:
            if log is not None:
                log.debug("%s:%d unreadable: %s", path, i, e)
            continue
        if dicts_only and not isinstance(row, dict):
            continue
        out.append(row)
    return out


def append_jsonl_line(path: Path | str, rec: Any, lock: threading.Lock) -> None:
    """Append ``rec`` as one JSON line under ``lock`` (``ensure_ascii=False``,
    ``default=str`` so paths / enums / datetimes serialise).

    Replaced the identical bodies of ``events.EventLog.emit`` and
    ``agents/transcript.Trajectory.append``.  ``cost/ledger.CostLedger.append`` is NOT
    a copy — it mkdirs, flushes and swallows OSError ("flushed on write") — and keeps
    its own.
    """
    line = json.dumps(rec, ensure_ascii=False, default=str)
    with lock, Path(path).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
