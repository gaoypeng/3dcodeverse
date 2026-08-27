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

**Turn budget — off by default, measured.**  The audit's turn-≥20 share (39.3%
of the agent bill, docs/COST.md §4) looked like $14 of waste, so wave 2 capped
every session at 28 turns.  A controlled A/B rejected it — same prompt, n=3 per
arm: capped $1.381 / **0.479**, uncapped $1.360 / **0.684** (docs/COST.md §17).
A session cut off mid-task leaves work the next round pays for again.

So there is **no default cap** (``DEFAULT_AGENT_MAX_TURNS`` and
``RoundPolicy.agent_max_turns`` are 0 → the backend's own ``AgentJob.max_turns``).
A caller may still set one (``GenerationTask.max_turns`` > ``generate(max_turns=…)``
> ``CV3D_AGENT_MAX_TURNS`` > ``Settings.limits.agent_max_turns``, which a cost
profile sets), and a cap that IS set stays **graceful**: the session is not killed
but asked, in a short wrap-up (``agent_wrapup_turns``), for one last build + summary.

**Every dollar is charged.**  Both strategies spend through
``BudgetGuard.spend`` with their stage / label / round, so a retried session
(``<label>.a2``) and a wrap-up session are visible to the guard, the record and
the ledger even when the *next* attempt then raises.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
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

#: default cap on model turns per agent session: 0 = none, the session runs under the
#: backend's own ``AgentJob.max_turns``.  A 28-turn default was measured and rejected
#: (A/B: no saving, −0.205 score) — see the module docstring and docs/COST.md §17.
DEFAULT_AGENT_MAX_TURNS = 0
#: turns granted to the wrap-up session that lands a final build + summary when a cap IS set
DEFAULT_WRAPUP_TURNS = 6

WRAPUP_PROMPT = (
    "You have reached the turn budget for this task, so this is your LAST session on it.\n"
    "Do NOT start new work and do NOT begin any refactor.\n"
    "1. Make sure every file you edited is written to disk and self-consistent.\n"
    "2. Run the build tool ONCE. If it fails, make the smallest edit that makes it build "
    "(or revert your last incomplete edit) and build once more.\n"
    "3. Reply with a short summary: what you changed, what still does not match the task, "
    "and the single next step you would take.\n\n"
    "The task you were working on, for reference:\n"
)


def agent_max_turns(default: int = DEFAULT_AGENT_MAX_TURNS) -> int:
    """The turn cap a machine/profile asks for: ``CV3D_AGENT_MAX_TURNS`` >
    ``Settings.limits.agent_max_turns`` (what a cost profile sets) > ``default``.

    ``0`` means *no cap*: the caller leaves ``AgentJob.max_turns`` at the backend's
    own default.  That is the measured default (docs/COST.md §17)."""
    raw = os.environ.get("CV3D_AGENT_MAX_TURNS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    try:
        from codeverse.config import get_settings

        configured = int(getattr(get_settings().limits, "agent_max_turns", 0) or 0)
    except Exception as e:  # noqa: BLE001 — settings must never break a generation
        log.debug("agent turn cap fell back to the default: %s", e)
        configured = 0
    return max(0, configured or int(default))


def turn_capped(res: Any) -> bool:
    """True when the agent stopped because it ran out of turns (not money/time).

    api-agent says ``max_turns (N) reached``; claude-code reports the subtype
    ``error_max_turns`` — both surface as ``exit_reason="budget"``."""
    if getattr(res, "exit_reason", "") != "budget":
        return False
    return any("max_turns" in str(e) for e in (getattr(res, "errors", None) or []))


class GenerationTask(BaseModel):
    """One unit of code-writing work (baseline, refine group, asset, zone, repair)."""

    label: str
    prompt: str
    system: str = Field(
        default="", description="system prompt (single-shot) / system_append (agent)"
    )
    files_hint: list[str] = Field(
        default_factory=list, description="files this task should produce/edit"
    )
    round: int = 0
    kind: str = "generate"
    images: list[ImagePart] = Field(default_factory=list)
    temperature: float = 0.6
    thinking: str = "medium"
    max_output_tokens: int = 32000
    timeout_s: int | None = None
    max_turns: int = Field(
        default=0, description="model turns for an agent session (0 = the harness default)"
    )
    write_roots: list[str] = Field(default_factory=lambda: ["src", "public"])
    edit_only: bool = Field(
        default=False,
        description="enforce files_hint as the only existing files this session may overwrite",
    )
    owns_entry: bool = Field(
        default=False,
        description="this task may write the language entry file even under edit_only (assembly / "
        "whole-object / single-file tasks, and scoped refines whose prompt promises entry access). "
        "A task whose files_hint names the entry owns it implicitly; part / zone / detail / asset "
        "tasks default to False so parallel scoped sessions cannot race on the entry file",
    )
    phase: int = Field(
        default=0,
        ge=0,
        description=(
            "execution phase inside ONE round: tasks run in parallel WITHIN a phase and phases run "
            "in ascending order.  Everything is phase 0 unless a track says otherwise — the only "
            "current user is per-part scoped generation, where the assembly session (phase 1) must "
            "see the part files the scoped sessions (phase 0) wrote."
        ),
    )


class GenerationResult(BaseModel):
    ok: bool
    usage: Usage = Field(default_factory=Usage)
    files_changed: list[FileChange] = Field(default_factory=list)
    commit: str = ""
    notes: str = ""
    text: str = Field(default="", description="model/agent final text (truncated)")
    transcript_path: str = ""
    label: str = ""
    turns: int = Field(
        default=0, description="turns the agent session(s) took, as the backend counts them"
    )
    sessions: int = Field(
        default=0, description="agent sessions run for this task (retry / wrap-up count)"
    )
    turn_capped: bool = Field(
        default=False, description="a session hit the turn budget and was wrapped up"
    )


# ----------------------------------------------------------------------------- strategies
def is_single_shot(agent_id: str) -> bool:
    return agent_id.startswith(SINGLE_SHOT_PREFIX)


def single_shot_model_id(agent_id: str) -> str:
    if not is_single_shot(agent_id):
        raise ValueError(f"not a single-shot id: {agent_id!r}")
    return agent_id[len(SINGLE_SHOT_PREFIX) :]


def single_shot_format_text() -> str:
    """The canonical format doc (``prompts/system/singleshot_format.md`` when present)."""
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
        # the round tag is the cost ledger's only way to attribute a single-shot call:
        # it has no agent session to inherit an ambient round from (codeverse.cost.context)
        label=f"{task.label}:r{task.round:02d}",
    )
    resp = model.generate(req)
    usage = resp.usage
    # book the money WITHOUT enforcing: the response is already paid for, and raising
    # here would discard it before transcript/parse/write_files.  The ceiling is
    # enforced at the round's phase boundary instead (steps._run_phase).
    _charge(budget, usage, task=task, label=task.label, outcome=str(resp.finish_reason or "ok"),
            enforce=False)
    if _is_truncated(resp):
        # cut off by max_output_tokens: the envelope is unterminated — one retry with
        # a doubled budget beats writing a half-file and burning repair attempts on it
        if events is not None:
            events.emit(
                "generate.truncated",
                label=task.label,
                finish_reason=str(resp.finish_reason),
                retry=True,
            )
        req = req.model_copy(update={"max_output_tokens": min(task.max_output_tokens * 2, 65536)})
        resp = model.generate(req)
        usage = usage + resp.usage
        _charge(budget, resp.usage, task=task, label=f"{task.label}.retry", outcome="truncated",
                enforce=False)
    traj = ws.trajectory_dir(task.label.replace("/", "_"), task.round)
    (traj / "prompt.md").write_text(f"# system\n{system}\n\n# user\n{task.prompt}\n")
    (traj / "response.md").write_text(resp.text or "")
    if _is_truncated(resp):
        if events is not None:
            events.emit(
                "generate.truncated",
                label=task.label,
                finish_reason=str(resp.finish_reason),
                retry=False,
            )
        return GenerationResult(
            ok=False,
            usage=usage,
            notes=f"truncated: finish_reason={resp.finish_reason}",
            text=(resp.text or "")[:2000],
            transcript_path=str(traj / "response.md"),
            label=task.label,
        )
    try:
        files = parse_multifile(resp.text or "", expected_files=task.files_hint or None)
    except MultiFileParseError as e:
        if events is not None:
            events.emit("generate.parse_failed", label=task.label, error=str(e))
        return GenerationResult(
            ok=False,
            usage=usage,
            notes=f"parse failed: {e}",
            text=(resp.text or "")[:2000],
            transcript_path=str(traj / "response.md"),
            label=task.label,
        )
    skipped: list[str] = []

    def _skip(path: str, reason: str) -> None:
        skipped.append(path)
        if events is not None:
            events.emit("generate.skipped_path", label=task.label, path=path, reason=reason[:200])

    changes = write_files(ws, files, allowed_roots=allowed_roots,
                          only=_scope_only(ws, task), on_skip=_skip)
    if events is not None:
        events.emit(
            "generate.done",
            label=task.label,
            strategy="single-shot",
            files=[c.path for c in changes],
            cost_usd=round(usage.cost_usd, 4),
        )
    notes = f"skipped out-of-root paths: {', '.join(skipped)}" if skipped else ""
    return GenerationResult(
        ok=bool(changes),
        usage=usage,
        files_changed=changes,
        notes=notes,
        text=(resp.text or "")[:2000],
        transcript_path=str(traj / "response.md"),
        label=task.label,
    )


#: task kinds whose money belongs to a differently-named stage of the cost vocabulary
_STAGE_FOR_KIND = {
    "rebuild": "repair",
    "asset_fix": "assets",
    "asset": "assets",
    "zone": "zones",
    "generate": "baseline",
    "compose": "assemble",
}


def task_stage(task: GenerationTask) -> str:
    """The cost stage one generation task spends in (a ``codeverse.cost.types.Stage``
    value when we can name one, else the label — which ``record_call`` maps through
    ``stage_for_label`` (``asset_koi`` → assets, ``r00_baseline_repair1`` → repair))."""
    kind = task.kind or ""
    stage = _STAGE_FOR_KIND.get(kind, kind)
    try:
        from codeverse.cost.types import Stage

        return str(Stage(stage))
    except Exception:  # noqa: BLE001 — unknown kind (or no cost package): let the label decide
        return task.label or stage or "other"


def _charge(
    budget: Any | None, usage: Usage, *, task: GenerationTask, label: str, outcome: str = "ok",
    enforce: bool = True,
) -> None:
    """Spend through the guard with the stage/label/round this task belongs to.

    ``enforce=False`` books the dollar without raising — the single-shot path uses it
    so a paid response is still parsed and written to disk; the ceiling is enforced at
    the round's phase boundary (``steps._run_phase``) after the work is persisted."""
    if budget is None:
        return
    budget.charge(
        usage,
        stage=task_stage(task),
        role="generator",
        label=label,
        round_index=task.round,
        outcome=outcome,
        enforce=enforce,
    )


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
    max_turns: int = 0,
    wrapup_turns: int = DEFAULT_WRAPUP_TURNS,
) -> GenerationResult:
    """CodingAgent path.  ``ok`` = the agent changed files (envelope-vs-disk truth).

    A turn cap is applied only when a caller asks for one (``task.max_turns`` >
    ``max_turns`` > ``CV3D_AGENT_MAX_TURNS`` > ``Settings.limits.agent_max_turns``
    > :data:`DEFAULT_AGENT_MAX_TURNS`, which is 0 = the backend's own
    ``AgentJob.max_turns``; a 28-turn default was measured and rejected, see the
    module docstring).  Hitting whatever cap is in force is not a failure: one
    short wrap-up session is asked for a final build and a summary, so the money
    already spent leaves a buildable workspace instead of a session that was cut
    off mid-edit.  Every session — first attempt, silent-bail retry, wrap-up — is
    charged to ``budget`` as it ends, so nothing is lost when a later attempt raises."""
    before = ws.head()
    timeout = task.timeout_s or (settings.limits.agent_timeout_s if settings is not None else 1800)
    turns_cap = task.max_turns or max_turns or agent_max_turns()  # 0 = leave AgentJob's own default
    # typed job context honoured by every CodingAgent: round → trajectory dir + ToolContext,
    # language/track → spatial tool filtering, files_hint → per-session attribution of
    # files_changed when tasks run concurrently in ONE workspace (see agents/cli_common).
    language, track = _spec_lang_track(ws)
    job = AgentJob(
        workspace=str(ws.root),
        prompt=task.prompt,
        system_append=task.system,
        model=getattr(agent, "model", ""),
        label=task.label,
        timeout_s=timeout,
        spatial_tools=True,
        write_roots=task.write_roots,
        round=task.round,
        kind=task.kind,
        language=language,
        track=track,
        files_hint=list(task.files_hint),
        edit_only=task.edit_only,
        always_writable=_always_writable(language, task),
        read_only=_read_only(language),
        images=list(task.images),
        **({"max_turns": turns_cap} if turns_cap > 0 else {}),
    )
    acc = _SessionAcc(task=task, budget=budget)
    res = acc.run(agent, job)
    changes = res.files_changed or _attributed_fallback(ws, task, before)

    if turn_capped(res):
        # the turns are gone, the money is not wasted: ask for a landing, not more work
        if events is not None:
            # NB: not ``cost_usd`` — an event carrying that key is priced as its own
            # call by ``cost.reconstruct``, and this money is already in the session row.
            events.emit(
                "generate.turn_cap",
                label=task.label,
                round=task.round,
                max_turns=job.max_turns,
                turns=acc.turns,
                session_usd=round(acc.usage.cost_usd, 4),
            )
        wrap = job.model_copy(
            update={"prompt": WRAPUP_PROMPT + task.prompt, "max_turns": max(2, int(wrapup_turns))}
        )
        res = acc.run(agent, wrap, wrapup=True, optional=True) or res
        changes = res.files_changed or changes or _attributed_fallback(ws, task, before)

    if (
        retry_silent_bail
        and not changes
        and not acc.wrapped
        and res.exit_reason not in ("timeout", "budget")
    ):
        if events is not None:
            events.emit("generate.silent_bail", label=task.label, exit_reason=res.exit_reason)
        job2 = job.model_copy(
            update={
                "prompt": (
                    "Your previous attempt ended WITHOUT writing any file. You must create/edit the files "
                    "described below and run the build tool before finishing.\n\n" + task.prompt
                )
            }
        )
        res = acc.run(agent, job2, optional=True) or res
        changes = res.files_changed or _attributed_fallback(ws, task, before)

    notes = f"exit={res.exit_reason}" + (f"; errors={res.errors[:3]}" if res.errors else "")
    notes += ("; " + "; ".join(acc.errors)) if acc.errors else ""
    if acc.wrapped:
        notes += f"; turn cap {job.max_turns} reached → wrap-up session"
    if events is not None:
        events.emit(
            "generate.done",
            label=task.label,
            strategy=getattr(agent, "kind", "agent"),
            files=[c.path for c in changes],
            ok=bool(changes),
            exit_reason=res.exit_reason,
            turns=acc.turns,
            sessions=acc.sessions,
            turn_capped=acc.wrapped,
            cost_usd=round(acc.usage.cost_usd, 4),
        )
    return GenerationResult(
        ok=bool(changes),
        usage=acc.usage,
        files_changed=changes,
        notes=notes,
        text=(res.text or "")[:2000],
        transcript_path=res.transcript_path,
        label=task.label,
        turns=acc.turns,
        sessions=acc.sessions,
        turn_capped=acc.wrapped,
    )


