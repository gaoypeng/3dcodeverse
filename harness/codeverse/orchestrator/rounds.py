"""Round loop policy: when to stop, which round is best, what to refine next.

All thresholds live in ``RoundPolicy`` (one calibrated object, no scattered
ifs).  ``StopPolicy.evaluate`` is a pure function of the round history;
``BestSelector.pick`` is a pure function of the rounds; ``build_refine_
instructions`` compiles gate errors + failed acceptance + the judge's
improvement plan into typed ``RefineTask``s, and ``plan_parallel_groups``
decides which tasks may run concurrently from file ownership.

**Two money stops** were added from the cost audit (docs/COST.md §5), both
measured against the judge's own noise rather than a magic constant — the
per-model σ table lives in ONE place, ``codeverse.cost.routing.JUDGE_NOISE``
(pro 0.030, flash 0.083), and is read here through :func:`judge_sigma`:

* ``regression`` — a refine round that scored more than 1 σ *below* the best is
  not evidence that another round of the same shape will do better.  The first
  such round switches strategy (one whole-artifact rewrite instead of the same
  per-part task set); a second one stops the run.  19 rounds in the audit scored
  below the best and were thrown away ($14.23); this rule governs what the loop
  is allowed to do *after* one of them (measured replay: 4 rounds re-shaped, and
  a hard stop instead would have killed the recovery round that made
  ``furn_hard_rolltop_desk`` pass).
* ``diminishing_returns`` — from r03 on, a round only runs when the previous
  round gained more than 1.5 σ and the run is still below target.  r03 cost
  $33.49 per score point in the audit, against $4.93 for r01.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateReport
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem, Plan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import to_snake
from codeverse.judges.metrics import best_index

log = logging.getLogger(__name__)

StopReason = Literal["pass", "plateau", "budget", "continue", "max_rounds", "judge_unavailable",
                     "regression", "diminishing_returns"]

#: what the next round should look like when the loop continues
Strategy = Literal["same", "switch", "detail"]

#: ``RoundRecord.kind`` of a round that already changed strategy after a regression
REWRITE_KIND = "rewrite"
#: ``RoundRecord.kind`` of the surface-detail round (see :func:`detail_round_due`)
DETAIL_KIND = "detail"

#: ``Strategy`` → the ``RoundRecord.kind`` the loop stamps on the round it starts
KIND_FOR_STRATEGY: dict[str, str] = {"same": "refine", "switch": REWRITE_KIND, "detail": DETAIL_KIND}


def kind_for_strategy(strategy: str) -> str:
    """THE mapping from a stop decision's strategy to the round kind it produces."""
    return KIND_FOR_STRATEGY.get(strategy, "refine")

#: σ for a judge that is not in the measured table: the default judge's
#: (``gemini-3.1-pro-preview``, ``Settings.default_judge``), because that is what
#: an unnamed judge almost always is.  Calibrate a new judge
#: (``python -m codeverse.judges.calibration``) and add it to JUDGE_NOISE rather
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
    try:
        from codeverse.cost.routing import JUDGE_NOISE
    except Exception as e:  # noqa: BLE001 — the cost package is optional at import time
        log.debug("judge noise table unavailable: %s", e)
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
    judge_on_gate_errors: bool = True  # still judge when gates (not build) fail
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
    regression_allow_switch: bool = True  # False = a regression stops the run outright
    marginal_sigma: float = 1.5  # from marginal_from_round on, the last gain must beat this × σ
    marginal_from_round: int = 3  # first refine round index the marginal-value test applies to
    # ---- agent session shape.  A 28-turn cap was TESTED AND REJECTED: a controlled
    # A/B on one prompt (n=3 per arm, same generator/judge/budget) cost $1.381 mean at
    # 0.479 mean score capped, against $1.360 at 0.684 uncapped — no saving and −0.205
    # score (docs/COST.md §17).  0 = no policy cap: the session runs under the backend's
    # own AgentJob.max_turns.  Callers who want one set it explicitly (cost profiles do).
    agent_max_turns: int = 0  # model turns one generation session may take (0 = uncapped)
    agent_wrapup_turns: int = 6  # turns granted to land a final build + summary when a cap IS set
    # ---- depth.  Measured (wave "generation-depth"): across 88 consecutive refine-round pairs the
    # built part count changed ZERO times and mean Δgeometry_detail was +0.003 — the refine loop is
    # a repair loop and never adds anything.  The rounds that DID add geometry did it while assembly
    # was still broken and lost 0.075 of assembly_fit / 0.025 of overall for it.  So detail gets its
    # own round, and it only runs once the structure gates are clean.
    detail_rounds: int = 0  # surface-detail rounds a run may spend (0 = off; tracks that implement
    #                         the round opt in — see lifecycle.BaseTrack.supports_detail_round)
    detail_min_score: float = 0.45  # below this the object is still wrong; detail would be polish on a mistake
    detail_bbox_tol_m: float = 0.005  # a detail round that moves a part box by more than this failed its brief

    def with_candidates(self, n: int | None) -> RoundPolicy:
        """Copy with ``n_candidates`` set (``None`` → unchanged)."""
        if n is None or n == self.n_candidates:
            return self
        return replace(self, n_candidates=max(1, int(n)))

    def with_judge(self, judge_model: str | None) -> RoundPolicy:
        """Copy that knows which judge scores the rounds (its σ sizes both money stops)."""
        if not judge_model or judge_model == self.judge_model:
            return self
        return replace(self, judge_model=judge_model)

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

    def decide(self, history: Sequence[RoundRecord], *, budget_ok: bool = True) -> StopReason:
        """The stop reason only (``evaluate`` also says what shape the next round takes)."""
        return self.evaluate(history, budget_ok=budget_ok).reason

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
        if score is None or best_before is None:
            return None
        drop = best_before - score
        if drop <= self.policy.regression_delta:
            return None
        detail = (f"r{last.index:02d} scored {score:.3f} vs best {best_before:.3f} "
                  f"({-drop:+.3f}, judge σ {self.policy.sigma:.3f})")
        if not self.policy.regression_allow_switch:
            return StopDecision("regression", detail=detail)
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
                                       f"(1.5 × judge σ {self.policy.sigma:.3f})")
        if best is not None and best >= self.policy.target:
            return StopDecision("diminishing_returns",
                                detail=f"r{nxt:02d} not started: best {best:.3f} already at target "
                                       f"{self.policy.target:.2f}")
        return None

    @staticmethod
    def _regressions(history: Sequence[RoundRecord]) -> int:
        """How many scored rounds ended below the best of everything before them."""
        n = 0
        for i in range(1, len(history)):
            s, before = history[i].score, best_score(history[:i])
            if s is not None and before is not None and s < before:
                n += 1
        return n

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
    if policy.detail_rounds <= 0:
        return "detail rounds disabled"
    spent = sum(1 for r in history if r.kind == DETAIL_KIND)
    if spent >= policy.detail_rounds:
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


class BestSelector:
    """Best round = highest score, then fewer gate errors, then later.

    ``codeverse.judges.metrics.best_index`` owns the ranking rule.  A round that
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


# ----------------------------------------------------------------------------- refine tasks
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
