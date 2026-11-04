"""Generation depth: complexity budgets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from codeverse3d.conventions import MAX_TRIS_OBJECT

if TYPE_CHECKING:
    from codeverse3d.contracts.plan import StaticPlan

# --------------------------------------------------------------------------- triangle / time budget
#: triangles a well-detailed single part is worth.  Corpus: median tri/part ≈ 430, and the
#: highest-scoring hard run (garden arch, 0.929) sits at 1936.  Target the good end.
TRIS_PER_UNIT_TARGET = 2_000
TRIS_PER_UNIT_FLOOR = 350
TRIS_PER_UNIT_MAX = 20_000

BUILD_S_PER_UNIT = 3.0
MIN_BUILD_S = 20


@dataclass(frozen=True)
class DepthBudget:
    """What THIS object is allowed to spend, sized from its own plan."""

    n_units: int
    min_tris: int
    target_tris: int
    max_tris: int
    max_build_s: int


DETAIL_ADVICE = (
    "- Spend detail INSIDE the parts the plan already names — the edges, profiles, seams and repeats the "
    "real object carries — not on new parts the plan does not list.  Measured on this harness: detail "
    "inside a part is neutral-to-positive for the score, extra un-planned parts are strongly negative."
)


def depth_budget(plan: StaticPlan, build_timeout_s: int) -> DepthBudget:
    """Triangle + build-time budget sized from the plan's part/instance count."""
    n_units = plan.leaf_count
    target = _clamp(TRIS_PER_UNIT_TARGET * n_units, 6_000, 200_000)
    return DepthBudget(
        n_units=n_units,
        min_tris=_clamp(TRIS_PER_UNIT_FLOOR * n_units, 1_500, 40_000),
        target_tris=target,
        max_tris=_clamp(TRIS_PER_UNIT_MAX * n_units, 60_000, MAX_TRIS_OBJECT),
        # the subprocess is killed at build_timeout_s, so never allow more than
        # that minus the exporter's share, and never less than MIN_BUILD_S
        max_build_s=int(_clamp(int(BUILD_S_PER_UNIT * n_units), MIN_BUILD_S, max(MIN_BUILD_S, build_timeout_s - 30))),
    )


def _clamp(v: float, lo: float, hi: float) -> int:
    return int(max(lo, min(hi, v)))


# --------------------------------------------------------------------------- the budget gate
BUDGET_GATE = "detail_budget"


def budget_gate(measurement: Any, build: Any, budget: DepthBudget) -> Any:
    """``detail_budget`` gate: is this object as dense as its own plan says it should be?"""
    from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity

    findings: list[Any] = []
    tris = int(getattr(measurement, "tri_count", 0) or 0)
    per_unit = tris / max(1, budget.n_units)
    if tris > budget.max_tris:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.ERROR, target="overall",
            message=f"{tris:,} triangles exceeds this object's ceiling of {budget.max_tris:,} "
                    f"({budget.n_units} built objects × {TRIS_PER_UNIT_MAX:,})",
            fix_hint=f"over this object's ceiling of {budget.max_tris:,}: reduce the segment / subdivision "
                     f"counts on the heaviest parts",
            data={"kind": "over_tri_budget", "tris": tris, "max": budget.max_tris}))
    elif tris < budget.min_tris:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.WARN, target="overall",
            message=f"{tris:,} triangles ({per_unit:.0f} per built object) is below this object's detail floor "
                    f"of {budget.min_tris:,} — it is a stack of primitives, not a modelled object",
            fix_hint=f"{tris:,} triangles is under this object's floor of {budget.min_tris:,} (target "
                     f"{budget.target_tris:,}): add the detail the real object carries INSIDE the parts the "
                     f"plan names, not as new parts",
            data={"kind": "under_tri_budget", "tris": tris, "floor": budget.min_tris,
                  "target": budget.target_tris, "per_unit": round(per_unit, 1)}))
    ms = int(getattr(build, "duration_ms", 0) or 0)
    if ms > budget.max_build_s * 1000:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.WARN, target="overall",
            message=f"the build took {ms / 1000:.1f} s, over this object's {budget.max_build_s} s budget",
            fix_hint="find the part with the most segments/subdivision/booleans and simplify it",
            data={"kind": "slow_build", "ms": ms, "budget_s": budget.max_build_s}))
    if not findings:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.INFO, target="overall",
            message=f"{tris:,} triangles ({per_unit:.0f} per built object) inside the "
                    f"{budget.min_tris:,}–{budget.max_tris:,} budget",
            data={"kind": "tri_budget_ok", "tris": tris, "target": budget.target_tris}))
    return GateReport.of(BUDGET_GATE, findings)
