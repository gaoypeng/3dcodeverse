"""Generation depth: complexity budgets.

Measured on the recorded corpus (183 judged rounds / 81 runs, see the wave's
``complexity_baseline.md``), three facts shape this module:

1. **The complexity ceiling is set in round 0 and never moves.**  Across 88
   consecutive refine-round pairs the built part count changed *zero* times: the
   refine loop is a repair loop (``refine_object.j2``: "keep everything else as it
   is").  Whatever depth the baseline session managed is the depth that ships.
2. **Parts are punished, density is not.**  One point per run, ``n_plan`` vs
   ``assembly_fit`` ρ = −0.48 and vs ``geometry_detail`` ρ = −0.41, while
   ``tri_per_part`` is flat-to-positive (+0.08 / +0.18 on materials).  Extra parts
   cost score through the floating-part / interpenetration caps; extra triangles
   *inside* a part cost nothing.  Within one difficulty tier (static_v2 r00) the
   sign flips: ``n_built`` vs ``geometry_detail`` = +0.34 — more parts DO buy
   detail once difficulty is held constant, they just also buy gate errors.
3. **A flat limit says nothing.**  ``MAX_TRIS_OBJECT`` is 600 000 against a corpus p50
   of ~6 000 and an all-time max of 54 210; build timeout 300 s against p50 0.25 s /
   p99 8.3 s.  A 4-part stool and a 25-part machine hit the same numbers.

The *measurement* side of the same question — how much artifact actually got built,
on eight objective axes — is ``codeverse3d/spatial/complexity.py`` + ``eval/docs/COMPLEXITY.md``.
This module is the *target* side: what THIS plan should be allowed and asked to spend.

Hence: :func:`depth_budget` (how many triangles / how much build time this *plan*
deserves, checked by :func:`budget_gate`; the generate / refine briefs get the numberless
:data:`DETAIL_ADVICE`, and each object language's ``contract.md`` states the gate's per-object
constants as a measured requirement (D107, pinned in ``prompts/_claims.toml``); how many PARTS a prompt deserves is
``tracks.planner.plan_budget``, which also owns the thin-plan re-ask).  The per-part scoped
baseline that lived here (``scope_groups`` / ``interfaces_text``) was deleted by D110: one session
drafts the whole object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codeverse3d.conventions import MAX_TRIS_OBJECT

# --------------------------------------------------------------------------- triangle / time budget
#: triangles a well-detailed single part is worth.  Corpus: median tri/part ≈ 430, and the
#: highest-scoring hard run (garden arch, 0.929) sits at 1936.  Target the good end.
TRIS_PER_UNIT_TARGET = 2_000
#: below this per unit the part is a primitive stand-in (the ≤100 tri/part bin scores worst)
TRIS_PER_UNIT_FLOOR = 350
#: hard ceiling per unit — 10× the target leaves room for subdivided organics without
#: letting a smooth-shaded blob eat the whole budget
TRIS_PER_UNIT_MAX = 20_000

#: seconds of headless build time one unit may cost (corpus p99 for a WHOLE object is 8.3 s)
BUILD_S_PER_UNIT = 3.0
MIN_BUILD_S = 20


@dataclass(frozen=True)
class DepthBudget:
    """What THIS object is allowed to spend, sized from its own plan."""

    n_units: int  # parts × instances — the things that actually get built
    min_tris: int
    target_tris: int
    max_tris: int
    max_build_s: int


#: What the generate / refine briefs say about detail.  No numbers: the triangle and build-time
#: budget below is enforced by :func:`budget_gate`, whose findings state this object's numbers
#: (owner, 2026-09-22); the per-built-object rule itself is in each object ``contract.md`` (D107).
DETAIL_ADVICE = (
    "- Spend detail INSIDE the parts the plan already names — the edges, profiles, seams and repeats the "
    "real object carries — not on new parts the plan does not list.  Measured on this harness: detail "
    "inside a part is neutral-to-positive for the score, extra un-planned parts are strongly negative."
)


def depth_budget(plan: Any, *, build_timeout_s: int = 300) -> DepthBudget:
    """Triangle + build-time budget sized from the plan's part/instance count."""
    parts = list(getattr(plan, "parts", None) or [])
    n_units = sum(max(1, int(getattr(p, "leaf_count", None) or getattr(p, "instances", 1) or 1)) for p in parts) or 1
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
    """``detail_budget`` gate: is this object as dense as its own plan says it should be?

    Deterministic, so the judge is never asked "does it look detailed enough" —
    code answers it.  Over the ceiling is an ERROR (the build is too heavy to ship);
    under the floor and over the build-time budget are WARNs, because a thin object
    is a quality problem, not a broken one — the static track deliberately does NOT
    turn the thin WARN into a refine task: density is the detail round's job.
    """
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
