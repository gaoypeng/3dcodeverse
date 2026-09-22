"""Code generation: one entry point, two strategies.

* **CodingAgent path** — ``agent_id`` is any id ``codeverse3d.agents`` knows
  (``gemini-cli:*``, ``claude-code:*``, ``codex:*``, ``agy:*``):
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

So there is **no default cap** (``DEFAULT_AGENT_MAX_TURNS`` is 0 → the backend's own
``AgentJob.max_turns``).  A machine may still set one (``C3D_AGENT_MAX_TURNS`` >
``Settings.limits.agent_max_turns``; no cost profile sets one), and a cap that IS set
stays **graceful**: the session is not killed but asked, in a short wrap-up
(``DEFAULT_WRAPUP_TURNS``), for one last build + summary.

**Every dollar is on the ledger.**  Both strategies spend through the metered model or
agent (``cost.instrument``), one row per call or session — a retried session
(``<label>.a2``) and a wrap-up session included, even when the *next* attempt then
raises.  The run's clock (``BudgetGuard``) is checked at the session boundary.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse3d.contracts.common import Usage
from codeverse3d.proc import read_json_or_none
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# ===================================================================== single-shot envelope
# (merged back from codeverse3d/tracks/envelope.py, 2026-08-28 — the split existed only
#  to hold a re-export shim; this module was its one importer)
ALLOWED_ROOTS: tuple[str, ...] = ("src/", "public/")
#: where an out-of-workspace task image is copied so a vendor CLI can read it (D9, like the cookbook)
IMAGES_DIR = ".3dcode/images"

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
        return {expected[0]: _strip_envelope_header(text.strip("\n"))}
    raise MultiFileParseError(
        "no '=== FILE: <path> ===' blocks found" + (f"; expected {expected}" if expected else "")
    )


_HEADER_LINE = re.compile(r"^[ \t]*===\s*FILE:\s*[^\n=]+?\s*===[ \t]*\r?\n")


def _strip_envelope_header(body: str) -> str:
    """A truncated answer (cut before ``=== END FILE ===``) reaches the single-file
    fallback with its ``=== FILE: … ===`` header still attached; treat the
    unterminated block as the file's body instead of shipping the header as line 1."""
    m = _HEADER_LINE.match(body)
    return body[m.end():] if m else body


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
    if p.endswith("/"):  # '=== FILE: src/parts/ ===' names a directory, not a file
        raise GenerationError(f"path {path!r} is a directory, not a file")
    if not any(p.startswith(root) for root in allowed_roots):
        raise GenerationError(f"path {p!r} is outside the allowed roots {allowed_roots}")
    return p


