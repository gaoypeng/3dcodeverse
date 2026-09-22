"""Generation depth: complexity budgets and per-part scoped generation.

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
3. **The limits handed to the builder are flat and irrelevant.**  ``MAX_TRIS_OBJECT``
   is 600 000 against a corpus p50 of ~6 000 and an all-time max of 54 210; build
   timeout 300 s against p50 0.25 s / p99 8.3 s.  A 4-part stool and a 25-part
   machine were given the same numbers, so the number said nothing.

The *measurement* side of the same question — how much artifact actually got built,
on eight objective axes — is ``codeverse3d/spatial/complexity.py`` + ``docs/COMPLEXITY.md``.
This module is the *target* side: what THIS plan should be allowed and asked to spend.

Hence: :func:`depth_budget` (how many triangles / how much build time this *plan*
deserves — how many PARTS a prompt deserves is ``tracks.planner.plan_budget``,
which also owns the thin-plan re-ask), and :func:`scope_groups` (how to split one plan into sessions small
enough that each part gets real attention) with :func:`interfaces_text` handing
each session the exact numbers of the parts it must touch but may not edit.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from codeverse3d.conventions import MAX_TRIS_OBJECT, to_pascal, to_snake

log = logging.getLogger(__name__)

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

    n_parts: int
    n_units: int  # parts × instances — the things that actually get built
    min_tris: int
    target_tris: int
    max_tris: int
    max_build_s: int

    def as_prompt(self) -> str:
        """The complexity-aware limits block that replaces the contract's flat numbers."""
        return (
            f"DETAIL BUDGET for this object ({self.n_parts} plan parts / {self.n_units} built objects — "
            f"these numbers are sized for THIS plan, not a global cap):\n"
            f"- Triangles: aim for **{self.target_tris:,}** in total ({self.target_tris // max(1, self.n_units):,} "
            f"per built object).  Below {self.min_tris:,} the object reads as primitive boxes and the judge scores "
            f"geometry_detail down; above {self.max_tris:,} the build is rejected.\n"
            f"- Spend those triangles INSIDE the parts the plan already names — bevels, chamfers, profile sweeps, "
            f"fasteners, panel seams, arrays — not on new parts the plan does not list.  Measured on this harness: "
            f"triangles per part are neutral-to-positive for the score, extra un-planned parts are strongly negative.\n"
            f"- Build time: your code must finish in **under {self.max_build_s} s** headless.  Typical objects here "
            f"build in well under a second, so this is generous; if you are near it, lower segment counts before "
            f"you drop detail."
        )


def depth_budget(plan: Any, *, build_timeout_s: int = 300) -> DepthBudget:
    """Triangle + build-time budget sized from the plan's part/instance count."""
    parts = list(getattr(plan, "parts", None) or [])
    n_parts = len(parts)
    n_units = sum(max(1, int(getattr(p, "leaf_count", None) or getattr(p, "instances", 1) or 1)) for p in parts) or 1
    target = _clamp(TRIS_PER_UNIT_TARGET * n_units, 6_000, 200_000)
    return DepthBudget(
        n_parts=n_parts,
        n_units=n_units,
        min_tris=_clamp(TRIS_PER_UNIT_FLOOR * n_units, 1_500, 40_000),
        target_tris=target,
        max_tris=_clamp(TRIS_PER_UNIT_MAX * n_units, 60_000, MAX_TRIS_OBJECT),
        # honest: the subprocess is killed at build_timeout_s, so never promise more than
        # that minus the exporter's share, and never less than MIN_BUILD_S
        max_build_s=int(_clamp(int(BUILD_S_PER_UNIT * n_units), MIN_BUILD_S, max(MIN_BUILD_S, build_timeout_s - 30))),
    )


def _clamp(v: float, lo: float, hi: float) -> int:
    return int(max(lo, min(hi, v)))


# --------------------------------------------------------------------------- scoped generation
#: below this many plan parts one session handles the whole object fine (and fan-out only
#: adds coordination cost): the corpus shows the ceiling biting from ~8 parts up
MIN_PARTS_FOR_SCOPED = 8
#: parts one scoped session owns.  Small enough that every part gets real attention,
#: large enough that a part and the neighbour it must weld to usually land together.
DEFAULT_PARTS_PER_SCOPE = 3


