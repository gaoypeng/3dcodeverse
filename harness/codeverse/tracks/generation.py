"""Code generation: one entry point, two strategies.

* **CodingAgent path** — ``agent_id`` is any id ``codeverse.agents`` knows
  (``gemini-cli:*``, ``claude-code:*``, ``codex:*``, ``agy:*``, ``api-agent:*``):
  the workspace is materialised once per run (AGENTS.md + MCP config), then
  ``agent.run(AgentJob)``.  Truth is on disk: ``ok`` means files changed.
* **Single-shot path** — ``agent_id = "single-shot:<provider>:<model>"``: one
  ``ChatRequest`` whose answer is a multi-file envelope (``SINGLE_SHOT_FORMAT``),
  parsed by ``parse_multifile`` and written under the allowed roots.  This is
  handled here (not in the agents registry) because it is not an agent.

Neither path commits; the round commits once after all (possibly parallel)
tasks finished.  Neither path builds; see ``tracks/repair.py``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Usage
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

SINGLE_SHOT_PREFIX = "single-shot:"
ALLOWED_ROOTS: tuple[str, ...] = ("src/", "public/")

SINGLE_SHOT_FORMAT = """OUTPUT FORMAT (exactly this, nothing else around it):
For EVERY file you create or fully rewrite, emit one block:

=== FILE: <relative path, e.g. src/parts/seat.js> ===
<complete file contents — the whole file, not a diff>
=== END FILE ===