def write_files(
    ws: Workspace,
    files: dict[str, str],
    *,
    allowed_roots: tuple[str, ...] = ALLOWED_ROOTS,
    only: Collection[str] | None = None,
    frozen: Collection[str] = (),
    on_skip: Callable[[str, str], None] | None = None,
) -> list[FileChange]:
    """Write parsed files under the workspace; returns git-style FileChange rows.

    A block whose path is unsafe, outside ``allowed_roots`` (flash models love
    to add README.md / package.json despite the format rule) or unwritable is
    SKIPPED — reported via ``on_skip`` — instead of aborting the whole write:
    the valid files were already paid for.

    ``only`` (an ``edit_only`` task's file scope, entry included when owned) skips a
    path that ALREADY EXISTS and is not listed — new files stay allowed; ``frozen``
    (the language's harness-owned files, ``src/recipes.glsl``) is skipped outright.
    The single-shot envelope has no write-time gate, so this is where a scoped task is
    stopped from rewriting a sibling's file — the mirror of the CLI backends' post-hoc
    ``_enforce_scope`` (until 2026-08-29 only that side protected the harness files)."""
    scope = {_clean_path(p) for p in only} if only is not None else None
    owned = {_clean_path(p) for p in frozen}
    changes: list[FileChange] = []
    for raw, content in files.items():
        try:
            rel = safe_relpath(raw, allowed_roots)
        except GenerationError as e:
            if on_skip is not None:
                on_skip(raw, str(e))
            log.warning("skipping out-of-root file from generator: %s", e)
            continue
        dest = ws.root / rel
        existed = dest.exists()
        if rel in owned:
            reason = "harness-owned: call its functions, never rewrite it"
            if on_skip is not None:
                on_skip(raw, reason)
            log.warning("skipping harness-owned file from generator: %s", rel)
            continue
        if scope is not None and existed and rel not in scope:
            reason = f"out of scope: {rel} already exists and is not in this task's file list"
            if on_skip is not None:
                on_skip(raw, reason)
            log.warning("skipping out-of-scope file from generator: %s", rel)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content if content.endswith("\n") else content + "\n")
        except OSError as e:
            # Same contract as an out-of-root path: one unwritable block (a path that
            # is already a directory, a name the filesystem rejects) must not throw
            # away the files that DID parse -- the whole answer was already paid for.
            if on_skip is not None:
                on_skip(raw, str(e))
            log.warning("skipping unwritable file from generator: %s: %s", rel, e)
            continue
        changes.append(FileChange(path=rel, status="modified" if existed else "added",
                                  lines_added=content.count("\n") + 1))
    return changes

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
    """The turn cap a machine asks for: ``Settings.limits.agent_max_turns`` (also spelled
    ``C3D_AGENT_MAX_TURNS``) > ``default``.

    ``0`` means *no cap*: the caller leaves ``AgentJob.max_turns`` at the backend's
    own default.  That is the measured default (docs/COST.md §17)."""
    from codeverse3d.config import get_settings

    return get_settings().limits.agent_max_turns or max(0, int(default))


def turn_capped(res: Any) -> bool:
    """True when the agent stopped because it ran out of turns (not money/time).

    claude-code reports the subtype ``error_max_turns``; it surfaces as
    ``exit_reason="budget"``."""
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
    max_output_tokens: int = 65_536   # the model's declared ceiling; unused tokens cost nothing
    timeout_s: int | None = None
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
    transient: bool = Field(
        default=False,
        description="the last session died of a provider failure (AgentResult.transient), or the call "
        "raised one (a ModelError the retry layer marks retryable or answers 429/5xx: is_model_outage) — "
        "whatever it wrote before the wall is in files_changed",
    )
    quota: bool = Field(
        default=False,
        description="the last session met the vendor's usage limit (AgentResult.quota): no retry gets "
        "through until it resets",
    )
    storm: bool = Field(
        default=False,
        description="transient AND nothing was written — the agent route is down, not the task; "
        "tracks fall back to the hedged single-shot path (tracks.common.generate_for)",
    )

    @classmethod
    def from_error(cls, label: str, e: BaseException) -> GenerationResult:
        """The failed result of a generation call that RAISED, classified by the error's type
        rather than its message: the round loop reads ``transient`` / ``quota``, never text."""
        return cls(ok=False, notes=f"{type(e).__name__}: {e}", label=label, transient=is_model_outage(e))