class _SessionAcc:
    """Runs the agent sessions of ONE task and keeps their accounting.

    Each session is charged the moment it returns (label ``<task>``/``<task>.a2``
    for the retry, ``<task>.wrapup`` for the landing session), so a crash in a
    later session can never erase what an earlier one already spent — the ``.a2``
    hole of docs/COST.md §6."""

    def __init__(self, *, task: GenerationTask, budget: Any | None):
        self.task = task
        self.budget = budget
        self.usage = Usage()
        self.turns = 0
        self.sessions = 0
        self.wrapped = False
        self.errors: list[str] = []

    def run(
        self, agent: Any, job: AgentJob, *, wrapup: bool = False, optional: bool = False
    ) -> AgentResult | None:
        """One session.  ``optional`` sessions (wrap-up, silent-bail retry) never
        take the task down: a crash there leaves the earlier, already charged
        session as the result instead of discarding money that was really spent."""
        self.sessions += 1
        self.wrapped = self.wrapped or wrapup
        label = self.task.label + (
            ".wrapup" if wrapup else (f".a{self.sessions}" if self.sessions > 1 else "")
        )
        try:
            res: AgentResult = agent.run(job)
        except Exception as e:  # noqa: BLE001 — a budget stop still propagates (see below)
            self.sessions -= 1
            if not optional or _is_budget_stop(e):
                raise
            log.warning("optional agent session %s failed: %s: %s", label, type(e).__name__, e)
            self.errors.append(f"{label}: {type(e).__name__}: {e}")
            return None
        self.usage = self.usage + res.usage
        self.turns += session_turns(res)
        self.charge(res.usage, label=label, outcome=res.exit_reason or "ok")
        return res

    def charge(self, usage: Usage, *, label: str, outcome: str) -> None:
        if self.budget is None or not (usage.cost_usd or usage.input_tokens or usage.output_tokens):
            return
        self.budget.charge(
            usage,
            stage=task_stage(self.task),
            role="generator",
            label=label,
            round_index=self.task.round,
            outcome=outcome,
        )