Rules: paths are relative to the workspace root and must start with src/ (or public/);
emit the COMPLETE contents of each file (no "...rest unchanged"); no prose outside the blocks;
no markdown fences inside a block (plain code).  Files you do not emit are left untouched."""


class GenerationError(RuntimeError):
    """Raised when the generator cannot produce usable files (bad envelope, agent crash)."""


class MultiFileParseError(GenerationError):
    """The single-shot answer did not contain a parseable file envelope."""


class GenerationTask(BaseModel):
    """One unit of code-writing work (baseline, refine group, asset, zone, repair)."""

    label: str
    prompt: str
    system: str = Field(default="", description="system prompt (single-shot) / system_append (agent)")
    files_hint: list[str] = Field(default_factory=list, description="files this task should produce/edit")
    round: int = 0
    kind: str = "generate"
    images: list[ImagePart] = Field(default_factory=list)
    temperature: float = 0.6
    thinking: str = "medium"
    max_output_tokens: int = 32000
    timeout_s: int | None = None
    write_roots: list[str] = Field(default_factory=lambda: ["src", "public"])


class GenerationResult(BaseModel):
    ok: bool
    usage: Usage = Field(default_factory=Usage)
    files_changed: list[FileChange] = Field(default_factory=list)
    commit: str = ""
    notes: str = ""
    text: str = Field(default="", description="model/agent final text (truncated)")
    transcript_path: str = ""
    label: str = ""


# ----------------------------------------------------------------------------- envelope parsing
_BLOCK = re.compile(
    r"^[ \t]*===\s*FILE:\s*(?P<path>[^\n=]+?)\s*===[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?^[ \t]*===\s*END FILE\s*===[ \t]*$",
    re.S | re.M,
)
_FENCE = re.compile(r"```[a-zA-Z0-9_+\-]*[ \t]*(?:\r?\n)(?P<body>.*?)```", re.S)
_FENCE_PATH_HINT = re.compile(
    r"(?:^|\n)[^\n]*?(?P<path>(?:src|public)/[A-Za-z0-9_./\-]+\.[a-z]{1,5})[^\n]*\n[ \t]*```", re.S
)


def parse_multifile(text: str, *, expected_files: list[str] | None = None) -> dict[str, str]:
    """Parse ``SINGLE_SHOT_FORMAT`` → ``{path: content}``.

    Tolerant to: a fenced block wrapping a file body; an answer that is ONE fenced
    block when exactly one entry file is expected; fenced blocks preceded by a line
    naming the path.  Raises ``MultiFileParseError`` otherwise.
    """
    files: dict[str, str] = {}
    for m in _BLOCK.finditer(text):
        path = _clean_path(m.group("path"))
        files[path] = _strip_fence(m.group("body"))
    if files:
        return files
    fences = list(_FENCE.finditer(text))
    expected = [_clean_path(p) for p in (expected_files or [])]
    if fences:
        # fenced blocks preceded by a path mention
        for m in fences:
            probe = text[: m.start()][-400:].rstrip(" \t")
            probe += ("" if probe.endswith("\n") else "\n") + "```"
            hint = _FENCE_PATH_HINT.findall(probe)
            if hint:
                files[_clean_path(hint[-1])] = m.group("body").rstrip("\n")
        if files and (not expected or set(files) <= set(expected) or len(files) == len(fences)):
            return files
        if len(expected) == 1:
            largest = max(fences, key=lambda m: len(m.group("body")))
            return {expected[0]: largest.group("body").rstrip("\n")}
    if len(expected) == 1 and _looks_like_code(text):
        return {expected[0]: text.strip("\n")}
    raise MultiFileParseError(
        "no '=== FILE: <path> ===' blocks found" + (f"; expected {expected}" if expected else "")
    )


def _clean_path(p: str) -> str:
    p = p.strip().strip("`'\"").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _strip_fence(body: str) -> str:
    s = body.strip("\n")
    m = re.fullmatch(r"```[a-zA-Z0-9_+\-]*[ \t]*\n(.*?)\n?```", s, re.S)
    return (m.group(1) if m else body).rstrip("\n")


def _looks_like_code(text: str) -> bool:
    t = text.strip()
    return bool(t) and ("\n" in t) and not t.lower().startswith(("i ", "here", "sure", "sorry"))


def safe_relpath(path: str, allowed_roots: tuple[str, ...] = ALLOWED_ROOTS) -> str:
    """Validate a model-provided relative path: inside the workspace + allowed roots."""
    p = _clean_path(path)
    if not p or p.startswith("/") or ".." in Path(p).parts or re.match(r"^[A-Za-z]:", p):
        raise GenerationError(f"refusing to write outside the workspace: {path!r}")
    if not any(p.startswith(root) for root in allowed_roots):
        raise GenerationError(f"path {p!r} is outside the allowed roots {allowed_roots}")
    return p


def write_files(ws: Workspace, files: dict[str, str], *, allowed_roots: tuple[str, ...] = ALLOWED_ROOTS) -> list[FileChange]:
    """Write parsed files under the workspace; returns git-style FileChange rows."""
    changes: list[FileChange] = []
    for raw, content in files.items():
        rel = safe_relpath(raw, allowed_roots)
        dest = ws.root / rel
        existed = dest.exists()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content if content.endswith("\n") else content + "\n")
        changes.append(FileChange(path=rel, status="modified" if existed else "added",
                                  lines_added=content.count("\n") + 1))
    return changes


# ----------------------------------------------------------------------------- git helpers
_GIT_LOCKS: dict[str, threading.Lock] = {}
_GIT_LOCKS_GUARD = threading.Lock()


def workspace_lock(ws: Workspace) -> threading.Lock:
    """One lock per workspace root: git index operations from parallel tasks must serialise."""
    key = str(ws.root)
    with _GIT_LOCKS_GUARD:
        lock = _GIT_LOCKS.get(key)
        if lock is None:
            lock = _GIT_LOCKS[key] = threading.Lock()
        return lock


def changed_files_safe(ws: Workspace, since: str | None = None, *, retries: int = 6) -> list[FileChange]:
    """``ws.changed_files`` serialised per workspace and retried on transient git index locks."""
    with workspace_lock(ws):
        for attempt in range(retries):
            try:
                return ws.changed_files(since)
            except subprocess.CalledProcessError as e:
                if attempt == retries - 1:
                    raise
                log.warning("git diff failed (attempt %d): %s", attempt + 1, (e.stderr or "")[:200])
                time.sleep(0.2 * (attempt + 1))
    return []


# ----------------------------------------------------------------------------- strategies
def is_single_shot(agent_id: str) -> bool:
    return agent_id.startswith(SINGLE_SHOT_PREFIX)


def single_shot_model_id(agent_id: str) -> str:
    if not is_single_shot(agent_id):
        raise ValueError(f"not a single-shot id: {agent_id!r}")
    return agent_id[len(SINGLE_SHOT_PREFIX):]


def single_shot_format_text() -> str:
    """The canonical format doc (package K's ``system/singleshot_format.md`` when present)."""
    try:
        from codeverse.prompts import load_text

        return load_text("system/singleshot_format.md")
    except FileNotFoundError:
        return SINGLE_SHOT_FORMAT


def generate_files(
    ws: Workspace,
    *,
    model: Any,
    task: GenerationTask,
    budget: Any | None = None,
    events: Any | None = None,
    allowed_roots: tuple[str, ...] = ALLOWED_ROOTS,
) -> GenerationResult:
    """Single-shot API codegen: ChatRequest → envelope → files on disk (no commit)."""
    system = task.system + ("\n\n" if task.system else "") + single_shot_format_text()
    req = ChatRequest(
        messages=[ChatMessage.user(task.prompt, images=task.images or None)],
        system=system,
        temperature=task.temperature,
        thinking=task.thinking,  # type: ignore[arg-type]
        max_output_tokens=task.max_output_tokens,
        label=task.label,
    )
    resp = model.generate(req)
    usage = resp.usage
    if budget is not None:
        budget.charge(usage)
    traj = ws.trajectory_dir(task.label.replace("/", "_"), task.round)
    (traj / "prompt.md").write_text(f"# system\n{system}\n\n# user\n{task.prompt}\n")
    (traj / "response.md").write_text(resp.text or "")
    try:
        files = parse_multifile(resp.text or "", expected_files=task.files_hint or None)
    except MultiFileParseError as e:
        if events is not None:
            events.emit("generate.parse_failed", label=task.label, error=str(e))
        return GenerationResult(ok=False, usage=usage, notes=f"parse failed: {e}", text=(resp.text or "")[:2000],
                                transcript_path=str(traj / "response.md"), label=task.label)
    changes = write_files(ws, files, allowed_roots=allowed_roots)
    if events is not None:
        events.emit("generate.done", label=task.label, strategy="single-shot", files=[c.path for c in changes],
                    cost_usd=round(usage.cost_usd, 4))
    return GenerationResult(ok=bool(changes), usage=usage, files_changed=changes, text=(resp.text or "")[:2000],
                            transcript_path=str(traj / "response.md"), label=task.label)


def run_agent_task(
    ws: Workspace,
    *,
    agent: Any,
    task: GenerationTask,
    settings: Any | None = None,
    budget: Any | None = None,
    events: Any | None = None,
    retry_silent_bail: bool = True,
) -> GenerationResult:
    """CodingAgent path.  ``ok`` = the agent changed files (envelope-vs-disk truth)."""
    before = ws.head()
    timeout = task.timeout_s or (settings.limits.agent_timeout_s if settings is not None else 1800)
    # job.extra is honoured by every CodingAgent: round → trajectory dir + ToolContext,
    # language/track → spatial tool filtering (see agents/*).
    extra = {"round": task.round, "kind": task.kind}
    spec_path = ws.spec_path
    if spec_path.is_file():
        try:
            spec_d = ws.read_json(spec_path)
            extra["language"] = spec_d.get("language", "")
            extra["track"] = spec_d.get("track", "")
        except Exception:  # noqa: BLE001 — best effort context only
            pass
    job = AgentJob(workspace=str(ws.root), prompt=task.prompt, system_append=task.system,
                   model=getattr(agent, "model", ""), label=task.label, timeout_s=timeout,
                   spatial_tools=True, write_roots=task.write_roots, extra=extra)
    res: AgentResult = agent.run(job)
    usage = res.usage
    if budget is not None:
        budget.charge(res.usage)
    changes = res.files_changed or changed_files_safe(ws, before)
    if retry_silent_bail and not changes and res.exit_reason not in ("timeout", "budget"):
        if events is not None:
            events.emit("generate.silent_bail", label=task.label, exit_reason=res.exit_reason)
        job2 = job.model_copy(update={"prompt": (
            "Your previous attempt ended WITHOUT writing any file. You must create/edit the files "
            "described below and run the build tool before finishing.\n\n" + task.prompt)})
        res = agent.run(job2)
        usage = usage + res.usage
        if budget is not None:
            budget.charge(res.usage)
        changes = res.files_changed or changed_files_safe(ws, before)
    notes = f"exit={res.exit_reason}" + (f"; errors={res.errors[:3]}" if res.errors else "")
    if events is not None:
        events.emit("generate.done", label=task.label, strategy=getattr(agent, "kind", "agent"),
                    files=[c.path for c in changes], ok=bool(changes), exit_reason=res.exit_reason,
                    cost_usd=round(usage.cost_usd, 4))
    return GenerationResult(ok=bool(changes), usage=usage, files_changed=changes, notes=notes,
                            text=(res.text or "")[:2000], transcript_path=res.transcript_path, label=task.label)


def generate(
    ws: Workspace,
    *,
    agent_id: str,
    task: GenerationTask,
    agent: Any | None = None,
    model: Any | None = None,
    settings: Any | None = None,
    budget: Any | None = None,
    events: Any | None = None,
    model_factory: Callable[[str], Any] | None = None,
    agent_factory: Callable[[str], Any] | None = None,
) -> GenerationResult:
    """Dispatch to the single-shot or the coding-agent strategy by ``agent_id``.

    ``agent`` / ``model`` may be injected (tests, reuse); otherwise they are
    resolved lazily through ``codeverse.agents`` / ``codeverse.models``.
    """
    if is_single_shot(agent_id):
        if model is None:
            factory = model_factory or _default_model_factory
            model = factory(single_shot_model_id(agent_id))
        return generate_files(ws, model=model, task=task, budget=budget, events=events,
                              allowed_roots=tuple(r.rstrip("/") + "/" for r in task.write_roots))
    if agent is None:
        factory = agent_factory or _default_agent_factory
        agent = factory(agent_id)
    return run_agent_task(ws, agent=agent, task=task, settings=settings, budget=budget, events=events)


def _default_model_factory(model_id: str) -> Any:
    from codeverse.models import get_chat_model

    return get_chat_model(model_id)


def _default_agent_factory(agent_id: str) -> Any:
    from codeverse.agents import get_coding_agent

    return get_coding_agent(agent_id)
