"""Orchestration: the stage runner with resume, run state, the round policy
(RoundPolicy), refine-task compilation and the budget.

One module since 2026-08-28: the seven-file package predates the 1,500-line cap,
nobody ever imported the package itself, and refine_tasks existed only to be
re-exported by rounds.  candidates.py moved into tracks/candidates.py earlier.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from codeverse3d.contracts.artifacts import GateReport, Judgment
from codeverse3d.contracts.common import Budget, Usage
from codeverse3d.contracts.plan import AcceptanceItem, Plan
from codeverse3d.contracts.run import RoundRecord, RunStatus
from codeverse3d.conventions import to_snake
from codeverse3d.cost.billing import bills_usd
from codeverse3d.proc import EventLog
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


# ===================================================================== state
class StageState(BaseModel):
    """One completed stage: inputs hash + where its result JSON lives."""

    name: str
    inputs_hash: str
    result_path: str = ""
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_s: float = 0.0


class RunState(BaseModel):
    """Everything the orchestrator needs to resume a run — except the round history, which is
    the round journal's alone (``rounds/rNN.json`` + each round's commit; ``lifecycle.reconcile_resume``
    reads it).  A state saved before 2026-09-22 also caches that journal (``completed_rounds`` /
    ``round_commits`` / ``current_round``) and names a best round (``best_round`` / ``best_commit`` /
    ``best_score`` / ``best_considered_through``): unknown keys, ignored — a resume goes on from the
    journal's LAST round."""

    status: RunStatus = RunStatus.PLANNING
    stages: dict[str, StageState] = Field(default_factory=dict)
    materialized_for: str = Field(default="", description="agent kind the workspace was materialised for")
    stop_reason: str = ""
    error: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extra: dict[str, Any] = Field(default_factory=dict)

    # ----------------------------------------------------------------- persistence
    @classmethod
    def load(cls, ws: Workspace) -> RunState | None:
        """Return the saved state or ``None`` when the file is absent."""
        if not ws.state_path.is_file():
            return None
        try:
            return cls.model_validate(ws.read_json(ws.state_path))
        except (ValidationError, ValueError) as e:
            raise StateCorrupt(f"{ws.state_path}: {e}") from e

    @classmethod
    def load_or_new(cls, ws: Workspace, *, resume: bool) -> RunState:
        if resume:
            st = cls.load(ws)
            if st is not None:
                return st
        return cls()

    def save(self, ws: Workspace) -> None:
        self.updated_at = datetime.now(UTC)
        ws.write_json(ws.state_path, self)


class StateCorrupt(RuntimeError):
    """``run_state.json`` exists but does not validate — refuse to guess."""


# ===================================================================== runner

T = TypeVar("T")

_SAFE = re.compile(r"[^0-9A-Za-z._-]+")


def hash_inputs(inputs: Any) -> str:
    """Stable sha256 (12 hex) of arbitrary JSON-able inputs (pydantic ok)."""
    payload = _jsonable(inputs)
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return obj