def is_model_outage(e: BaseException) -> bool:
    """Is this a *service* failure (the model is down) rather than a bad answer?

    Read off the ``ModelError`` the models layer raises — ``retryable`` or an HTTP status of
    capacity / rate limiting — never off its message.  A 503 capacity storm reaches us only
    after ``models.retry`` has already spent its whole storm budget waiting."""
    from codeverse3d.models.base import ModelError

    if isinstance(e, ModelError):
        return bool(e.retryable) or e.status in (429, 500, 502, 503, 504, 529)
    return False


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
        from codeverse3d.prompts import load_text

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
    from codeverse3d.models.retry import RETRY_DEADLINE_S

    system = task.system + ("\n\n" if task.system else "") + single_shot_format_text()
    req = ChatRequest(
        messages=[ChatMessage.user(task.prompt, images=task.images or None)],
        system=system,
        temperature=task.temperature,
        thinking=task.thinking,  # type: ignore[arg-type]
        max_output_tokens=task.max_output_tokens,
        # the RUN's remaining wall clock, not the models' 1800 s default — and past
        # the hard ceiling this refuses to buy the call at all (agent-path symmetry)
        max_wait_s=_deadline_preflight(budget, RETRY_DEADLINE_S),
        # the round tag is the cost ledger's only way to attribute a single-shot call:
        # it has no agent session to inherit an ambient round from (codeverse3d.cost.context)
        label=f"{task.label}:r{task.round:02d}",
    )
    resp = model.generate(req)
    # + every paid round-trip the winner does not represent (a billed-but-invalid
    # response, a late hedge loser): the ledger records those as source="extra"
    usage = resp.usage + (resp.raw.get("wasted_usage") or Usage())
    # the clock is not enforced here: the response is already paid for, and raising would
    # discard it before transcript/parse/write_files — the round's phase boundary does
    # (steps._run_phase)
    retry = False
    if _is_truncated(resp):
        # cut off by max_output_tokens: the envelope is unterminated — one retry with a
        # DOUBLED budget beats writing a half-file.  Already at the 65,536 model ceiling
        # there is nothing to grow: a re-ask would be byte-identical and full price, so
        # it is not bought (the default task budget IS the ceiling since 2026-08-27).
        grown = min(task.max_output_tokens * 2, 65_536)
        retry = grown > int(req.max_output_tokens or 0)
        if events is not None:
            events.emit(
                "generate.truncated",
                label=task.label,
                finish_reason=str(resp.finish_reason),
                retry=retry,
            )
        if retry:
            # the retry is a second full-price call: preflight the clock again and
            # hand it only what is left of the run (BudgetExceeded past the ceiling)
            req = req.model_copy(update={"max_output_tokens": grown,
                                         "max_wait_s": _deadline_preflight(budget, RETRY_DEADLINE_S)})
            resp = model.generate(req)
            usage = usage + resp.usage
    traj = ws.trajectory_dir(task.label.replace("/", "_"), task.round)
    (traj / "prompt.md").write_text(f"# system\n{system}\n\n# user\n{task.prompt}\n")
    (traj / "response.md").write_text(resp.text or "")
    if _is_truncated(resp):
        if events is not None and retry:   # the RETRY was cut off too; the first cut is already in the log
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

    only, frozen = _envelope_scope(ws, task)
    changes = write_files(ws, files, allowed_roots=allowed_roots, only=only, frozen=frozen, on_skip=_skip)
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


