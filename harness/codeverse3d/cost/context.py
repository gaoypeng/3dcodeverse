"""Where a call sits in a run: the attribution the ledger needs.

Precedence: explicit arguments > a label naming a job of its own (:data:`SELF_DESCRIBING`)
> the ambient context (:func:`call_context`) > the rest of the label.  The run binding
(:func:`bound_run`) is a plain ``ContextVar`` copied into fan-out workers, with no
process-wide fallback.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

from codeverse3d.cost.types import Role, Stage, role_for_stage, stage_for_label


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
        """Fill role from stage (and stage from label) so the row is never ``other`` needlessly."""
        stage = self.stage or (stage_for_label(self.label) if self.label else None)
        role = self.role or (role_for_stage(stage) if stage else None)
        return replace(self, stage=stage, role=role)


_EMPTY = CallContext()
_ctx: ContextVar[CallContext] = ContextVar("c3d_cost_ctx", default=_EMPTY)

#: the run this execution context belongs to ("" = nothing bound here)
_run_var: ContextVar[str] = ContextVar("c3d_cost_run", default="")


@contextmanager
def bound_run(run: str) -> Iterator[None]:
    """Name the run every row written inside the block belongs to (concurrent blocks unwind independently)."""
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
    """Set the ambient attribution for the block; nesting merges."""
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
#: request-label prefix → (stage, role); longest prefix wins, the rest goes through ``stage_for_label``.
_LABEL_HINTS: tuple[tuple[str, Stage, Role], ...] = (
    ("planner", Stage.PLAN, Role.PLANNER),
    ("plan", Stage.PLAN, Role.PLANNER),
    ("judge", Stage.JUDGE, Role.JUDGE),
    ("caption", Stage.CAPTION, Role.CAPTIONER),
    ("calibrate", Stage.JUDGE, Role.JUDGE),
)


def context_from_label(label: str) -> CallContext:
    """Best-effort attribution from a ``ChatRequest.label`` (head names the job, ``r<NN>`` the round)."""
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
    """``Stage.OTHER`` states nothing, so it must not outrank the ambient context."""
    return None if stage is Stage.OTHER else stage


def _round_from_label(label: str) -> int | None:
    """``judge:static_object:r02:s1`` → 2 (``None`` when the label has no round tag)."""
    for token in label.replace("/", ":").split(":"):
        t = token.strip()
        if len(t) >= 2 and t[0] == "r" and t[1:].isdigit():
            return int(t[1:])
    return None


#: Stages whose label names a job of its own: they outrank the ambient session context.
SELF_DESCRIBING: frozenset[Stage] = frozenset(
    {Stage.PLAN, Stage.JUDGE, Stage.CAPTION})


def attribute(explicit: CallContext | None = None, *, label: str = "") -> CallContext:
    """The final attribution for one call (precedence in the module docstring)."""
    ctx = current()
    if label:
        from_label = context_from_label(label)
        ctx = ctx.merge(from_label) if from_label.stage in SELF_DESCRIBING else from_label.merge(ctx)
    if explicit is not None:
        ctx = ctx.merge(explicit)
    if label and not (explicit is not None and explicit.label):
        ctx = replace(ctx, label=label)
    return ctx.resolved()


__all__ = ["CallContext", "attribute", "bound_run", "call_context", "context_from_label", "current",
           "run_binding"]
