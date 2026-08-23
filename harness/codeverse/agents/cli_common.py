"""Shared plumbing for subprocess-based CodingAgent backends.

* :func:`hardened_env` — child environment without unrelated secrets.
* :func:`begin_session` / :func:`finish_session` — trajectory dir (a retry of
  the same label+round gets ``<label>.a2_rNN`` instead of overwriting attempt 1),
  git snapshot before/after, ``files_changed`` from git *attributed to this
  session* (see :func:`attribute_changes`), ``result.json``.
* :func:`deliver_prompt` — argv prompt or "read the prompt file" stub.
* :func:`estimate_cost_safe` — lazy bridge to ``codeverse.models.pricing``.
* :func:`is_transient_failure` — 429 / 503 / empty-response detection.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.agents.transcript import Trajectory
from codeverse.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse.contracts.common import Usage
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

#: argv prompts above this many bytes are written to a file instead.
MAX_ARGV_PROMPT_BYTES = 100_000

_SECRET_EXACT = {
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENAI_ORG_ID", "HF_TOKEN", "HUGGINGFACE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "SUPABASE_SERVICE_ROLE_KEY", "CODEX_API_KEY",
}
_SECRET_SUFFIXES = ("_API_KEY", "_SECRET", "_SECRET_KEY", "_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_PRIVATE_KEY")


def is_secret_env(name: str) -> bool:
    """True for env vars that look like credentials (stripped from agent children)."""
    return name in _SECRET_EXACT or name.endswith(_SECRET_SUFFIXES)


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
    """The 3dcv MCP command for this job: ``.mcp.json`` (materialised) > ``job.extra['mcp_command']`` > default."""
    mcp = ws.root / ".mcp.json"
    if mcp.is_file():
        try:
            srv = (json.loads(mcp.read_text()).get("mcpServers") or {}).get("3dcv")
        except json.JSONDecodeError:
            srv = None
        if srv and srv.get("command"):
            return [srv["command"], *srv.get("args", [])]
    if job.extra.get("mcp_command"):
        return list(job.extra["mcp_command"])
    return default_mcp_command(ws, language=str(job.extra.get("language", "")), track=str(job.extra.get("track", "")),
                               round_index=int(job.extra.get("round", 0) or 0))


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
    """Files claimed (``extra['files_hint']``) by OTHER sessions that overlapped ``me`` in time."""
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
    declared as its target (``job.extra['files_hint']``) unless this session declared it too."""
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


def _session_label(ws: Workspace, label: str, round_index: int) -> tuple[str, int]:
    """``label`` for attempt 1; ``<label>.a<n>`` when ``trajectories/<label>_rNN`` already
    holds a finished attempt (result.json) — a silent-bail retry must not overwrite it."""
    n, cand = 1, label
    while (ws.trajectory_dir(cand, round_index) / "result.json").exists():
        n += 1
        cand = f"{label}.a{n}"
    return cand, n


def begin_session(job: AgentJob, kind: str) -> Session:
    """Create the trajectory dir, write prompt.md and snapshot src/ (``pre:<label>``)."""
    ws = Workspace(job.workspace)
    if not ws.root.is_dir():
        raise FileNotFoundError(f"workspace does not exist: {ws.root}")
    if not (ws.root / ".git").exists():
        ws.create()
    round_index = int(job.extra.get("round", 0) or 0)
    label, attempt = _session_label(ws, job.label or kind, round_index)
    traj = Trajectory(ws.trajectory_dir(label, round_index))
    traj.write_prompt(job.prompt, job.system_append)
    hints = frozenset(str(h) for h in (job.extra.get("files_hint") or []) if str(h).strip())
    live = _register(ws, label, hints)
    head_before = ws.commit(f"pre:{label}")
    return Session(ws=ws, job=job, kind=kind, label=label, round_index=round_index,
                   traj=traj, head_before=head_before, attempt=attempt, files_hint=hints, live=live)


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
    session — see :func:`attribute_changes`), write result.json."""
    s.ws.commit(f"agent:{s.label}")
    siblings = _sibling_hints(s.ws, s.live) if s.live is not None else frozenset()
    files = attribute_changes(s.ws.changed_files(s.head_before), write_roots=s.job.write_roots,
                              own_hints=s.files_hint, sibling_hints=siblings)
    res = AgentResult(
        ok=ok, exit_reason=exit_reason, text=text, files_changed=files,
        transcript_path=str(s.traj.transcript_path if s.traj.transcript_path.exists() else s.traj.dir),
        usage=usage, duration_s=round(time.monotonic() - s.t0, 3), tool_calls=tool_calls,
        errors=list(errors or []),
    )
    s.traj.write_result(res, kind=s.kind, label=s.label, round=s.round_index, attempt=s.attempt, job_label=s.job.label,
                        head_before=s.head_before, head_after=s.ws.head(), notes=s.notes, **extra)
    return res


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
