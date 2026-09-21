"""Orchestration: the stage runner with resume, run state, the round loop
(RoundPolicy / StopPolicy / BestSelector), refine-task compilation and the budget.

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

from codeverse.contracts.artifacts import GateReport, Judgment
from codeverse.contracts.common import Budget, Usage
from codeverse.contracts.plan import AcceptanceItem, Plan
from codeverse.contracts.run import RoundRecord, RunStatus
from codeverse.conventions import to_snake
from codeverse.cost.billing import bills_usd
from codeverse.cost.routing import JUDGE_NOISE
from codeverse.proc import EventLog
from codeverse.workspace import Workspace

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
    """Everything the orchestrator needs to resume a run."""

    status: RunStatus = RunStatus.PLANNING
    stages: dict[str, StageState] = Field(default_factory=dict)
    current_round: int = Field(default=0, description="index of the round in progress / next to run")
    completed_rounds: list[int] = Field(default_factory=list)
    round_commits: dict[int, str] = Field(default_factory=dict)
    best_round: int | None = None
    best_commit: str = ""
    best_score: float | None = None
    #: highest round index that has been THROUGH best selection (choose_best_round,
    #: including its paid pairwise comparison).  Persisted so a resume can tell
    #: "the best was deliberately kept" from "this round was never considered" —
    #: without it, a state that merely LOOKS consistent with the journal kept a
    #: stale best and delivered the worse round (2026-08-27).  -1 = nothing yet.
    best_considered_through: int = -1
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

    # ----------------------------------------------------------------- helpers
    def mark_round_done(self, index: int, commit: str) -> None:
        if index not in self.completed_rounds:
            self.completed_rounds.append(index)
        self.round_commits[index] = commit
        self.current_round = index + 1

    def update_best(self, index: int, commit: str, score: float | None) -> bool:
        """Record ``index`` as best; returns True when it changed."""
        changed = self.best_round != index
        self.best_round, self.best_commit, self.best_score = index, commit, score
        return changed


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
StopReason = Literal["pass", "plateau", "budget", "continue", "max_rounds", "judge_unavailable",
                     "regression", "diminishing_returns", "agent_quota"]

#: what the next round should look like when the loop continues
Strategy = Literal["same", "switch", "detail"]

#: ``RoundRecord.kind`` of a round that already changed strategy after a regression
REWRITE_KIND = "rewrite"
#: ``RoundRecord.kind`` of the surface-detail round (see :func:`detail_round_due`)
DETAIL_KIND = "detail"

#: ``Strategy`` → the ``RoundRecord.kind`` the loop stamps on the round it starts (unknown → refine)
KIND_FOR_STRATEGY: dict[str, str] = {"same": "refine", "switch": REWRITE_KIND, "detail": DETAIL_KIND}

#: σ for a judge that is not in the measured table: the default judge's
#: (``gemini-3.1-pro-preview``, ``Settings.default_judge``), because that is what
#: an unnamed judge almost always is.  Calibrate a new judge
#: (``python -m codeverse.addons.calibration``) and add it to JUDGE_NOISE rather
#: than tuning the multipliers around it.
DEFAULT_JUDGE_SIGMA = 0.030


def judge_sigma(judge_model: str = "", *, default: float = DEFAULT_JUDGE_SIGMA) -> float:
    """Measured score noise (std) of one judge model.

    The numbers come from ``judges/calibration.py`` (n=3 on the e2e rounds) and
    are tabulated ONCE in :data:`codeverse.cost.routing.JUDGE_NOISE`; this is
    the only reader in the orchestrator.  Accepts a bare model name or a full
    backend id (``gemini:gemini-3.1-pro-preview``)."""
    name = (judge_model or "").strip().split(":")[-1]
    if not name:
        return default
    hit = JUDGE_NOISE.get(name)
    if hit is None:  # version suffixes: gemini-3.7-flash-002 → gemini-3.7-flash
        hit = next((v for k, v in JUDGE_NOISE.items() if name.startswith(k)), None)
    return float(hit[0]) if hit else default


@dataclass(frozen=True)
class RoundPolicy:
    """Calibrated knobs for the refine loop."""

    max_rounds: int = 4  # refine rounds AFTER the baseline
    plateau_window: int = 2  # consecutive scored rounds without min_delta gain
    min_delta: float = 0.02
    target: float = 0.8  # rubric pass threshold (overridden from the rubric when known)
    max_refine_tasks: int = 6
    max_instructions_per_task: int = 6  # lines handed to ONE generation task (grouped by target)
    parallel_min_tasks: int = 2  # fan out only when >= this many file-disjoint groups
    n_candidates: int = 1  # best-of-N baseline (candidates generated in parallel, best kept)
    pairwise_margin: float = 0.03  # |Δscore| below this = judge noise → pairwise tie-break
    pairwise_min_confidence: float = 0.6  # new round replaces best only when pairwise is this sure
    judge_samples: int = 1  # VLM judge samples per round (flash: std ≈ 0.001 between samples → 1 is enough)
    # ---- money stops (docs/COST.md §5); thresholds are multiples of the judge's measured σ
    judge_model: str = ""  # judge backend id → σ via judge_sigma(); "" = the default judge's σ
    regression_sigma: float = 1.0  # a round below (best − this × σ) regressed: change shape or stop
    marginal_sigma: float = 1.5  # from marginal_from_round on, the last gain must beat this × σ
    marginal_from_round: int = 3  # first refine round index the marginal-value test applies to
    # ---- agent session shape.  A 28-turn cap was TESTED AND REJECTED: a controlled
    # A/B on one prompt (n=3 per arm, same generator/judge/budget) cost $1.381 mean at
    # 0.479 mean score capped, against $1.360 at 0.684 uncapped — no saving and −0.205
    # score (docs/COST.md §17).  0 = no policy cap: the session runs under the backend's
    # own AgentJob.max_turns.  Callers who want one set it explicitly (cost profiles do).
    agent_max_turns: int = 0  # 0 = the backend's own AgentJob.max_turns (claude-code 60; the other vendors have no turn cap)
    agent_wrapup_turns: int = 6  # turns granted to land a final build + summary when a cap IS set
    # ---- depth.  Measured (wave "generation-depth"): across 88 consecutive refine-round pairs the
    # built part count changed ZERO times and mean Δgeometry_detail was +0.003 — the refine loop is
    # a repair loop and never adds anything.  The rounds that DID add geometry did it while assembly
    # was still broken and lost 0.075 of assembly_fit / 0.025 of overall for it.  So detail gets its
    # own round, and it only runs once the structure gates are clean.
    detail_rounds: int | None = None  # surface-detail rounds a run may spend: 0 = off, None = the track's own
    #                                   default (lifecycle.detail_round_budget; only tracks with the round get one)
    detail_min_score: float = 0.45  # below this the object is still wrong; detail would be polish on a mistake
    detail_bbox_tol_m: float = 0.005  # a detail round that moves a part box by more than this failed its brief

    @property
    def sigma(self) -> float:
        """Measured judge noise for this run's judge (see :func:`judge_sigma`)."""
        return judge_sigma(self.judge_model)

    @property
    def regression_delta(self) -> float:
        """How far below the best a round must score to count as a regression."""
        return self.regression_sigma * self.sigma

    @property
    def marginal_delta(self) -> float:
        """Gain the previous round must have produced for r03+ to be worth starting."""
        return self.marginal_sigma * self.sigma


