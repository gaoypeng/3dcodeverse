"""Shared plumbing for subprocess-based CodingAgent backends.

* :func:`hardened_env` — child environment without unrelated secrets.
* :func:`begin_session` / :func:`finish_session` — trajectory dir (a retry of
  the same label+round gets ``<label>.a2_rNN`` instead of overwriting attempt 1),
  git snapshot before/after, ``files_changed`` from git *attributed to this
  session* (see :func:`attribute_changes`), ``result.json``.
* :func:`deliver_prompt` — argv prompt or "read the prompt file" stub.
* :func:`invoke` / :func:`watchdog_error` — one CLI process under the watchdog,
  recorded in the trajectory (``invoke`` line, every output line, stdout/stderr
  captures); ``IDLE_GRACE_S`` is the shared idle kill threshold.
* :func:`estimate_cost_safe` — lazy bridge to ``codeverse.models.pricing``.
* :func:`is_transient_failure` — 429 / 503 / empty-response detection.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.agents.transcript import Trajectory
from codeverse.agents.watchdog import CompletedProc, run_with_watchdog
from codeverse.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse.contracts.common import Usage
from codeverse.proc import scrub_secrets
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

#: argv prompts above this many bytes are written to a file instead.
MAX_ARGV_PROMPT_BYTES = 100_000

def is_secret_env(name: str) -> bool:
    """True for env vars that look like credentials (stripped from agent children).

    One owner for the patterns: :mod:`codeverse.proc` (a stdlib-only leaf every layer
    may import), which also scrubs the generated-code subprocesses."""
    return name not in scrub_secrets({name: ""})


def hardened_env(ws: Workspace, job: AgentJob, *, keep: set[str] | None = None) -> dict[str, str]:
    """Copy ``os.environ`` minus secrets, plus recursion guard + git ceiling + ``job.env``.

    ``keep`` names secrets that must survive (e.g. the CLI's own auth var).
    """
    keep = keep or set()
    env = {k: v for k, v in os.environ.items() if k in keep or not is_secret_env(k)}
    env["CV3D_AGENT_CONTEXT"] = "1"
    env["GIT_CEILING_DIRECTORIES"] = str(ws.root.parent)
    env["PYTHONUNBUFFERED"] = "1"
    env.update(job.env)
    return env


def default_mcp_command(ws: Workspace, *, language: str = "", track: str = "", round_index: int | None = None) -> list[str]:
    """The stdio MCP server command exposing the spatial tool registry for ``ws``."""
    cmd = [sys.executable, "-m", "codeverse.spatial.mcp_server", "--workspace", str(ws.root)]
    if language:
        cmd += ["--language", language]
    if track:
        cmd += ["--track", track]
    if round_index is not None:
        cmd += ["--round", str(round_index)]
    return cmd


def mcp_command_for(ws: Workspace, job: AgentJob) -> list[str]:
    """The 3dcv MCP command for this job, from the TYPED job: ``job.mcp_command`` > default.

    It deliberately does NOT read the workspace's ``.mcp.json``.  That file lives where
    the agent works and every CLI backend runs unsandboxed inside it, so an agent that
    rewrote it in round 0 chose what executable the NEXT round's codex/claude launched —
    with provider credentials in the environment, and (for codex) outside the
    ``--sandbox workspace-write`` its own shell obeys.  The command is rebuilt from the
    job every session instead; nothing on disk can redirect it (audit 2026-08-27)."""
    if job.mcp_command:
        return list(job.mcp_command)
    return default_mcp_command(ws, language=job.language, track=job.track, round_index=job.round)


# --------------------------------------------------------------------------- session
#: paths the harness owns; never attributed to an agent session even when git sees them change
HARNESS_OWNED_DIRS = ("artifacts", "trajectories", "stages", "rounds", "_cand", "_assets", ".3dcv", ".gemini", ".claude", ".git")
HARNESS_OWNED_FILES = frozenset({"events.jsonl", "run_state.json", "record.json", "AGENTS.md", "GEMINI.md", "CLAUDE.md",
                                 ".mcp.json", ".geminiignore", ".aiexclude", ".gitignore"})


@dataclass
class _LiveSession:
    """Registry entry: lets concurrent sessions in ONE workspace (scene fan-out) attribute files."""

    label: str
    hints: frozenset[str]
    t_start: float
    t_end: float | None = None


_LIVE: dict[str, list[_LiveSession]] = {}
_LIVE_LOCK = threading.Lock()


def _register(ws: Workspace, label: str, hints: frozenset[str]) -> _LiveSession:
    entry = _LiveSession(label=label, hints=hints, t_start=time.monotonic())
    with _LIVE_LOCK:
        _LIVE.setdefault(str(ws.root), []).append(entry)
    return entry


def _sibling_hints(ws: Workspace, me: _LiveSession) -> frozenset[str]:
    """Files claimed (``job.files_hint``) by OTHER sessions that overlapped ``me`` in time."""
    me.t_end = time.monotonic()
    with _LIVE_LOCK:
        entries = _LIVE.get(str(ws.root), [])
        claimed: set[str] = set()
        for e in entries:
            if e is me:
                continue
            overlaps = e.t_start <= me.t_end and (e.t_end is None or e.t_end >= me.t_start)
            if overlaps:
                claimed |= e.hints
        oldest_active = min((e.t_start for e in entries if e.t_end is None), default=me.t_end)
        entries[:] = [e for e in entries if e.t_end is None or e.t_end >= oldest_active]
    return frozenset(claimed - me.hints)


def _hinted(path: str, hints: frozenset[str]) -> bool:
    return any(path == h or path.startswith(h.rstrip("/") + "/") for h in hints)


def attribute_changes(
    files: list[FileChange],
    *,
    write_roots: list[str],
    own_hints: frozenset[str] = frozenset(),
    sibling_hints: frozenset[str] = frozenset(),
) -> list[FileChange]:
    """The subset of a whole-worktree git diff that belongs to ONE session: inside its
    ``write_roots``, not harness-owned, and not a file another concurrent session
    declared as its target (``job.files_hint``) unless this session declared it too."""
    roots = tuple(r.strip("/") for r in write_roots if r.strip("/"))
    out: list[FileChange] = []
    for f in files:
        parts = Path(f.path).parts
        if not parts or parts[0] in HARNESS_OWNED_DIRS or f.path in HARNESS_OWNED_FILES:
            continue
        if roots and not any(f.path == r or f.path.startswith(r + "/") for r in roots):
            continue
        if sibling_hints and _hinted(f.path, sibling_hints) and not _hinted(f.path, own_hints):
            continue
        out.append(f)
    return out


@dataclass
class Session:
    """State for one CLI agent run (created by :func:`begin_session`)."""

    ws: Workspace
    job: AgentJob
    kind: str
    label: str
    round_index: int
    traj: Trajectory
    head_before: str
    t0: float = field(default_factory=time.monotonic)
    notes: list[str] = field(default_factory=list)
    attempt: int = 1
    files_hint: frozenset[str] = frozenset()
    live: _LiveSession | None = None
    ws_lock: threading.RLock | None = None


# --------------------------------------------------------------------------- serialisation
#: agent kinds whose sessions are EXCLUSIVE per workspace.  CLI agents have no
#: write-time enforcement (FileTools guards only the in-process api-agent), and both
#: session snapshots run ``git add -A`` — a late-starting session's ``pre:`` commit
#: absorbs a sibling's in-flight writes, which makes concurrent provenance (and any
#: per-path rollback, see :func:`_enforce_scope`) unfixable after the fact.  So CLI
#: sessions on one workspace are serialised; the main generator (api-agent) keeps its
#: full parallelism because its writes are scope-checked as they happen.
EXCLUSIVE_KINDS = frozenset({"claude-code", "codex", "gemini-cli", "agy"})

_WS_SESSION_LOCKS: dict[str, threading.RLock] = {}
_WS_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(ws: Workspace) -> threading.RLock:
    """One re-entrant lock per workspace root.  Re-entrant so a thread that begins a
    session it never finishes (argv-building tests do) cannot deadlock itself; real
    sessions begin, finish and release on their own worker thread."""
    with _WS_SESSION_LOCKS_GUARD:
        return _WS_SESSION_LOCKS.setdefault(str(ws.root), threading.RLock())


def release_session(s: Session) -> None:
    """Release ``s``'s workspace lock (idempotent).  ``finish_session`` always calls it;
    the CLI backends also call it in a ``finally`` so a session that crashes between
    begin and finish cannot starve every later session on the same workspace."""
    lock, s.ws_lock = s.ws_lock, None
    if lock is not None:
        lock.release()


def _session_label(ws: Workspace, label: str, round_index: int) -> tuple[str, int]:
    """``label`` for attempt 1; ``<label>.a<n>`` when ``trajectories/<label>_rNN`` already
    holds a finished attempt (result.json) — a silent-bail retry must not overwrite it."""
    n, cand = 1, label
    while (ws.trajectory_dir(cand, round_index) / "result.json").exists():
        n += 1
        cand = f"{label}.a{n}"
    return cand, n


def begin_session(job: AgentJob, kind: str) -> Session:
    """Create the trajectory dir, write prompt.md and snapshot src/ (``pre:<label>``).

    For :data:`EXCLUSIVE_KINDS` the per-workspace session lock is taken BEFORE the
    ``pre:`` commit and held until ``finish_session`` releases it — see the constant's
    comment for why concurrent CLI sessions in one workspace are unfixable."""
    ws = Workspace(job.workspace)
    if not ws.root.is_dir():
        raise FileNotFoundError(f"workspace does not exist: {ws.root}")
    if not (ws.root / ".git").exists():
        ws.create()
    round_index = job.round
    label, attempt = _session_label(ws, job.label or kind, round_index)
    traj = Trajectory(ws.trajectory_dir(label, round_index))
    traj.write_prompt(job.prompt, job.system_append)
    hints = frozenset(h for h in (str(x).strip() for x in job.files_hint) if h)
    lock = _session_lock(ws) if kind in EXCLUSIVE_KINDS else None
    if lock is not None:
        lock.acquire()
    try:
        live = _register(ws, label, hints)
        head_before = ws.commit(f"pre:{label}")
    except BaseException:
        if lock is not None:
            lock.release()
        raise
    return Session(ws=ws, job=job, kind=kind, label=label, round_index=round_index,
                   traj=traj, head_before=head_before, attempt=attempt, files_hint=hints,
                   live=live, ws_lock=lock)


def _enforce_scope(s: Session) -> list[str]:
    """Restore an ``edit_only`` session's out-of-scope writes; returns the restored paths.

    Post-hoc, because the CLI backends have no write-time file gate.  A PRE-EXISTING
    file that changed and is neither in ``files_hint`` nor always-writable (the entry
    file, when the task owns it) is out of scope; new files stay allowed — the exact
    contract ``FileTools`` enforces for the api-agent.  Only safe because sessions on
    one workspace are serialised (:data:`EXCLUSIVE_KINDS`): ``head_before`` holds no
    sibling's in-flight work, so restoring to it cannot destroy anyone else's files."""
    if not (s.job.edit_only and s.files_hint):
        return []
    allowed = s.files_hint | frozenset(
        h for h in (str(x).strip() for x in s.job.always_writable) if h)
    out: list[str] = []
    for f in s.ws.changed_files(s.head_before):
        parts = Path(f.path).parts
        if not parts or parts[0] in HARNESS_OWNED_DIRS or f.path in HARNESS_OWNED_FILES:
            continue
        if f.status == "added" or _hinted(f.path, allowed):
            continue
        out.append(f.path)
    if out:
        s.ws.restore_paths(s.head_before, out)
    return out


def finish_session(
    s: Session,
    *,
    ok: bool,
    exit_reason: str,
    text: str,
    usage: Usage,
    tool_calls: int = 0,
    errors: list[str] | None = None,
    **extra: Any,
) -> AgentResult:
    """Commit the agent's work, compute ``files_changed`` via git (attributed to this
    session — see :func:`attribute_changes`), write result.json.

    ``edit_only`` jobs are scope-checked here for the CLI backends (they have no
    write-time gate): out-of-scope changes to pre-existing files are restored to this
    session's own ``pre:`` commit and the session is failed (:func:`_enforce_scope`)."""
    try:
        errors = list(errors or [])
        restored = _enforce_scope(s)
        if restored:
            ok = False
            msg = ("out-of-scope writes restored: " + ", ".join(sorted(restored))
                   + f" — this edit_only session may only change {sorted(s.files_hint)} "
                   "(new files, and the entry file when the task owns it, stay allowed)")
            errors.append(msg)
            s.notes.append(msg)
        s.ws.commit(f"agent:{s.label}")
        siblings = _sibling_hints(s.ws, s.live) if s.live is not None else frozenset()
        files = attribute_changes(s.ws.changed_files(s.head_before), write_roots=s.job.write_roots,
                                  own_hints=s.files_hint, sibling_hints=siblings)
        res = AgentResult(
            ok=ok, exit_reason=exit_reason, text=text, files_changed=files,
            transcript_path=str(s.traj.transcript_path if s.traj.transcript_path.exists() else s.traj.dir),
            usage=usage, duration_s=round(time.monotonic() - s.t0, 3), tool_calls=tool_calls,
            errors=errors,
        )
        s.traj.write_result(res, kind=s.kind, label=s.label, round=s.round_index, attempt=s.attempt, job_label=s.job.label,
                            head_before=s.head_before, head_after=s.ws.head(), notes=s.notes, **extra)
        return res
    finally:
        release_session(s)