def _is_budget_stop(e: BaseException) -> bool:
    from codeverse.orchestrator.budget import BudgetExceeded

    return isinstance(e, BudgetExceeded)


def session_turns(res: Any) -> int:
    """The session's turn count as the BACKEND reports it — the ``turns`` field
    ``agents/cli_common.finish_session`` writes into ``result.json`` (0 when the
    backend does not report one).

    Backends count slightly differently (the api-agent reports transcript
    messages, roughly two per model turn), so this is a size signal for the
    ``cost.round`` event, not the number the turn cap is compared against —
    that one is enforced inside the session by ``job.max_turns``."""
    path = getattr(res, "transcript_path", "") or ""
    if not path:
        return 0
    p = Path(path)
    result = (p if p.is_dir() else p.parent) / "result.json"
    try:
        if result.is_file():
            value = json.loads(result.read_text(errors="replace")).get("turns")
            if isinstance(value, (int, float)):
                return int(value)
    except (OSError, ValueError) as e:  # noqa: PERF203 - best effort telemetry
        log.debug("session turns unreadable (%s): %s", result, e)
    return 0


def _spec_lang_track(ws: Workspace) -> tuple[str, str]:
    """Best-effort ``(language, track)`` from the workspace's spec.json ("" when absent)."""
    if not ws.spec_path.is_file():
        return "", ""
    try:
        spec_d = ws.read_json(ws.spec_path)
    except Exception:  # noqa: BLE001 — best effort context only
        return "", ""
    return str(spec_d.get("language", "")), str(spec_d.get("track", ""))