def meaningful_regression(score: float | None, before: float | None, policy: RoundPolicy) -> bool:
    """Did ``score`` fall below ``before`` by MORE than this judge's own noise?

    THE regression predicate: ``StopPolicy._regression`` (the stop gate) and
    ``StopPolicy._regressions`` (the counter behind "a second regression stops
    the run") both call it, so they cannot drift apart.  The scale is the
    measured per-judge sigma table via ``policy.regression_delta`` — deliberately
    NOT the 0.202 A/A replay floor, which measures cross-run planner variance,
    not judge noise."""
    if score is None or before is None:
        return False
    return (before - score) > policy.regression_delta


@dataclass(frozen=True)
class StopDecision:
    """``StopPolicy``'s answer: stop (and why), or continue (and in what shape)."""

    reason: StopReason
    strategy: Strategy = "same"
    detail: str = ""

    @property
    def stop(self) -> bool:
        return self.reason != "continue"

    def event(self) -> dict[str, object]:
        return {"reason": self.reason, "strategy": self.strategy, "detail": self.detail}


class StopPolicy:
    """Decide whether the loop goes on.  ``history`` = rounds so far (baseline first)."""

    def __init__(self, policy: RoundPolicy):
        self.policy = policy

    def evaluate(self, history: Sequence[RoundRecord], *, budget_ok: bool = True) -> StopDecision:
        if not budget_ok:
            return StopDecision("budget")
        if not history:
            return StopDecision("continue")
        last = history[-1]
        if last.judgment is not None and last.judgment.passed and last.judgment.overall >= self.policy.target:
            return StopDecision("pass")
        refine_rounds = len(history) - 1
        if refine_rounds >= self.policy.max_rounds:
            return StopDecision("max_rounds")
        # Order is the economics.  A regression whose strategy switch is already spent
        # is the most specific answer there is, so it comes first.  Otherwise the
        # marginal-value test decides late rounds (at r03 no *shape* is worth $0.5),
        # and only before that does a first regression buy one change of shape.
        # Plateau is the fallback: the same money statement, less precisely.
        exhausted = self._regression(history, switch=False)
        if exhausted is not None:
            return self._maybe_detail(history, exhausted)
        marginal = self._diminishing(history)
        if marginal is not None:
            return self._maybe_detail(history, marginal)
        regressed = self._regression(history)
        if regressed is not None:
            return regressed
        if self._plateaued(history):
            return self._maybe_detail(history, StopDecision("plateau"))
        return StopDecision("continue")

    def _maybe_detail(self, history: Sequence[RoundRecord], stop: StopDecision) -> StopDecision:
        """Convert a "we are done repairing" stop into ONE surface-detail round.

        Only from a stop the loop was going to take anyway, so the detail round is
        never bought instead of a repair round — and only on a clean, best-scoring
        artifact, because the rounds that added geometry while assembly was still
        broken measurably lost score (module docstring / ``detail_rounds``)."""
        if stop.reason not in ("plateau", "diminishing_returns"):
            return stop
        why = detail_blocked(history, self.policy)
        if why:
            return stop
        return StopDecision("continue", strategy="detail",
                            detail=f"structure clean at r{history[-1].index:02d} → one surface-detail round "
                                   f"({stop.reason} otherwise)")

    # ---------------------------------------------------------------- money stops
    def _regression(self, history: Sequence[RoundRecord], *, switch: bool = True) -> StopDecision | None:
        """The last round scored below the best by more than the judge's own noise.

        Another round of the SAME shape is not justified by that evidence: the
        first regression buys one change of strategy (a whole-artifact rewrite),
        a second one — or one after a rewrite already failed to recover — stops
        the run.  ``switch=False`` asks only "is the switch already spent?", which
        is the question that outranks every other stop."""
        last = history[-1]
        score, best_before = last.score, best_score(history[:-1])
        if not meaningful_regression(score, best_before, self.policy):
            return None
        drop = best_before - score
        detail = (f"r{last.index:02d} scored {score:.3f} vs best {best_before:.3f} "
                  f"({-drop:+.3f}, judge σ {self.policy.sigma:.3f})")
        if last.kind == REWRITE_KIND or self._regressions(history) > 1:
            return StopDecision("regression", detail=detail + " — a changed strategy did not recover it")
        return StopDecision("continue", strategy="switch", detail=detail) if switch else None

    def _diminishing(self, history: Sequence[RoundRecord]) -> StopDecision | None:
        """From ``marginal_from_round`` on, only run when the last round actually
        bought something (> ``marginal_sigma`` × σ) and the target is still open."""
        nxt = len(history)
        if nxt < self.policy.marginal_from_round:
            return None
        gain = last_gain(history)
        best = best_score(history)
        need = self.policy.marginal_delta
        if gain is None:
            return None
        if gain <= need:
            return StopDecision("diminishing_returns",
                                detail=f"r{nxt:02d} not started: last gain {gain:+.3f} ≤ {need:.3f} "
                                       f"({self.policy.marginal_sigma:g} × judge σ {self.policy.sigma:.3f})")
        if best is not None and best >= self.policy.target:
            return StopDecision("diminishing_returns",
                                detail=f"r{nxt:02d} not started: best {best:.3f} already at target "
                                       f"{self.policy.target:.2f}")
        return None

    def _regressions(self, history: Sequence[RoundRecord]) -> int:
        """How many scored rounds ended MEANINGFULLY below the best of everything
        before them.  Same predicate as the gate (:func:`meaningful_regression`),
        so a sub-noise dip can never help burn the run's single strategy switch
        (a -0.005 blip + one real regression used to count 2 = "regression" stop
        with the switch never offered)."""
        return sum(1 for i in range(1, len(history))
                   if meaningful_regression(history[i].score, best_score(history[:i]), self.policy))

    def _plateaued(self, history: Sequence[RoundRecord]) -> bool:
        """True when the last ``plateau_window`` scored rounds did not raise the best
        score by ``min_delta``.  Rounds without a judgment (build failures) do not
        count as evidence of a plateau: a pending repair explains a flat score."""
        scored = [r for r in history if r.score is not None]
        w = self.policy.plateau_window
        if len(scored) < w + 1:
            return False
        before = max(r.score for r in scored[:-w])  # type: ignore[type-var]
        after = max(r.score for r in scored[-w:])  # type: ignore[type-var]
        return (after - before) < self.policy.min_delta