def failed(s: Session, reason: str, message: str, usage: Usage | None = None) -> AgentResult:
    """Shortcut for an ``ok=False`` result with one error line."""
    return finish_session(s, ok=False, exit_reason=reason, text="", usage=usage or Usage(), errors=[message])


# --------------------------------------------------------------------------- prompts
def deliver_prompt(s: Session, prompt: str, *, max_bytes: int = MAX_ARGV_PROMPT_BYTES) -> str:
    """Return the text to pass on argv: the prompt itself, or a stub pointing at a file."""
    if len(prompt.encode("utf-8")) <= max_bytes:
        return prompt
    p = s.traj.write_text("task_prompt.md", prompt)
    s.notes.append(f"prompt ({len(prompt)} chars) delivered via file {p}")
    rel = os.path.relpath(p, s.ws.root)
    return (
        f"Your full task is in the file `{rel}` (relative to the current working directory). "
        "Read that file completely with your file-reading tool FIRST, then carry out every "
        "instruction in it. Do not ask for confirmation."
    )


# --------------------------------------------------------------------------- invoke
#: seconds without output or workspace activity before the watchdog kills a CLI agent;
#: read at call time by :func:`invoke` so a test can monkeypatch it here.
IDLE_GRACE_S = 300.0


def invoke(
    s: Session,
    argv: list[str],
    env: Mapping[str, str],
    *,
    prompt: str,
    stdout_name: str = "stdout.json",
    on_stdout: Callable[[str], None] | None = None,
    stdin: str | None = None,
    attempt: int = 1,
    idle_grace_s: float | None = None,
    **invoke_extra: Any,
) -> CompletedProc:
    """Run one CLI agent process under the watchdog and record it in the trajectory.

    Replaces the block ``claude_code.ClaudeCodeAgent.run``, ``codex.CodexAgent.run``,
    ``antigravity.AntigravityAgent.run`` and ``gemini_cli.GeminiCliAgent._invoke`` each
    carried verbatim: the ``invoke`` transcript line with the prompt masked out of argv,
    ``run_with_watchdog`` with identical kwargs (workspace cwd, the job's soft timeout,
    ``IDLE_GRACE_S``, every output line into the transcript, ``src/`` + ``public/`` as
    activity dirs) and the stdout/stderr captures next to the transcript.

    ``stdout_name`` sets the capture's name (codex streams JSONL → ``stdout.jsonl``);
    ``on_stdout`` also receives each stdout line (codex folds events live); ``stdin``
    feeds the prompt through stdin instead of argv; ``attempt`` > 1 writes
    ``stdout.<n>.json`` / ``stderr.<n>.log`` so a retry keeps attempt 1's captures.
    The ``invoke`` line records ``attempt`` and whether stdin was used, plus
    ``invoke_extra`` (gemini-cli adds ``model`` and ``key_tail``).
    """
    masked = [a if a != prompt else f"<prompt {len(prompt)} chars>" for a in argv]
    s.traj.append("invoke", argv=masked, attempt=attempt, stdin=stdin is not None, **invoke_extra)

    def on_line(stream: str, line: str) -> None:
        s.traj.append("line", stream=stream, text=line[:4000])
        if on_stdout is not None and stream == "stdout":
            on_stdout(line)

    proc = run_with_watchdog(
        argv, cwd=s.ws.root, env=env, soft_timeout_s=s.job.timeout_s,
        idle_grace_s=IDLE_GRACE_S if idle_grace_s is None else idle_grace_s,
        on_line=on_line, stdin=stdin, activity_dirs=[s.ws.src, s.ws.public],
    )
    suffix = "" if attempt == 1 else f".{attempt}"
    out = Path(stdout_name)
    s.traj.write_text(f"{out.stem}{suffix}{out.suffix}", proc.stdout)
    s.traj.write_text(f"stderr{suffix}.log", proc.stderr)
    return proc


