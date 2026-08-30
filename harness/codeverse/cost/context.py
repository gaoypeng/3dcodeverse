"""Where a call sits in a run — the attribution the ledger needs.

A :class:`CallContext` answers "which run, which round, which stage, which
role" for the call that is about to happen.  Precedence:

1. what the caller passed explicitly (a judge knows it is judging) — always wins;
2. a label that names a **job of its own** (``judge:…``, ``planner``,
   ``texture_gate``, ``caption…``, ``pairwise:…`` — :data:`SELF_DESCRIBING`).
   Such a call is not the work of whatever agent session happens to surround it,
   so it outranks the ambient context.  This is the fix for a measured
   mis-attribution: an in-process tool that bills a model *inside* an agent
   session (a texture pass, a captioner, a judge invoked as a tool) was filed
   under the session's ``stage=refine / role=generator``;
3. the ambient context (:func:`call_context`) — set by
   :mod:`codeverse.cost.instrument` around an agent session.  It beats a
   *generation* label because the session knows more than a call made inside it
   does: a best-of-N candidate session runs with ``job.kind="candidate"`` while a
   tool it calls bills a model under a plain ``baseline`` label;
4. what is left of the label (a generation stage, the round tag) — which also
   survives a thread hop that a ``ContextVar`` does not.

A label that names nothing (:data:`~codeverse.cost.types.Stage.OTHER`) states
nothing and never displaces anything.

The **run binding** (:func:`bound_run`, the run slug) is a plain ``ContextVar``.
A bench worker that opens its own run in its own thread gets its own binding, and a
worker thread inherits its caller's because ``codeverse.proc.fan_out`` copies the
context (bb6179c).  There is deliberately no process-wide fallback: restoring one by
value republished a sibling's run the moment the first parallel run exited, and rows
written afterwards landed in that finished run's file (2026-08-30 — the interleave
1756866 deleted the ``_active_ledgers`` global for, left standing on this half).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

from codeverse.contracts.common import Usage
from codeverse.cost.types import Role, Stage, role_for_stage, stage_for_label


@dataclass(frozen=True)
class CallContext:
    """Attribution for one call (all fields optional; ``None`` = "not stated")."""

    run: str = ""
    round: int | None = None
    stage: Stage | None = None
    role: Role | None = None
    label: str = ""

    def merge(self, other: CallContext) -> CallContext:
        """``other`` wins field by field where it says something."""
        return CallContext(
            run=other.run or self.run,
            round=self.round if other.round is None else other.round,
            stage=other.stage or self.stage,
            role=other.role or self.role,
            label=other.label or self.label,
        )

    def resolved(self) -> CallContext:
        """Fill role from stage (and stage from label) so the row is never ``other``
        when something better can be derived."""
        stage = self.stage or (stage_for_label(self.label) if self.label else None)
        role = self.role or (role_for_stage(stage) if stage else None)
        return replace(self, stage=stage, role=role)


_EMPTY = CallContext()
_ctx: ContextVar[CallContext] = ContextVar("cv3d_cost_ctx", default=_EMPTY)

#: the run this execution context belongs to ("" = nothing bound here)
_run_var: ContextVar[str] = ContextVar("cv3d_cost_run", default="")


@contextmanager
def bound_run(run: str) -> Iterator[None]:
    """Name the run every row written inside the block belongs to.

    Unwinds with the ``ContextVar`` token instead of re-setting the previous value:
    two of these open at once (``bench.run_bench``'s pool) must undo independently
    — see the module docstring."""
    token = _run_var.set(run or "")
    try:
        yield
    finally:
        _run_var.reset(token)


def run_binding() -> CallContext:
    """The run bound in this execution context (``run=""`` when none is)."""
    return CallContext(run=_run_var.get())


def current() -> CallContext:
    """Run binding merged with the ambient (thread-local) context."""
    return run_binding().merge(_ctx.get())


@contextmanager
def call_context(
    *,
    run: str = "",
    round: int | None = None,  # noqa: A002 - matches the record/round vocabulary
    stage: Stage | str | None = None,
    role: Role | str | None = None,
    label: str = "",
) -> Iterator[CallContext]:
    """Set the ambient attribution for the duration of the block (this thread).

    Used by :mod:`codeverse.cost.instrument` around an agent session and by the
    judges around a verdict; nesting merges, so an inner block only has to name
    what it changes."""
    ctx = _ctx.get().merge(CallContext(run=run, round=round, stage=_stage(stage), role=_role(role), label=label))
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


def _stage(value: Stage | str | None) -> Stage | None:
    if value is None or isinstance(value, Stage):
        return value
    try:
        return Stage(value)
    except ValueError:
        return stage_for_label(str(value))


def _role(value: Role | str | None) -> Role | None:
    if value is None or isinstance(value, Role):
        return value
    try:
        return Role(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------- labels
#: request-label prefix → (stage, role).  Longest prefix wins; anything else is
#: routed through ``stage_for_label`` (which understands generation labels).
_LABEL_HINTS: tuple[tuple[str, Stage, Role], ...] = (
    ("planner", Stage.PLAN, Role.PLANNER),
    ("plan", Stage.PLAN, Role.PLANNER),
    ("judge", Stage.JUDGE, Role.JUDGE),
    ("pairwise", Stage.PAIRWISE, Role.JUDGE),
    ("texture_gate", Stage.TEXTURE, Role.JUDGE),
    ("texture", Stage.TEXTURE, Role.OTHER),
    ("scene_texture", Stage.TEXTURE, Role.OTHER),
    ("caption", Stage.CAPTION, Role.CAPTIONER),
    ("calibrate", Stage.JUDGE, Role.JUDGE),
)


def context_from_label(label: str) -> CallContext:
    """Best-effort attribution from a ``ChatRequest.label``: its head names the job
    (``judge:…``, ``texture_plan``, a generation stage) and the judge's
    ``…:r<NN>:s<k>`` tag carries the round."""
    low = (label or "").strip()
    if not low:
        return _EMPTY
    round_index = _round_from_label(low)
    head = low.split(":", 1)[0].lower()
    best: tuple[int, Stage, Role] | None = None
    for prefix, stage, role in _LABEL_HINTS:
        if head.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), stage, role)
    if best is not None:
        return CallContext(round=round_index, stage=best[1], role=best[2], label=low)
    return CallContext(round=round_index, stage=_stated(stage_for_label(head)), label=low)


def _stated(stage: Stage | None) -> Stage | None:
    """``Stage.OTHER`` is "the label told us nothing" — not a statement, so it must
    not outrank the ambient session context."""
    return None if stage is Stage.OTHER else stage


def _round_from_label(label: str) -> int | None:
    """``judge:static_object_v1:r02:s1`` → 2 (``None`` when the label has no round tag)."""
    for token in label.replace("/", ":").split(":"):
        t = token.strip()
        if len(t) >= 2 and t[0] == "r" and t[1:].isdigit():
            return int(t[1:])
    return None


#: Stages a label can name that describe a job of their own.  A call whose label
#: says "judge" / "plan" / "texture" / "caption" / "pairwise" is doing that job
#: even when an agent session surrounds it, so it outranks the ambient context;
#: a generation stage in a label does not (see the module docstring).
SELF_DESCRIBING: frozenset[Stage] = frozenset(
    {Stage.PLAN, Stage.JUDGE, Stage.PAIRWISE, Stage.TEXTURE, Stage.CAPTION})


def attribute(explicit: CallContext | None = None, *, label: str = "") -> CallContext:
    """The final attribution for one call.

    Precedence (see the module docstring): explicit > a label that names a job of
    its own > the ambient session context > the rest of the label.  The row's
    ``label`` is always the most specific one available."""
    ctx = current()
    if label:
        from_label = context_from_label(label)
        ctx = ctx.merge(from_label) if from_label.stage in SELF_DESCRIBING else from_label.merge(ctx)
    if explicit is not None:
        ctx = ctx.merge(explicit)
    if label and not (explicit is not None and explicit.label):
        ctx = replace(ctx, label=label)
    return ctx.resolved()


# --------------------------------------------------------------------------- per-attempt sink
@dataclass(frozen=True)
class AttemptRecord:
    """One round-trip of one logical model call (``models.retry.rotate_with_retries``).

    The metering layer (``instrument.MeteredChatModel``) installs a sink for these
    with :func:`attempt_recording`; the backend reports every round-trip — the
    winner included — so a hedge loser's and a charged-but-invalid reply's tokens
    reach the ledger instead of vanishing (audit 2026-08-27).  Rows written from
    these carry ``source="attempt"`` and are excluded from every total."""

    attempt: int  #: 1-based issue order within the logical call
    key: str  #: the full API key that served it; the ledger keeps only its last 4 chars
    outcome: str  #: the KeyPool report vocabulary (``models.retry``): ok | 429 | 5xx | error | dead, plus whatever the pool adds
    discarded: bool  #: True for every round-trip that is not the winning one
    usage: Usage  #: what the provider billed for THIS round-trip
    error: str = ""  #: str(ModelError) when the round-trip failed


AttemptSink = Callable[[AttemptRecord], None]

#: Request-scoped sink for per-attempt rows.  A ContextVar rather than a new
#: parameter because ``ChatModel.generate(request)`` is a protocol many backends
#: implement; the backend captures the sink ONCE at call entry, so a hedge loser
#: that lands later in its own thread still reports through the captured callable.
_attempt_sink: ContextVar[AttemptSink | None] = ContextVar("cv3d_cost_attempt_sink", default=None)


def attempt_sink() -> AttemptSink | None:
    """The sink installed for the current logical call (``None`` = nobody meters)."""
    return _attempt_sink.get()


@contextmanager
def attempt_recording(sink: AttemptSink) -> Iterator[None]:
    """Install ``sink`` for the duration of ONE logical ``generate`` call."""
    token = _attempt_sink.set(sink)
    try:
        yield
    finally:
        _attempt_sink.reset(token)


__all__ = ["AttemptRecord", "AttemptSink", "CallContext", "attempt_recording", "attempt_sink",
           "attribute", "bound_run", "call_context", "context_from_label", "current",
           "run_binding"]