def detail_blocked(history: Sequence[RoundRecord], policy: RoundPolicy) -> str:
    """"" when a surface-detail round is due; otherwise the reason it is not.

    A detail round is worth money only on an artifact whose STRUCTURE is finished:
    the last round built, has no gate ERROR, scored at least ``detail_min_score``
    and is within one judge σ of the best round in the run.  Anything else and the
    money belongs to repair (measured: refine rounds that added > 2000 triangles
    while assembly was still broken lost 0.075 of assembly_fit)."""
    if (policy.detail_rounds or 0) <= 0:
        return "detail rounds disabled"
    spent = sum(1 for r in history if r.kind == DETAIL_KIND)
    if spent >= (policy.detail_rounds or 0):
        return f"{spent} detail round(s) already spent"
    if not history:
        return "no rounds yet"
    last = history[-1]
    if last.build is None or not last.build.ok:
        return "last round did not build"
    if gate_error_count(last) > 0:
        return f"{gate_error_count(last)} gate error(s) still open"
    if last.score is None:
        return "last round was not judged"
    if last.score < policy.detail_min_score:
        return f"score {last.score:.3f} < detail floor {policy.detail_min_score:.2f}"
    best = best_score(history)
    if best is not None and last.score < best - policy.sigma:
        return f"last round {last.score:.3f} is below best {best:.3f} by more than σ"
    return ""