def watchdog_error(proc: CompletedProc) -> str:
    """The error line every CLI backend records when the watchdog killed its process."""
    return f"killed by watchdog ({proc.killed_reason}) after {proc.duration_s:.0f}s"


# --------------------------------------------------------------------------- failures
_TRANSIENT_RE = re.compile(
    r"(\b429\b|RESOURCE_EXHAUSTED|rate.?limit|quota|\b503\b|UNAVAILABLE|overloaded|"
    r"\b502\b|\b500\b|INTERNAL|empty response|Invalid stream|ECONNRESET|ETIMEDOUT|socket hang up)",
    re.IGNORECASE,
)
_QUOTA_RE = re.compile(r"(\b429\b|RESOURCE_EXHAUSTED|quota|rate.?limit)", re.IGNORECASE)


def is_transient_failure(*texts: str) -> bool:
    """Heuristic: did the CLI die of a rate-limit / 5xx / empty-stream glitch?"""
    return any(_TRANSIENT_RE.search(t or "") for t in texts)


def is_quota_failure(*texts: str) -> bool:
    return any(_QUOTA_RE.search(t or "") for t in texts)


def tail(text: str, n: int = 2000) -> str:
    return text if len(text) <= n else text[-n:]


# --------------------------------------------------------------------------- pricing
def estimate_cost_safe(provider: str, model: str, usage: Usage) -> float:
    """``codeverse.models.pricing.estimate_cost`` when available; else 0.0 + warning."""
    try:
        from codeverse.models.pricing import estimate_cost
    except ImportError:
        log.warning("codeverse.models.pricing unavailable; cost for %s:%s recorded as 0", provider, model)
        return 0.0
    try:
        return float(estimate_cost(provider, model, usage))
    except Exception as e:  # unknown model in the price table must not sink the run
        log.warning("estimate_cost(%s, %s) failed: %s", provider, model, e)
        return 0.0


def exists_on_path(binary: str) -> bool:
    from shutil import which

    return bool(which(binary)) or Path(binary).is_file()