class StageRunner:
    """Runs stages with resume semantics on top of ``RunState`` + ``EventLog``."""

    def __init__(self, ws: Workspace, events: EventLog, state: RunState | None = None):
        self.ws = ws
        self.events = events
        self.state = state if state is not None else RunState()
        #: the scene baseline fans sibling stages out in parallel; the bookkeeping
        #: (``state.stages`` + the ``run_state.json`` save) must not interleave
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- paths
    def result_path(self, name: str) -> Path:
        return self.ws.root / "stages" / f"{_SAFE.sub('_', name)}.json"

    # ----------------------------------------------------------------- api
    def stage(
        self,
        name: str,
        fn: Callable[[], T],
        *,
        inputs: Any = "",
        model: type[BaseModel] | None = None,
    ) -> T:
        """Run ``fn`` unless a cached result for the same ``inputs`` exists."""
        h = hash_inputs(inputs)
        path = self.result_path(name)
        prior = self.state.stages.get(name)
        if prior is not None and prior.inputs_hash == h and path.is_file():
            # A cached result that cannot be read back is a cache MISS, not a dead run.
            # ``inputs_hash`` covers the INPUTS only, never the result model's schema, so a
            # contract that gained a field invalidates nothing — and a clobbered file
            # invalidates nothing either.  Both used to escape as ValidationError /
            # JSONDecodeError through BaseTrack.run, which marks the run FAILED and
            # re-raises, so every later `3dcode resume <slug>` died the same way with no way
            # out (there is no flag to drop a cached stage).  Re-run instead and
            # overwrite the file — the same tolerance load_ledger and _read_jsonl apply.
            try:
                result = _revive(json.loads(path.read_text()), model)
            except (OSError, ValueError) as e:  # ValidationError is a ValueError
                self.events.emit("stage.cache_invalid", stage=name, inputs_hash=h, path=str(path),
                                 error=f"{type(e).__name__}: {e}")
                log.warning("stage %s: cached result at %s is unusable (%s); re-running", name, path, e)
            else:
                self.events.emit("stage.cached", stage=name, inputs_hash=h, path=str(path))
                return result  # type: ignore[return-value]

        self.events.emit("stage.start", stage=name, inputs_hash=h)
        t0 = time.time()
        try:
            result = fn()
        except Exception as e:
            self.events.emit("stage.failed", stage=name, error=f"{type(e).__name__}: {e}",
                             duration_s=round(time.time() - t0, 2))
            raise
        dt = time.time() - t0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ws.write_json(path, {"stage": name, "inputs_hash": h, "result": _jsonable(result)})
        with self._lock:
            self.state.stages[name] = StageState(name=name, inputs_hash=h, result_path=str(path), duration_s=dt)
            self.state.save(self.ws)
        self.events.emit("stage.done", stage=name, inputs_hash=h, duration_s=round(dt, 2))
        return result

    def is_done(self, name: str, inputs: Any = "") -> bool:
        prior = self.state.stages.get(name)
        return prior is not None and prior.inputs_hash == hash_inputs(inputs) and self.result_path(name).is_file()


def _revive(data: dict[str, Any], model: type[BaseModel] | None) -> Any:
    result = data.get("result")
    if model is not None and isinstance(result, dict):
        return model.model_validate(result)
    return result


# ===================================================================== refine_tasks


class RefineTask(BaseModel):
    """One targeted change request compiled for the next round."""

    target: str = Field(description="part / zone / asset / joint / camera / 'overall'")
    kind: str = Field(description="geometry | assembly | material | articulation | bug | acceptance | ...")
    instruction: str
    priority: int = Field(ge=0, le=9, description="0 = gate error, 1 = failed must item, 2+ = judge")
    files: list[str] = Field(default_factory=list, description="files this task may edit ([] = unknown/whole)")
    source: Literal["gate", "acceptance", "judge"] = "judge"

    def line(self) -> str:
        files = f" (files: {', '.join(self.files)})" if self.files else ""
        return f"[{self.source}/{self.kind}] {self.target}: {self.instruction}{files}"


class TaskGroup(BaseModel):
    """Tasks that must run together (they touch the same files)."""

    tasks: list[RefineTask]
    files: list[str] = Field(default_factory=list)

    @property
    def targets(self) -> list[str]:
        seen: list[str] = []
        for t in self.tasks:
            if t.target not in seen:
                seen.append(t.target)
        return seen

    @property
    def label(self) -> str:
        return "+".join(to_snake(t) for t in self.targets[:3]) or "refine"


FileForTarget = Callable[[str], list[str]]


def build_refine_instructions(
    judgment: Judgment | None,
    gates: Sequence[GateReport],
    acceptance_failures: Sequence[AcceptanceItem],
    plan: Plan | None,
    *,
    file_for_target: FileForTarget | None = None,
    max_tasks: int = 6,
    extra: Sequence[RefineTask] = (),
) -> list[RefineTask]:
    """Gate ERRORS first (with fix hints), then ``extra`` harness-derived tasks
    (e.g. reference-silhouette mismatch), then failed *must* acceptance items,
    then the judge's improvement plan; de-duplicated by (target, kind), capped.
    Gate/acceptance/extra tasks are never dropped by the cap unless they alone exceed it."""
    known = _known_targets(plan)
    tasks: list[RefineTask] = []
    seen: set[tuple[str, str]] = set()

    def _add(t: RefineTask) -> None:
        key = (to_snake(t.target), t.kind)
        if key in seen:
            return
        seen.add(key)
        t.target = _canon_target(t.target, known)
        if file_for_target is not None and not t.files:
            t.files = list(file_for_target(t.target))
        tasks.append(t)

    for g in gates:
        for f in g.errors:
            _add(RefineTask(target=f.target or "overall", kind=f"gate:{g.gate}", instruction=f.as_line(),
                            priority=0, source="gate"))
    for t in extra:
        _add(t.model_copy())
    for a in acceptance_failures:
        if a.priority != "must":
            continue
        _add(RefineTask(target=_acceptance_target(a, known), kind="acceptance",
                        instruction=f"Acceptance item {a.id} NOT met: {a.text}", priority=1,
                        source="acceptance"))
    if judgment is not None:
        for item in sorted(judgment.improvement_plan, key=lambda i: (i.priority, -i.expected_gain)):
            _add(RefineTask(target=item.target or "overall", kind=item.kind, instruction=item.instruction,
                            priority=min(9, 1 + item.priority), source="judge"))
    tasks.sort(key=lambda t: t.priority)
    if len(tasks) > max_tasks:
        protected = [t for t in tasks if t.source != "judge"]
        rest = [t for t in tasks if t.source == "judge"]
        tasks = (protected + rest)[:max(max_tasks, len(protected))]
    return tasks


