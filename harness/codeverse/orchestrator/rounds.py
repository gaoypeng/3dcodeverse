"""Round loop policy: when to stop, which round is best, what to refine next.

All thresholds live in ``RoundPolicy`` (one calibrated object, no scattered
ifs).  ``StopPolicy.decide`` is a pure function of the round history;
``BestSelector.pick`` is a pure function of the rounds; ``build_refine_
instructions`` compiles gate errors + failed acceptance + the judge's
improvement plan into typed ``RefineTask``s, and ``plan_parallel_groups``
decides which tasks may run concurrently from file ownership.
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

StopReason = Literal["pass", "plateau", "budget", "continue", "max_rounds", "judge_unavailable"]


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

    def with_candidates(self, n: int | None) -> RoundPolicy:
        """Copy with ``n_candidates`` set (``None`` → unchanged)."""
        if n is None or n == self.n_candidates:
            return self
        return replace(self, n_candidates=max(1, int(n)))


class StopPolicy:
    """Decide whether the loop goes on.  ``history`` = rounds so far (baseline first)."""

    def __init__(self, policy: RoundPolicy):
        self.policy = policy

    def decide(self, history: Sequence[RoundRecord], *, budget_ok: bool = True) -> StopReason:
        if not budget_ok:
            return "budget"
        if not history:
            return "continue"
        last = history[-1]
        if last.judgment is not None and last.judgment.passed and last.judgment.overall >= self.policy.target:
            return "pass"
        refine_rounds = len(history) - 1
        if refine_rounds >= self.policy.max_rounds:
            return "max_rounds"
        if self._plateaued(history):
            return "plateau"
        return "continue"

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


class BestSelector:
    """Best round = highest score, then fewer gate errors, then earlier.

    ``codeverse.judges.metrics.best_index`` owns the ranking rule; rounds
    without any score fall back to the last successfully built one."""

    def pick(self, rounds: Sequence[RoundRecord]) -> int | None:
        scored = [(i, r) for i, r in enumerate(rounds) if r.score is not None]
        if scored:
            k = best_index([(float(r.score), gate_error_count(r)) for _, r in scored])  # type: ignore[arg-type]
            return scored[int(k)][0]
        built = [i for i, r in enumerate(rounds) if r.build is not None and r.build.ok]
        return built[-1] if built else None


def gate_error_count(r: RoundRecord) -> int:
    return sum(len(g.errors) for g in r.gates)


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