def scoped_generation_enabled(default: bool = True) -> bool:
    """``C3D_SCOPED_PARTS=off|0|false`` turns per-part scoped baselines off (A/B, debugging)."""
    from codeverse3d.config import env_flag

    return env_flag("C3D_SCOPED_PARTS", default)


@dataclass(frozen=True)
class PartScope:
    """One scoped generation session: a few plan parts and the files that hold them."""

    parts: tuple[Any, ...]
    files: tuple[str, ...]

    @property
    def names(self) -> list[str]:
        return [str(p.name) for p in self.parts]

    @property
    def label(self) -> str:
        return "+".join(to_snake(n) for n in self.names[:3]) or "parts"


def scope_groups(
    plan: Any,
    *,
    files_for: Any,
    max_groups: int = 6,
    parts_per_scope: int = DEFAULT_PARTS_PER_SCOPE,
    min_parts: int = MIN_PARTS_FOR_SCOPED,
) -> list[PartScope]:
    """Split a plan into per-part scoped sessions, or ``[]`` when one session suffices.

    Parts are partitioned along the ``attach_to`` tree, so a part and the parts it
    carries land in the SAME session wherever possible — that is where the
    assembly-fit penalty for extra parts comes from, and a contact both sides of
    which are written by one session is a contact that gets welded.  Subtrees are
    then packed into at most ``max_groups`` groups of ``parts_per_scope``.
    """
    parts = list(getattr(plan, "parts", None) or [])
    if len(parts) < max(2, min_parts) or max_groups < 2:
        return []
    units = _attachment_units(parts, parts_per_scope)
    groups = _pack(units, max_groups=max_groups, parts_per_scope=parts_per_scope)
    if len(groups) < 2:
        return []
    out: list[PartScope] = []
    for g in groups:
        files: list[str] = []
        for p in g:
            for f in _files_for(files_for, p.name):
                if f not in files:
                    files.append(f)
        if not files:  # a language with no per-part file ownership cannot be scoped
            return []
        out.append(PartScope(parts=tuple(g), files=tuple(files)))
    return out


def _files_for(files_for: Any, name: str) -> list[str]:
    if not callable(files_for):
        return []
    try:
        got = files_for(name)
    except Exception as e:  # noqa: BLE001 — a mapper failure must not kill generation
        log.warning("file_for_target failed for %s: %s", name, e)
        return []
    if not got:
        return []
    return [str(got)] if isinstance(got, str) else [str(x) for x in got]


def _attachment_units(parts: Sequence[Any], parts_per_scope: int) -> list[list[Any]]:
    """Attachment subtrees, each split down to ``parts_per_scope`` at most."""
    by_key = {to_snake(str(p.name)): p for p in parts}
    children: dict[str, list[str]] = {k: [] for k in by_key}
    roots: list[str] = []
    for p in parts:
        key = to_snake(str(p.name))
        parent = to_snake(str(getattr(p, "attach_to", None) or ""))
        if parent and parent in by_key and parent != key:
            children[parent].append(key)
        else:
            roots.append(key)
    seen: set[str] = set()
    units: list[list[Any]] = []

    def subtree(key: str) -> list[Any]:
        out, stack = [], [key]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            out.append(by_key[k])
            stack.extend(children.get(k, ()))
        return out

    for r in roots:
        if r in seen:
            continue
        seen.add(r)
        units.append([by_key[r]])  # the root itself is its own unit (it is the datum)
        for c in children.get(r, ()):
            got = subtree(c)
            if got:
                units.append(got)
    for k, p in by_key.items():  # cycles / unreachable parts
        if k not in seen:
            seen.add(k)
            units.append([p])
    out: list[list[Any]] = []
    for u in units:
        for i in range(0, len(u), parts_per_scope):
            out.append(u[i:i + parts_per_scope])
    return out


