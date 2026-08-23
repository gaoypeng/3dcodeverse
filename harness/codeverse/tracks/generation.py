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
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Usage
from codeverse.tracks.envelope import (  # noqa: F401 — re-exported: this was their import path
    ALLOWED_ROOTS,
    SINGLE_SHOT_FORMAT,
    GenerationError,
    MultiFileParseError,
    parse_multifile,
    safe_relpath,
    write_files,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

SINGLE_SHOT_PREFIX = "single-shot:"


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
    if _is_truncated(resp):
        # cut off by max_output_tokens: the envelope is unterminated — one retry with
        # a doubled budget beats writing a half-file and burning repair attempts on it
        if events is not None:
            events.emit("generate.truncated", label=task.label, finish_reason=str(resp.finish_reason), retry=True)
        req = req.model_copy(update={"max_output_tokens": min(task.max_output_tokens * 2, 65536)})
        resp = model.generate(req)
        usage = usage + resp.usage
        if budget is not None:
            budget.charge(resp.usage)
    traj = ws.trajectory_dir(task.label.replace("/", "_"), task.round)
    (traj / "prompt.md").write_text(f"# system\n{system}\n\n# user\n{task.prompt}\n")
    (traj / "response.md").write_text(resp.text or "")
    if _is_truncated(resp):
        if events is not None:
            events.emit("generate.truncated", label=task.label, finish_reason=str(resp.finish_reason), retry=False)
        return GenerationResult(ok=False, usage=usage, notes=f"truncated: finish_reason={resp.finish_reason}",
                                text=(resp.text or "")[:2000], transcript_path=str(traj / "response.md"), label=task.label)
    try:
        files = parse_multifile(resp.text or "", expected_files=task.files_hint or None)
    except MultiFileParseError as e:
        if events is not None:
            events.emit("generate.parse_failed", label=task.label, error=str(e))
        return GenerationResult(ok=False, usage=usage, notes=f"parse failed: {e}", text=(resp.text or "")[:2000],
                                transcript_path=str(traj / "response.md"), label=task.label)
    skipped: list[str] = []

    def _skip(path: str, reason: str) -> None:
        skipped.append(path)
        if events is not None:
            events.emit("generate.skipped_path", label=task.label, path=path, reason=reason[:200])

    changes = write_files(ws, files, allowed_roots=allowed_roots, on_skip=_skip)
    if events is not None:
        events.emit("generate.done", label=task.label, strategy="single-shot", files=[c.path for c in changes],
                    cost_usd=round(usage.cost_usd, 4))
    notes = f"skipped out-of-root paths: {', '.join(skipped)}" if skipped else ""
    return GenerationResult(ok=bool(changes), usage=usage, files_changed=changes, notes=notes,
                            text=(resp.text or "")[:2000], transcript_path=str(traj / "response.md"), label=task.label)


_TRUNCATED_FINISH = {"max_tokens", "max_output_tokens", "length"}


def _is_truncated(resp: Any) -> bool:
    return str(getattr(resp, "finish_reason", "") or "").lower() in _TRUNCATED_FINISH


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
    # language/track → spatial tool filtering, files_hint → per-session attribution of
    # files_changed when tasks run concurrently in ONE workspace (see agents/cli_common).
    extra: dict[str, Any] = {"round": task.round, "kind": task.kind, "files_hint": list(task.files_hint)}
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
    changes = res.files_changed or _attributed_fallback(ws, task, before)
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
        changes = res.files_changed or _attributed_fallback(ws, task, before)
    notes = f"exit={res.exit_reason}" + (f"; errors={res.errors[:3]}" if res.errors else "")
    if events is not None:
        events.emit("generate.done", label=task.label, strategy=getattr(agent, "kind", "agent"),
                    files=[c.path for c in changes], ok=bool(changes), exit_reason=res.exit_reason,
                    cost_usd=round(usage.cost_usd, 4))
    return GenerationResult(ok=bool(changes), usage=usage, files_changed=changes, notes=notes,
                            text=(res.text or "")[:2000], transcript_path=res.transcript_path, label=task.label)


def _attributed_fallback(ws: Workspace, task: GenerationTask, before: str) -> list[FileChange]:
    """Change detection when the agent did not report its own ``files_changed``.

    A whole-worktree diff claims sibling tasks' files under fan_out (a task that
    wrote nothing looked ok because its neighbours wrote files); attribute the diff
    to this task instead: inside its write roots, never harness-owned paths."""
    raw = changed_files_safe(ws, before)
    try:
        from codeverse.agents.cli_common import attribute_changes
    except ImportError:  # pragma: no cover — agents package always ships with tracks
        return raw
    return attribute_changes(raw, write_roots=list(task.write_roots), own_hints=frozenset(task.files_hint))


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