def plan_parallel_groups(tasks: Sequence[RefineTask]) -> list[TaskGroup]:
    """Group tasks by file ownership.  Tasks with unknown files (``[]``) cannot
    be isolated, so if any exists everything collapses into ONE group (a single
    whole-object task).  Otherwise groups are the connected components of the
    "shares a file" relation — each group may run concurrently with the others."""
    if not tasks:
        return []
    if any(not t.files for t in tasks):
        files = sorted({f for t in tasks for f in t.files})
        return [TaskGroup(tasks=list(tasks), files=files)]
    parent = list(range(len(tasks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, t in enumerate(tasks):
        for f in t.files:
            if f in owner:
                parent[find(i)] = find(owner[f])
            else:
                owner[f] = i
    groups: dict[int, list[RefineTask]] = {}
    for i, t in enumerate(tasks):
        groups.setdefault(find(i), []).append(t)
    out = []
    for members in groups.values():
        files = sorted({f for t in members for f in t.files})
        out.append(TaskGroup(tasks=members, files=files))
    out.sort(key=lambda g: min(t.priority for t in g.tasks))
    return out


def plan_refine_groups(
    tasks: Sequence[RefineTask],
    *,
    allow_fanout: bool = True,
    parallel_min_tasks: int = 2,
) -> tuple[list[TaskGroup], bool]:
    """The ONE grouping rule of the refine scaffold (``BaseTrack.refine_tasks``).

    Groups by file ownership via :func:`plan_parallel_groups`; the groups run in
    parallel only when fan-out is allowed for the track, at least
    ``parallel_min_tasks`` file-disjoint groups exist and every group owns
    files.  Otherwise everything collapses into ONE whole-artifact group."""
    groups = plan_parallel_groups(tasks)
    parallel = allow_fanout and len(groups) >= parallel_min_tasks and all(g.files for g in groups)
    if not parallel:
        groups = [TaskGroup(tasks=list(tasks), files=sorted({f for t in tasks for f in t.files}))]
    return groups, parallel


def compact_instructions(tasks: Sequence[RefineTask], max_lines: int = 6) -> list[str]:
    """The instruction lines handed to ONE generation task: grouped by target
    (instances ``Leg_0``/``Leg_1`` fold into ``Leg``), ordered by priority, capped
    at ``max_lines``.  A flash-class agent given 12 near-identical connectivity
    errors fixes none; given "FrontLeg: (a) … (b) …" it fixes the part."""
    if not tasks:
        return []
    groups: dict[str, list[RefineTask]] = {}
    for t in sorted(tasks, key=lambda t: t.priority):
        groups.setdefault(_instance_base(t.target), []).append(t)
    lines: list[str] = []
    for target, members in groups.items():
        if len(members) == 1:
            lines.append(members[0].line())
            continue
        bits = []
        for i, t in enumerate(members):
            who = f" [{t.target}]" if t.target != target else ""
            bits.append(f"({chr(97 + i % 26)}){who} [{t.source}/{t.kind}] {t.instruction}")
        files = sorted({f for t in members for f in t.files})
        tail = f" (files: {', '.join(files)})" if files else ""
        lines.append(f"{target}: " + "; ".join(bits) + tail)
    if len(lines) > max_lines:
        dropped = len(lines) - max_lines
        lines = lines[:max_lines]
        log.info("compact_instructions: %d target group(s) deferred to a later round", dropped)
    return lines


# ----------------------------------------------------------------------------- helpers
def _split_instance(target: str) -> tuple[str, str]:
    """``BackLeg_1`` → (``BackLeg``, ``1``) per the ``Name_0..N-1`` instance naming
    convention; anything else → (target, "")."""
    base, sep, suffix = (target or "").rpartition("_")
    if sep and base and suffix.isdigit():
        return base, suffix
    return target or "overall", ""


def _instance_base(target: str) -> str:
    return _split_instance(target)[0]


def _known_targets(plan: Plan | None) -> dict[str, str]:
    """snake → canonical name for every part/zone/asset/joint/camera in the plan."""
    names: dict[str, str] = {}
    if plan is None:
        return names
    for attr in ("parts", "joints", "zones", "assets", "cameras", "effects"):
        for item in getattr(plan, attr, None) or []:
            names[to_snake(item.name)] = item.name
    return names


def _canon_target(target: str, known: dict[str, str]) -> str:
    """Plan name for ``target``; instance suffixes (``Leg_1``, ``leg.2``) keep the
    suffix but take the plan's spelling of the base name."""
    hit = known.get(to_snake(target))
    if hit is not None:
        return hit
    base, suffix = _split_instance(target)
    if suffix and to_snake(base) in known:
        return f"{known[to_snake(base)]}_{suffix}"
    snake = to_snake(target)
    if snake.endswith("s") and snake[:-1] in known:  # judges say "Legs" for the plan's "Leg"
        return known[snake[:-1]]
    return target


def _acceptance_target(item: AcceptanceItem, known: dict[str, str]) -> str:
    """Route a failed acceptance item to the part it names, else 'overall'."""
    words = item.text.lower()
    for snake, name in known.items():
        if snake.replace("_", " ") in words or snake in words:
            return name
    return "overall"


# ===================================================================== rounds
@dataclass(frozen=True)
class RoundPolicy:
    """The refine loop's knobs.  There is no stop knob besides ``max_rounds``: a run is the
    baseline plus ``max_rounds`` refine rounds, cut short only by the clock or a hard
    failure (owner, 2026-09-22 — the pass / plateau / regression / diminishing-returns
    stops, the rewrite and the surface-detail rounds they drove are gone)."""

    max_rounds: int = 4  # refine rounds AFTER the baseline
    max_refine_tasks: int = 6
    max_instructions_per_task: int = 6  # lines handed to ONE generation task (grouped by target)
    parallel_min_tasks: int = 2  # fan out only when >= this many file-disjoint groups
    n_candidates: int = 1  # best-of-N baseline (candidates generated in parallel, best kept)
    judge_samples: int = 1  # VLM judge samples per round (flash: std ≈ 0.001 between samples → 1 is enough)


def gate_error_count(r: RoundRecord) -> int:
    return sum(len(g.errors) for g in r.gates)


# ===================================================================== budget

#: stage label used when a caller does not say where the money went
OTHER_STAGE = "other"


class BudgetExceeded(RuntimeError):
    """Raised when a run crosses its USD or wall-clock ceiling."""

    def __init__(self, reason: str, *, spent_usd: float):
        super().__init__(reason)
        self.reason = reason
        self.spent_usd = spent_usd


class BudgetGuard:
    """Thread-safe accumulator of ``Usage`` against a ``Budget``.

    ``charge`` adds usage and then calls ``check``; ``check`` raises
    ``BudgetExceeded`` when ``max_minutes`` is exceeded.  Cost is accumulated for the
    ledger and the run record; the wall clock is the ceiling.
    """

    def __init__(
        self,
        budget: Budget,
        start_time: float | None = None,
        *,
        soft_fraction: float = 1.0,
    ):
        self.budget = budget
        self.start_time = time.time() if start_time is None else start_time
        #: ACTIVE seconds carried over from previous sessions of this run (``restore``
        #: sets it from the snapshot); downtime between sessions never counts.
        self._active_s: float = 0.0
        self.spent = Usage()
        #: the share of ``spent.cost_usd`` that is money someone is actually charged.
        #: The ceilings enforce THIS, not ``spent.cost_usd`` — a backend on a flat-rate
        #: subscription prices its tokens notionally and must not consume a spend guard.
        #: See ``cost/billing.py`` for why, and what still bounds a subscription run.
        self.billed_usd = 0.0
        self._lock = threading.Lock()
        self.calls = 0
        #: fraction of the hard ceilings the *baseline* may use (1.0 = no soft cap)
        self.soft_fraction = min(1.0, max(0.0, float(soft_fraction)))
        #: one-off extension of the HARD wall-clock ceiling (finalise/salvage headroom)
        self.grace_minutes = 0.0
        #: stage -> USD, so a stop can say where the money went
        self.by_stage: dict[str, float] = {}

    # ----------------------------------------------------------------- accounting
    def charge(self, usage: Usage | None, *, stage: str = OTHER_STAGE, enforce: bool = True) -> None:
        """THE door every dollar goes through — for bucketing and enforcement only.

        Accumulates ``usage``, buckets it by ``stage`` and — when ``enforce`` — raises
        ``BudgetExceeded`` if a hard ceiling is now crossed.
        The ledger row is NOT written here: ``cost.instrument`` (MeteredAgent /
        MeteredChatModel) is the one writer, one priced row per real call.  Until
        2026-08-29 this method kept a second, aggregate writer that fired whenever no
        ``run_ledger()`` was active — a direct ``track.run()`` without the CLI, or any
        run with ``C3D_COST_LEDGER=off`` (the guard's own ledger was opened
        unconditionally), so ledger-off runs of that era carry one unpriced row per
        charge; a ledger-off run now writes nothing at all.  Role, label and round
        went with that second writer (2026-08-30): ``CallCost`` carries all three per
        call, so the guard bucketing them a second time fed nothing but itself."""
        if usage is None:
            return
        with self._lock:
            self.spent = self.spent + usage
            if bills_usd(getattr(usage, "backend", None)):
                self.billed_usd += float(usage.cost_usd)
            self.calls += 1
            key = stage or OTHER_STAGE
            self.by_stage[key] = self.by_stage.get(key, 0.0) + float(usage.cost_usd)
        if enforce:
            self.check()

    def add(self, usage: Usage | None, *, stage: str = OTHER_STAGE) -> None:
        """Account for ``usage`` WITHOUT enforcing the ceilings.

        For work that is already done and persisted (a completed judge verdict,
        a pairwise tie-break, the texture pass): the money is spent either way,
        and raising here would throw away a finished, paid-for result.  The
        round loop stops at its next ``ok()`` check instead.  The dollar is
        still bucketed — "not enforced" never means "not seen"."""
        self.charge(usage, stage=stage, enforce=False)

    def mark(self) -> Usage:
        """Snapshot of the running total — diff it with :func:`usage_delta` to see
        what a round burned even when the round itself raised half-way."""
        return self.spent.model_copy(deep=True)

    # ----------------------------------------------------------------- resume snapshot
    def snapshot(self) -> BudgetSnapshot:
        """Point-in-time guard state for ``run_state.json`` so a resume continues the
        SAME budget instead of a fresh one.  Grace and config are deliberately absent:
        grace is one attempt's salvage headroom — persisting it would ratchet the hard
        ceiling run over run, and the ceilings always come from the current spec."""
        with self._lock:
            return BudgetSnapshot(
                spent=self.spent.model_copy(deep=True),
                billed_usd=self.billed_usd,
                calls=self.calls,
                by_stage=dict(self.by_stage),
                active_s=self._active_s + (time.time() - self.start_time),
            )

    def restore(self, snap: BudgetSnapshot) -> None:
        """Adopt a snapshot: money, calls and buckets keep counting; ``start_time``
        stays *now*, so ``elapsed_minutes`` is prior ACTIVE seconds plus this session
        — never the downtime in between.  The ceilings come from the (possibly raised)
        spec budget, so a raised cap (``--max-minutes`` / ``--rounds``) grants exactly
        the difference, never a fresh full cap."""
        with self._lock:
            self.spent = snap.spent.model_copy(deep=True)
            self.billed_usd = float(snap.billed_usd)
            self.calls = int(snap.calls)
            self.by_stage = dict(snap.by_stage)
            self._active_s = float(snap.active_s)
            self.start_time = time.time()

    def elapsed_minutes(self) -> float:
        """Cumulative ACTIVE minutes: prior sessions' seconds (restored from the
        snapshot) plus this session's wall clock.  Downtime costs nothing."""
        return (self._active_s + (time.time() - self.start_time)) / 60.0

    # ----------------------------------------------------------------- ceilings
    @property
    def hard_minutes(self) -> float:
        return self.budget.max_minutes + self.grace_minutes

    def grant_grace(self, *, minutes: float = 0.0) -> None:
        """Extend the HARD wall-clock ceiling (never shrink it).  Used once by finalise /
        salvage so a run that tripped the clock mid-stage can still deliver a judged
        round instead of no score at all."""
        with self._lock:
            self.grace_minutes = max(self.grace_minutes, max(0.0, float(minutes)))

    def check(self) -> None:
        """Raise ``BudgetExceeded`` if any hard ceiling has been crossed."""
        spent = self.billed_usd
        elapsed = self.elapsed_minutes()
        if elapsed > self.hard_minutes:
            raise BudgetExceeded(
                f"elapsed {elapsed:.1f} min exceeds max_minutes {self.hard_minutes:.1f}",
                spent_usd=spent,
            )

    # ----------------------------------------------------------------- soft cap
    def soft_minutes(self) -> float:
        """The wall clock the soft sub-budget allows (grace is hard-only)."""
        return self.budget.max_minutes * self.soft_fraction

    def soft_exceeded(self) -> str:
        """Reason string when the soft sub-budget is used up, else ``""``."""
        minutes = self.soft_minutes()
        elapsed = self.elapsed_minutes()
        if elapsed > minutes:
            return f"elapsed {elapsed:.1f} min exceeds soft cap {minutes:.1f} min ({self.soft_fraction:.0%} of {self.budget.max_minutes:.1f})"
        return ""

    def soft_ok(self) -> bool:
        """True while the baseline still has its share of the budget."""
        return not self.soft_exceeded()

    def ok(self) -> bool:
        """True when no ceiling is crossed (non-raising variant of ``check``)."""
        try:
            self.check()
        except BudgetExceeded:
            return False
        return True

    def timeout_s(self, want_s: float, *, floor_s: float = 60.0, soft: bool = True) -> int:
        """``want_s`` clipped to the wall-clock actually left (soft cap when ``soft``).

        A generation session must never be allowed to outlive the run's budget:
        the greenhouse scene lost 54 minutes to zone agents that kept working
        after the ceiling had already been crossed."""
        left = max(0.0, (self.soft_minutes() if soft else self.hard_minutes) - self.elapsed_minutes()) * 60.0
        return int(max(floor_s, min(float(want_s), left) if left > 0 else floor_s))

    def summary(self) -> dict[str, float | int]:
        return {
            "spent_usd": round(self.billed_usd, 4),
            # what it WOULD have cost at list price; equal to spent_usd unless a
            # subscription backend ran (cost/billing.py)
            "notional_usd": round(self.spent.cost_usd, 4),
            "elapsed_min": round(self.elapsed_minutes(), 2),
            "soft_fraction": round(self.soft_fraction, 3),
            "calls": self.calls,
            "input_tokens": self.spent.input_tokens,
            "output_tokens": self.spent.output_tokens,
        }

    def stage_summary(self) -> dict[str, float]:
        """``{stage: USD}`` over the whole run (what the ``cost.round`` events add up to)."""
        return {k: round(v, 6) for k, v in sorted(self.by_stage.items(), key=lambda kv: -kv[1])}


class BudgetSnapshot(BaseModel):
    """What survives a resume (``run_state.extra["budget_snapshot"]``).

    The four accumulator fields of :class:`BudgetGuard` plus cumulative ACTIVE
    seconds.  Grace (``grace_minutes``) and config (ceilings, soft fraction) are
    EXCLUDED on purpose: grace is per-attempt salvage headroom — persisting it
    would ratchet the hard ceiling — and config always comes from the current
    spec/settings."""

    spent: Usage
    billed_usd: float
    calls: int
    by_stage: dict[str, float]
    active_s: float


def usage_delta(after: Usage, before: Usage) -> Usage:
    """``after - before`` field by field (never negative).  Used to report what a
    round burned when it raised before it could fold its own usage together."""
    return Usage(
        backend=after.backend or before.backend,
        model=after.model or before.model,
        input_tokens=max(0, after.input_tokens - before.input_tokens),
        output_tokens=max(0, after.output_tokens - before.output_tokens),
        cached_tokens=max(0, after.cached_tokens - before.cached_tokens),
        thoughts_tokens=max(0, after.thoughts_tokens - before.thoughts_tokens),
        tool_calls=max(0, after.tool_calls - before.tool_calls),
        cost_usd=max(0.0, after.cost_usd - before.cost_usd),
        latency_ms=max(0, after.latency_ms - before.latency_ms),
    )