def _pack(units: list[list[Any]], *, max_groups: int, parts_per_scope: int) -> list[list[Any]]:
    """First-fit-decreasing pack of subtrees into ≤ ``max_groups`` groups."""
    if not units:
        return []
    cap = max(parts_per_scope, -(-sum(len(u) for u in units) // max_groups))
    groups: list[list[Any]] = []
    for u in sorted(units, key=len, reverse=True):
        target = next((g for g in groups if len(g) + len(u) <= cap), None)
        if target is None and len(groups) < max_groups:
            groups.append(list(u))
        elif target is None:
            min(groups, key=len).extend(u)
        else:
            target.extend(u)
    return [g for g in groups if g]


# --------------------------------------------------------------------------- scoped prompt text
def interfaces_text(plan: Any, scope: PartScope) -> str:
    """The exact numbers of every part OUTSIDE this scope that touches a part inside it.

    This is the whole point of scoping: the session never sees the other 20 parts,
    but it must weld to the ones it borders, so it gets their planned box and the
    2 mm overlap rule for each contact — deterministically, from the plan.
    """
    parts = list(getattr(plan, "parts", None) or [])
    by_key = {to_snake(str(p.name)): p for p in parts}
    inside = {to_snake(n) for n in scope.names}
    rows: list[str] = []
    seen: set[tuple[str, str]] = set()

    def _row(mine: Any, other: Any, direction: str) -> None:
        key = (to_snake(str(mine.name)), to_snake(str(other.name)))
        if key in seen:
            return
        seen.add(key)
        rows.append(f"| {to_pascal(str(mine.name))} | {direction} | {to_pascal(str(other.name))} | "
                    f"({_v(other.bbox.center)}) | ({_v(other.bbox.extents)}) | "
                    f"x [{other.bbox.min[0]:.3f}, {other.bbox.max[0]:.3f}] "
                    f"y [{other.bbox.min[1]:.3f}, {other.bbox.max[1]:.3f}] "
                    f"z [{other.bbox.min[2]:.3f}, {other.bbox.max[2]:.3f}] |")

    for p in scope.parts:
        parent = by_key.get(to_snake(str(getattr(p, "attach_to", None) or "")))
        if parent is not None and to_snake(str(parent.name)) not in inside:
            _row(p, parent, "attaches TO")
    for other in parts:
        if to_snake(str(other.name)) in inside:
            continue
        parent_key = to_snake(str(getattr(other, "attach_to", None) or ""))
        if parent_key in inside:
            _row(by_key[parent_key], other, "carries")
    if not rows:
        return "(no parts outside this scope touch yours — build to the plan boxes and the ground plane)"
    head = ("| your part | relation | neighbour (owned by another session — DO NOT create or edit it) | "
            "neighbour centre (m) | neighbour extents (m) | neighbour box |\n|---|---|---|---|---|---|")
    return head + "\n" + "\n".join(rows)


def _v(vec: Sequence[float]) -> str:
    return ", ".join(f"{float(x):.3f}" for x in vec)



# --------------------------------------------------------------------------- the budget gate
BUDGET_GATE = "detail_budget"


def budget_gate(measurement: Any, build: Any, budget: DepthBudget) -> Any:
    """``detail_budget`` gate: is this object as dense as its own plan says it should be?

    Deterministic, so the judge is never asked "does it look detailed enough" —
    code answers it.  Over the ceiling is an ERROR (the build is too heavy to ship);
    under the floor and over the build-time budget are WARNs, because a thin object
    is a quality problem, not a broken one — the static track turns the thin WARN
    into a refine task through ``extra_refine_tasks``.
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
            fix_hint="lower the segment counts and subdivision levels on the heaviest parts; "
                     "detail with bevels and arrays instead of dense meshes",
            data={"kind": "over_tri_budget", "tris": tris, "max": budget.max_tris}))
    elif tris < budget.min_tris:
        findings.append(GateFinding(
            gate=BUDGET_GATE, severity=Severity.WARN, target="overall",
            message=f"{tris:,} triangles ({per_unit:.0f} per built object) is below this object's detail floor "
                    f"of {budget.min_tris:,} — it is a stack of primitives, not a modelled object",
            fix_hint=f"aim for {budget.target_tris:,} triangles: bevel every hard edge, add the panel seams, "
                     f"fasteners and counted features the plan describes INSIDE the existing parts "
                     f"(see the cookbook's Density chapter)",
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
    return GateReport(gate=BUDGET_GATE, findings=findings,
                      passed=not any(f.severity is Severity.ERROR for f in findings))