def _always_writable(language: str, task: GenerationTask) -> list[str]:
    """The entry file, exempt from ``edit_only`` — but only for a task that OWNS it.

    Owning tasks: ``owns_entry=True`` (assembly / whole-object / single-file tasks, and
    scoped refines whose prompt promises entry access) or a ``files_hint`` naming the
    entry.  Part / zone / detail / asset tasks own nothing here — under the old
    unconditional exemption, parallel scoped sessions raced on the entry file unopposed."""
    from codeverse.contracts.common import ENTRY_FILE, Language

    try:
        entry = ENTRY_FILE[Language(language)]
    except (ValueError, KeyError):
        return []
    return [entry] if task.owns_entry or entry in task.files_hint else []


def _scope_only(ws: Workspace, task: GenerationTask) -> set[str] | None:
    """``write_files``'s scope for an ``edit_only`` single-shot task: the files_hint plus
    the entry file when the task owns it (the envelope-path mirror of FileTools)."""
    if not task.edit_only or not task.files_hint:
        return None
    language, _ = _spec_lang_track(ws)
    return {f for f in task.files_hint if f} | set(_always_writable(language, task))


def _read_only(language: str) -> list[str]:
    """The harness-owned files of the language (``src/recipes.glsl`` for glsl_shader): every session
    of the run — baseline, refine, repair — may read them, none may write them."""
    from codeverse.contracts.common import HARNESS_OWNED_SRC, Language

    try:
        return list(HARNESS_OWNED_SRC.get(Language(language), ()))
    except ValueError:
        return []