def best_score(history: Sequence[RoundRecord]) -> float | None:
    """Best judged score in ``history`` (``None`` when nothing was scored)."""
    scores = [r.score for r in history if r.score is not None]
    return max(scores) if scores else None


def last_gain(history: Sequence[RoundRecord]) -> float | None:
    """How much the LAST round moved the best score (``None`` when unscored).

    Negative when it regressed; 0.0 when it changed nothing measurable."""
    if len(history) < 2 or history[-1].score is None:
        return None
    before = best_score(history[:-1])
    if before is None:
        return None
    return float(history[-1].score) - before


def best_index(rounds: Sequence[tuple[float, int]]) -> int:
    """Index of the best round: higher score, tie → fewer errors, tie → later round."""
    if not rounds:
        raise ValueError("best_index needs at least one round")
    # the three-clause running max WAS this key: greater score, then fewer errors,
    # then later index (the third clause `score == bs and n_err == be` is the tie-break)
    return max(range(len(rounds)), key=lambda i: (rounds[i][0], -rounds[i][1], i))


class BestSelector:
    """Best round = highest score, then fewer gate errors, then later.

    ``best_index`` above owns the ranking rule.  A round that
    did not build is never picked (it has no score, and delivering code that does
    not run is never an improvement); when NO round has a score the fallback is
    the built round with the fewest gate errors — later on a tie — so a round that
    broke the gates cannot displace a clean earlier artifact just by being last."""

    def pick(self, rounds: Sequence[RoundRecord]) -> int | None:
        scored = [(i, r) for i, r in enumerate(rounds) if r.score is not None and not _build_failed(r)]
        if scored:
            k = best_index([(float(r.score), gate_error_count(r)) for _, r in scored])  # type: ignore[arg-type]
            return scored[int(k)][0]
        built = [i for i, r in enumerate(rounds) if r.build is not None and r.build.ok]
        if not built:
            return None
        return min(built, key=lambda i: (gate_error_count(rounds[i]), -i))