def _deadline_preflight(budget: Any | None, wait_s: float, *, soft: bool = False,
                        floor_s: float = 120.0) -> float:
    """Gate a session on the run clock: refuse to start past the HARD ceiling (a
    timed-out session that produced nothing bills nothing, so the accounting-driven
    check alone never fires) and clip the wait to the remaining wall clock (a flat
    30-minute session once ran 9 minutes past the ceiling, measured 2026-08-27)."""
    if budget is None:
        return wait_s
    if hasattr(budget, "check"):
        budget.check()
    if hasattr(budget, "timeout_s"):
        wait_s = budget.timeout_s(wait_s, floor_s=floor_s, soft=soft)
    return wait_s


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
) -> GenerationResult:
    """CodingAgent path.  ``ok`` = the agent changed files (envelope-vs-disk truth).

    A turn cap is applied only when the machine asks for one (``C3D_AGENT_MAX_TURNS`` >
    ``Settings.limits.agent_max_turns`` > :data:`DEFAULT_AGENT_MAX_TURNS`, which is 0 = the
    backend's own ``AgentJob.max_turns``; a 28-turn default was measured and rejected, see the
    module docstring).  Hitting whatever cap is in force is not a failure: one
    short wrap-up session is asked for a final build and a summary, so the money
    already spent leaves a buildable workspace instead of a session that was cut
    off mid-edit.  Every session — first attempt, silent-bail retry, wrap-up — is
    charged to ``budget`` as it ends, so nothing is lost when a later attempt raises."""
    before = ws.head()
    timeout = task.timeout_s or (settings.limits.agent_timeout_s if settings is not None else 1800)
    # A task that chose its own window already clipped it the way its stage wanted
    # (scene.py: soft for env/zones/assets, hard for refine/rebuild) — re-clipping THAT
    # against the soft share handed every scene refine session after the 0.55 share
    # exactly the 120 s floor instead of the ≤ 900 s it asked for.  A task with no
    # window of its own (repair) is still bounded by the soft share, so a failing
    # baseline cannot eat the refine rounds' half.  (Both found by review 2026-08-29.)
    timeout = _deadline_preflight(budget, timeout, soft=task.timeout_s is None)
    turns_cap = agent_max_turns()  # 0 = leave AgentJob's own default
    # typed job context honoured by every CodingAgent: round → trajectory dir + ToolContext,
    # language/track → spatial tool filtering, files_hint → the edit_only scope.
    language, track = _spec_lang_track(ws)
    prompt = task.prompt + _images_block(task.images, ws)
    job = AgentJob(
        workspace=str(ws.root),
        prompt=prompt,
        system_append=task.system,
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
        **({"max_turns": turns_cap} if turns_cap > 0 else {}),
    )
    acc = _SessionAcc(task=task, budget=budget)
    res = acc.run(agent, job)
    changes = res.files_changed or _attributed_fallback(ws, task, before)

    if turn_capped(res):
        # the turns are gone, the money is not wasted: ask for a landing, not more work
        if events is not None:  # (the money itself is the session's ledger row)
            events.emit(
                "generate.turn_cap",
                label=task.label,
                round=task.round,
                max_turns=job.max_turns,
                turns=acc.turns,
                session_usd=round(acc.usage.cost_usd, 4),
            )
        wrap = job.model_copy(
            update={"prompt": WRAPUP_PROMPT + prompt, "max_turns": DEFAULT_WRAPUP_TURNS}
        )
        res = acc.run(agent, wrap, wrapup=True, optional=True) or res
        changes = res.files_changed or changes or _attributed_fallback(ws, task, before)

    if (
        not changes
        and not acc.wrapped
        and res.exit_reason not in ("timeout", "budget")
        # a provider failure or a spent usage limit is not a bail: a second session meets the
        # same wall (the storm fallback and the round loop's one retry handle a transient)
        and not (res.transient or res.quota)
    ):
        if events is not None:
            events.emit("generate.silent_bail", label=task.label, exit_reason=res.exit_reason)
        job2 = job.model_copy(
            update={
                "prompt": (
                    "Your previous attempt ended WITHOUT writing any file. You must create/edit the files "
                    "described below and run the build tool before finishing.\n\n" + prompt
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
        transient=bool(res.transient),
        quota=bool(res.quota),
        storm=not changes and bool(res.transient),
    )


class _SessionAcc:
    """Runs the agent sessions of ONE task and keeps their accounting.

    Each session is a ledger row the moment it returns (label ``<task>``/``<task>.a2`` for
    the retry, ``<task>.wrapup`` for the landing session — ``cost.instrument.MeteredAgent``),
    so a crash in a later session can never erase what an earlier one already spent — the
    ``.a2`` hole of docs/COST.md §6.  The run's clock is checked after each session that
    did any work, as the guard's charge did before money left it."""

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
        if self.budget is not None and (res.usage.cost_usd or res.usage.input_tokens or res.usage.output_tokens):
            self.budget.check()
        return res


def _is_budget_stop(e: BaseException) -> bool:
    from codeverse3d.orchestrator import BudgetExceeded

    return isinstance(e, BudgetExceeded)


def session_turns(res: Any) -> int:
    """``AgentResult.turns`` — the count as the BACKEND reports it (0 for gemini-cli,
    which exposes none).  Backends count turns slightly differently, so this is a
    size signal (the ``generate.turn_cap`` event), not the number the turn cap is compared
    against — that one is enforced inside the session by ``job.max_turns``.  (Until
    2026-08-29 this re-read result.json for a ``turns`` key only the deleted api-agent
    wrote, so ``agent_turns`` was 0 for every vendor CLI.)"""
    return int(getattr(res, "turns", 0) or 0)


def _images_block(images: list[ImagePart], ws: Workspace) -> str:
    """The task's images (reference photos, the judged contact sheet) as a prompt
    section.  No vendor CLI takes an image on argv, so the agent opens the files with
    its own file/image tools; until 2026-08-29 the only carrier was ``AgentJob.images``,
    which no backend read, so the contact sheet the judge scored reached no CLI session
    (that field is gone since 2026-08-30 — this block IS the delivery).

    Every image is copied into ``.3dcode/images/`` first, exactly as the cookbook is (D9),
    and listed workspace-relative: a CLI reads only INSIDE its workspace (``--image
    ref.png`` stores the host's absolute path), and the judged contact sheet sits under
    ``artifacts/renders/``, which ``.geminiignore`` hides from gemini-cli's read_file."""
    paths: list[tuple[str, str]] = []
    for i in images:
        if not i.path:
            continue
        src = Path(i.path)
        dest = ws.root / IMAGES_DIR / f"{len(paths):02d}_{src.name}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        except OSError as e:  # unreadable source: naming it is still better than silence
            log.warning("task image %s could not be copied into %s: %s", src, dest, e)
            paths.append((i.label or "image", str(src)))
            continue
        paths.append((i.label or "image", dest.relative_to(ws.root).as_posix()))
    if not paths:
        return ""
    lines = ["", "", "## Images for this task",
             "They are NOT attached to this message: open each file with your image/file-reading tool.",
             "Paths are relative to the workspace root."]
    lines += [f"- {label}: `{path}`" for label, path in paths]
    return "\n".join(lines)


def _spec_lang_track(ws: Workspace) -> tuple[str, str]:
    """Best-effort ``(language, track)`` from the workspace's spec.json ("" when absent)."""
    spec_d = read_json_or_none(ws.spec_path) or {}
    return str(spec_d.get("language", "")), str(spec_d.get("track", ""))


def _always_writable(language: str, task: GenerationTask) -> list[str]:
    """The entry file, exempt from ``edit_only`` — but only for a task that OWNS it.

    Owning tasks: ``owns_entry=True`` (assembly / whole-object / single-file tasks, and
    scoped refines whose prompt promises entry access) or a ``files_hint`` naming the
    entry.  Part / zone / detail / asset tasks own nothing here — under the old
    unconditional exemption, parallel scoped sessions raced on the entry file unopposed."""
    from codeverse3d.contracts.common import ENTRY_FILE, Language

    try:
        entry = ENTRY_FILE[Language(language)]
    except (ValueError, KeyError):
        return []
    return [entry] if task.owns_entry or entry in task.files_hint else []


def _envelope_scope(ws: Workspace, task: GenerationTask) -> tuple[set[str] | None, list[str]]:
    """``write_files``'s ``only`` (an ``edit_only`` task: files_hint + the entry when
    owned) and ``frozen`` (the language's harness-owned files) for a single-shot task."""
    language, _ = _spec_lang_track(ws)
    frozen = _read_only(language)
    if not task.edit_only or not task.files_hint:
        return None, frozen
    return {f for f in task.files_hint if f} | set(_always_writable(language, task)), frozen


def _read_only(language: str) -> list[str]:
    """The harness-owned files of the language (``src/recipes.glsl`` for glsl_shader): every session
    of the run — baseline, refine, repair — may read them, none may write them."""
    from codeverse3d.contracts.common import HARNESS_OWNED_SRC, Language

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
        from codeverse3d.agents.cli_common import attribute_changes
    except ImportError:  # pragma: no cover — agents package always ships with tracks
        return raw
    return attribute_changes(raw, write_roots=list(task.write_roots))


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
) -> GenerationResult:
    """Dispatch to the single-shot or the coding-agent strategy by ``agent_id``.

    ``agent`` / ``model`` may be injected (tests, reuse); otherwise they are
    resolved lazily through ``codeverse3d.agents`` / ``codeverse3d.models``.
    """
    if is_single_shot(agent_id):
        if model is None:
            from codeverse3d.models import get_chat_model

            model = get_chat_model(single_shot_model_id(agent_id))
        return generate_files(
            ws,
            model=model,
            task=task,
            budget=budget,
            events=events,
            allowed_roots=tuple(r.rstrip("/") + "/" for r in task.write_roots),
        )
    if agent is None:
        from codeverse3d.agents import get_coding_agent

        agent = get_coding_agent(agent_id)
    return run_agent_task(
        ws,
        agent=agent,
        task=task,
        settings=settings,
        budget=budget,
        events=events,
    )