def _attributed_fallback(ws: Workspace, task: GenerationTask, before: str) -> list[FileChange]:
    """Change detection when the agent did not report its own ``files_changed``.

    A whole-worktree diff claims sibling tasks' files under fan_out (a task that
    wrote nothing looked ok because its neighbours wrote files); attribute the diff
    to this task instead: inside its write roots, never harness-owned paths."""
    raw = ws.changed_files(before)  # Workspace serialises + retries the git index itself
    try:
        from codeverse.agents.cli_common import attribute_changes
    except ImportError:  # pragma: no cover — agents package always ships with tracks
        return raw
    return attribute_changes(
        raw, write_roots=list(task.write_roots), own_hints=frozenset(task.files_hint)
    )


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
    max_turns: int = 0,
    wrapup_turns: int = DEFAULT_WRAPUP_TURNS,
) -> GenerationResult:
    """Dispatch to the single-shot or the coding-agent strategy by ``agent_id``.

    ``agent`` / ``model`` may be injected (tests, reuse); otherwise they are
    resolved lazily through ``codeverse.agents`` / ``codeverse.models``.
    """
    if is_single_shot(agent_id):
        if model is None:
            factory = model_factory or _default_model_factory
            model = factory(single_shot_model_id(agent_id))
        return generate_files(
            ws,
            model=model,
            task=task,
            budget=budget,
            events=events,
            allowed_roots=tuple(r.rstrip("/") + "/" for r in task.write_roots),
        )
    if agent is None:
        factory = agent_factory or _default_agent_factory
        agent = factory(agent_id)
    return run_agent_task(
        ws,
        agent=agent,
        task=task,
        settings=settings,
        budget=budget,
        events=events,
        max_turns=max_turns,
        wrapup_turns=wrapup_turns,
    )


def _default_model_factory(model_id: str) -> Any:
    from codeverse.models import get_chat_model

    return get_chat_model(model_id)


def _default_agent_factory(agent_id: str) -> Any:
    from codeverse.agents import get_coding_agent

    return get_coding_agent(agent_id)