def gate_error_count(r: RoundRecord) -> int:
    return sum(len(g.errors) for g in r.gates)


def _build_failed(r: RoundRecord) -> bool:
    """The round is KNOWN not to build (a record with no build at all is not a failure:
    resumed/rejudged records may carry a score without one)."""
    return r.build is not None and not r.build.ok


# ===================================================================== budget

#: stage label used when a caller does not say where the money went
OTHER_STAGE = "other"


class BudgetExceeded(RuntimeError):
    """Raised when a run crosses its USD or wall-clock ceiling."""

    def __init__(self, reason: str, *, spent_usd: float, elapsed_min: float):
        super().__init__(reason)
        self.reason = reason
        self.spent_usd = spent_usd
        self.elapsed_min = elapsed_min


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
        run with ``CV3D_COST_LEDGER=off`` (the guard's own ledger was opened
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
                elapsed_min=elapsed,
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

    def soft_remaining(self) -> dict[str, float]:
        """Headroom left inside the soft sub-budget (never negative)."""
        return {"minutes": max(0.0, self.soft_minutes() - self.elapsed_minutes())}

    def ok(self) -> bool:
        """True when no ceiling is crossed (non-raising variant of ``check``)."""
        try:
            self.check()
        except BudgetExceeded:
            return False
        return True

    def remaining(self) -> dict[str, float]:
        """Remaining wall clock: ``{"minutes": ..., "fraction": ...}`` (no money key since the USD ceiling went, fbf89a5)."""
        minutes = max(0.0, self.hard_minutes - self.elapsed_minutes())
        frac = minutes / self.hard_minutes if self.hard_minutes > 0 else 0.0
        return {"minutes": minutes, "fraction": frac}

    def timeout_s(self, want_s: float, *, floor_s: float = 60.0, soft: bool = True) -> int:
        """``want_s`` clipped to the wall-clock actually left (soft cap when ``soft``).

        A generation session must never be allowed to outlive the run's budget:
        the greenhouse scene lost 54 minutes to zone agents that kept working
        after the ceiling had already been crossed."""
        left = (self.soft_remaining()["minutes"] if soft else self.remaining()["minutes"]) * 60.0
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
    seconds.  Grace (``grace_minutes``) and config (ceilings, soft
    fraction, run, ledger) are EXCLUDED on purpose: grace is per-attempt salvage
    headroom — persisting it would ratchet the hard ceiling — and config always
    comes from the current spec/settings."""

    version: int = 1
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
