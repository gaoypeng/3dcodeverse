"""Contract gate: does the built GLB honour the plan's part list and boxes?

Plans are authored in the language's *native* frame (see ``conventions``):
blender / cadquery / urdf are Z-up with -Y front, three.js is Y-up.  The GLB is
always Y-up, so plan boxes are converted with the glTF mapping
``(x, y, z) → (x, z, -y)`` before comparison — and every number in a fix hint
(sizes, deltas, centres, translation vectors) is converted *back* into the
authoring frame and labelled, because hints are pasted verbatim into refine
prompts that speak the language's frame.

Severity policy (``tol = max(tol_m, REL_TOL × plan extent)`` per axis):
* plan part missing in the GLB → ERROR
* bbox centre / extents off by > tol → WARN (exact delta in the hint);
  > ``ERROR_FACTOR × tol`` → ERROR
* overall bbox vs ``overall_bbox`` → same policy
* overall extents are the plan's with up swapped for a horizontal axis → ERROR
  ``data["kind"] == "orientation"`` (lying down / stood on end); the two horizontal
  extents swapped → WARN "turned" (the az-0 view then shows a side, not the front)
* not standing on the ground / footprint off-centre → WARN
* GLB parts the plan never mentioned → INFO
* ScenePlan: zone groups missing → WARN; model outside ``bounds`` → WARN
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from codeverse3d.contracts.artifacts import (
    GateFinding,
    GateReport,
    Measurement,
    PartMeasure,
    Severity,
)
from codeverse3d.contracts.plan import BBox, PartPlan, Plan, ScenePlan, StaticPlan
from codeverse3d.conventions import (
    BBOX_TOLERANCE_M,
    CONTACT_GAP_M,
    FRAME_AXES,
    Frame,
    split_instance,
    to_authoring_frame,
    to_snake,
)
from codeverse3d.conventions import (
    authoring_frame as language_frame,  # the name connectivity.py imports from here
)
from codeverse3d.spatial.measure import fmt_extent_cm, fmt_vec

GATE = "contract"
#: relative tolerance on extents (fraction of the plan's extent)
REL_TOL = 0.10
#: a delta beyond ERROR_FACTOR × tolerance is an error, not a warning
ERROR_FACTOR = 3.0
_AXES = ("x", "y", "z")

# Orientation (2026-08-30).  A 90° turn about a horizontal axis leaves the AABB of the
# planned box with its up extent swapped for a horizontal one, so "lying down" is a
# permutation test on extents — but planners size THIN axes badly (h2h c_clamp: 9.6 cm
# planned for a 2.4 cm clamp), so only the planned LONG axis is required to reappear,
# within ORIENT_FIT; the thin one may be over-planned by up to ORIENT_OVER (the h2h
# clock: 26 cm planned, 34 cm real).  The up extent itself must have shrunk by
# ORIENT_LYING; a build merely too short (fancy_v1 chamber_organ, 1.15 m for 3.2 m
# planned, nothing sideways) or a 2× scale error is contract_violation, not this.
# "Stood on end" (taller than planned, the planned width now up) needs ORIENT_STOOD:
# planners under-size tall objects — loop_w7 orrery 0.32 m planned / 0.47 m built and
# fancy_v1 smock_windmill 18.5 / 25.8 m are upright and sit at 1.4–1.5×.  The rotation
# axis's own extent must not have moved by more than ORIENT_THIRD (sysprompt_ab
# b36_v0_01r: a table 1.38 m wide for a 0.48 m chair — wrong object, not rotated), and
# near-cubes say nothing (ORIENT_ANISO).  Over every measured static_object round under
# eval/bench/out (629 measurements: 435 rounds + 194 final measurement.json, 217 runs) these
# fire 0 times; on the brilliana c_clamp and gate_valve GLBs (both lying, by eye) they
# fire with the right axis.
ORIENT_FIT = float(np.log(1.25))
ORIENT_LYING = float(np.log(1.5))
ORIENT_STOOD = float(np.log(2.0))
ORIENT_THIRD = float(np.log(1.5))
ORIENT_ANISO = 1.5
ORIENT_OVER = 1.4


# --------------------------------------------------------------------------- frames
def frame_label(language: str) -> str:
    """Short frame tag for fix hints, e.g. ``"blender frame: Z-up, -Y front"``."""
    if language_frame(language) is Frame.Z_UP_NEG_Y_FRONT:
        return f"{language} frame: Z-up, -Y front"
    return f"{language} frame: Y-up, +Z front" if language else "GLB frame: Y-up, +Z front"


def plan_bbox_to_glb(bbox: BBox, language: str) -> BBox:
    """Convert a plan-frame box to the canonical GLB frame (Y-up, +Z front)."""
    if language_frame(language) is Frame.Y_UP_POS_Z_FRONT:
        return bbox
    cx, cy, cz = bbox.center
    ex, ey, ez = bbox.extents
    return BBox(center=(cx, cz, -cy), extents=(ex, ez, ey))


def glb_vec_to_plan(v, language: str, *, extents: bool = False) -> np.ndarray:
    """``conventions.to_authoring_frame`` as an array: hints must be written in the frame the
    agent codes in."""
    return np.asarray(to_authoring_frame(v, language, extents=extents), dtype=float)


# --------------------------------------------------------------------------- matching
def match_parts(plan_parts: list[PartPlan], measured: list[PartMeasure]) -> tuple[dict[str, list[PartMeasure]], list[PartMeasure]]:
    """Map each plan part → measured rows (``Name``, ``Name_0``… accepted); plus unmatched rows.

    Exact names are claimed FIRST, and the ``Name_0..Name_N`` instance pass
    (``conventions.split_instance``) never takes a node that another plan part names
    exactly.  The instance pattern once took ``shelf2`` for ``Shelf``, so a plan of
    ``Shelf`` + ``Shelf2`` had ``Shelf`` swallow the ``Shelf2`` node before ``Shelf2`` was
    considered — a false "missing from the GLB" ERROR and a real 0.75 judge cap on geometry
    that matched the plan exactly.  ``Shelf``/``Shelf2``, ``Slat``/``Slat1``, ``Tier``/``Tier2``
    are ordinary planner output for PascalCase part names.
    """
    remaining = {m.name: m for m in measured}
    matched: dict[str, list[PartMeasure]] = {pp.name: [] for pp in plan_parts}
    reserved = {to_snake(pp.name) for pp in plan_parts}
    for pp in plan_parts:  # pass 1: the node this plan part names exactly
        snake = to_snake(pp.name)
        hits = [m for n, m in list(remaining.items()) if to_snake(n) == snake]
        for m in hits:
            remaining.pop(m.name, None)
        matched[pp.name].extend(hits)
    for pp in plan_parts:  # pass 2: its Name_0..Name_N instances
        snake = to_snake(pp.name)
        hits = [m for n, m in list(remaining.items())
                if to_snake(n) not in reserved and split_instance(to_snake(n))[0] == snake]
        for m in hits:
            remaining.pop(m.name, None)
        matched[pp.name].extend(hits)
    return matched, list(remaining.values())


def planned_joins(plan: Plan | None, measurement: Measurement | None) -> list[tuple[str, tuple[str, ...]]]:
    """The plan's ``attach_to`` edges spelled in the GLB's part names, for the connectivity ledger.

    The ONE resolver: the track's gate and the MCP ``check_connectivity`` / folded build
    check all call this, so the agent-facing ledger and the judge's read the same pairs.
    Names resolve the way ``check_contract`` resolves them (``match_parts``: the exact node
    first, then the ``Name_0..N`` instances).  Counted 2026-08-30 over the 357 stored
    static_object rounds that carry attach_to edges: 965 of 2 666 raw plan names are
    instance parts absent from the mesh under their plan spelling (``FrontLeg`` →
    ``FrontLeg_0``/``FrontLeg_1``) — feeding raw names listed them as unresolved.
    Each child copy joins the copy of its parent it TOUCHES by AABB (gap ≤ CONTACT_GAP_M),
    and every touching copy when several do: a brace spanning Leg_1→Leg_3 whose box also
    grazes Leg_0 ties at 0 for all three, and picking the first by list order sent it to
    Leg_0 alone — a false OPEN.  The gate measures every pair exactly and reduces per
    child (the contact row wins, else the smallest gap), so emitting candidates costs
    nothing but a guess.  A child touching no copy joins the nearest one — the full cross
    product would report the far apron/leg pair of every chair as OPEN.
    """
    if plan is None or measurement is None or not measurement.parts:
        return []
    parts = list(getattr(plan, "parts", []) or [])
    matched, _ = match_parts(parts, measurement.parts)
    edges: list[tuple[str, str]] = []
    for pp in parts:
        if not pp.attach_to:
            continue
        parents = matched.get(pp.attach_to) or []
        for child in matched.get(pp.name) or []:
            gaps = {p.name: _aabb_gap((child.bbox_min, child.bbox_max), (p.bbox_min, p.bbox_max))
                    for p in parents if p.name != child.name}
            if not gaps:
                continue
            touching = [n for n, g in gaps.items() if g <= CONTACT_GAP_M]
            edges.append((child.name, tuple(touching or [min(gaps, key=gaps.__getitem__)])))
    return edges


def _aabb_gap(a: np.ndarray | tuple, b: np.ndarray | tuple) -> float:
    """Euclidean distance between two boxes given as ``(min, max)`` corners, 0 when they
    overlap (``connectivity`` feeds it ``trimesh.bounds``: a mesh pair's lower bound)."""
    (lo_a, hi_a), (lo_b, hi_b) = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(np.linalg.norm(np.maximum(np.maximum(lo_a - hi_b, lo_b - hi_a), 0.0)))


@dataclass
class _BoxDelta:
    center: np.ndarray
    extents: np.ndarray
    tol: np.ndarray

    @property
    def worst_ratio(self) -> float:
        return float(np.max(np.maximum(np.abs(self.center), np.abs(self.extents)) / self.tol))


def _box_delta(meas_min: np.ndarray, meas_max: np.ndarray, plan: BBox, tol_m: float) -> _BoxDelta:
    p_ext = np.asarray(plan.extents, dtype=float)
    p_ctr = np.asarray(plan.center, dtype=float)
    tol = np.maximum(tol_m, REL_TOL * p_ext)
    m_ext = meas_max - meas_min
    m_ctr = (meas_max + meas_min) / 2
    return _BoxDelta(center=m_ctr - p_ctr, extents=m_ext - p_ext, tol=tol)


def _fmt_delta(v: np.ndarray) -> str:
    return ", ".join(f"{a}{d * 100:+.1f}cm" for a, d in zip(_AXES, v, strict=True))


def _box_findings(target: str, d: _BoxDelta, plan: BBox, *, what: str, language: str,
                  check_center: bool = True) -> list[GateFinding]:
    """WARN/ERROR findings for one box comparison (empty when within tolerance).
    ``d``/``plan`` are in the GLB frame; every number in the hint (and ``data``) is
    written back in ``language``'s authoring frame so the agent can paste it."""
    out: list[GateFinding] = []
    ext_bad = np.abs(d.extents) > d.tol
    ctr_bad = check_center and np.any(np.abs(d.center) > d.tol)
    if not ext_bad.any() and not ctr_bad:
        return out
    ratio = d.worst_ratio if check_center else float(np.max(np.abs(d.extents) / d.tol))
    sev = Severity.ERROR if ratio > ERROR_FACTOR else Severity.WARN
    p_ext = glb_vec_to_plan(plan.extents, language, extents=True)
    d_ext = glb_vec_to_plan(d.extents, language, extents=True)
    d_ctr = glb_vec_to_plan(d.center, language)
    p_ctr = glb_vec_to_plan(plan.center, language)
    bits = []
    if ext_bad.any():
        bits.append(f"size {fmt_extent_cm(p_ext + d_ext)} vs planned {fmt_extent_cm(p_ext)} cm (Δ {_fmt_delta(d_ext)})")
    if ctr_bad:
        bits.append(f"centre off by ({_fmt_delta(d_ctr)})")
    hint = f"{what}: " + ("; ".join(bits))
    out.append(GateFinding(
        gate=GATE, severity=sev, target=target,
        message=f"{what} bbox deviates from the plan (worst {ratio:.1f}× tolerance)",
        fix_hint=hint + f"; planned centre ({', '.join(f'{c:.3f}' for c in p_ctr)}) m ({frame_label(language)})",
        data={"delta_center_m": d_ctr.tolist(), "delta_extents_m": d_ext.tolist(),
              "tol_m": glb_vec_to_plan(d.tol, language, extents=True).tolist(), "frame": language_frame(language).value},
    ))
    return out


def _log_ratio(a: float, b: float) -> float:
    return abs(float(np.log(a / b)))


def _orientation_finding(m: Measurement, plan: StaticPlan, language: str) -> GateFinding | None:
    """Is the object lying down / stood on end (ERROR) or turned 90° about up (WARN)?

    Everything is in the plan frame: ``meas`` is the measured overall extents mapped back
    with :func:`glb_vec_to_plan`, ``up`` the frame's up axis from ``conventions``.  One
    finding at most; the thresholds and the corpus behind them are documented at the top."""
    planned = np.asarray(plan.overall_bbox.extents, dtype=float)
    meas = glb_vec_to_plan(m.extents, language, extents=True)
    if np.any(planned <= 1e-6) or np.any(meas <= 1e-6):
        return None
    up = int(np.argmax(FRAME_AXES[language_frame(language)]["up"]))
    label = frame_label(language)
    horizontal = [a for a in range(3) if a != up]
    best: tuple[float, str, int] | None = None  # (fit, pose, h)
    for h in horizontal:
        k = 3 - up - h  # the axis the object turned about keeps its extent
        if _log_ratio(meas[k], planned[k]) > ORIENT_THIRD:
            continue
        shrink = _log_ratio(meas[up], planned[up])
        if meas[up] < planned[up] and shrink >= ORIENT_LYING and planned[up] / planned[h] >= ORIENT_ANISO:
            fit = _log_ratio(meas[h], planned[up])  # the planned height now runs along h
            if fit <= ORIENT_FIT and meas[up] <= planned[h] * ORIENT_OVER and (best is None or fit < best[0]):
                best = (fit, "lying", h)
        elif meas[up] > planned[up] and shrink >= ORIENT_STOOD and planned[h] / planned[up] >= ORIENT_ANISO:
            fit = _log_ratio(meas[up], planned[h])  # the planned h extent now runs up
            if fit <= ORIENT_FIT and meas[h] <= planned[up] * ORIENT_OVER and (best is None or fit < best[0]):
                best = (fit, "stood", h)
    data = {"kind": "orientation", "planned_up_m": float(planned[up]), "measured_up_m": float(meas[up]),
            "frame": language_frame(language).value}
    if best is not None:
        _, pose, h = best
        k = 3 - up - h
        # a disagreement with the PLANNED box, phrased as one: a plan that boxed a wall clock
        # flat makes an upright build read "stood", and the judge treats ERROR text as fact
        verb = "is lying down relative to the planned box" if pose == "lying" else "stands on end relative to the planned box"
        return GateFinding(
            gate=GATE, severity=Severity.ERROR, target="overall",
            message=f"object {verb}: planned {planned[up] * 100:.1f} cm tall ({_AXES[up]}), measured "
                    f"{meas[up] * 100:.1f} cm tall with {planned[h] * 100:.1f} cm along {_AXES[h]} planned "
                    f"and {meas[h] * 100:.1f} cm measured — the {_AXES[up]} and {_AXES[h]} extents are swapped",
            fix_hint=f"rotate the whole object 90° about {_AXES[k]} so its height runs along {_AXES[up]} "
                     f"({label}); do not resize parts to fit the box",
            data={**data, "best_axis": _AXES[h], "pose": pose},
        )
    h1, h2 = horizontal
    if (_log_ratio(meas[up], planned[up]) <= ORIENT_THIRD
            and max(planned[h1] / planned[h2], planned[h2] / planned[h1]) >= ORIENT_ANISO
            and _log_ratio(meas[h1], planned[h2]) <= ORIENT_FIT and _log_ratio(meas[h2], planned[h1]) <= ORIENT_FIT
            and _log_ratio(meas[h1], planned[h1]) >= ORIENT_LYING and _log_ratio(meas[h2], planned[h2]) >= ORIENT_LYING):
        wide = h1 if planned[h1] > planned[h2] else h2
        return GateFinding(
            gate=GATE, severity=Severity.WARN, target="overall",
            message=f"object is turned 90° about {_AXES[up]}: planned {planned[h1] * 100:.1f}×{planned[h2] * 100:.1f} cm "
                    f"({_AXES[h1]}×{_AXES[h2]}), measured {meas[h1] * 100:.1f}×{meas[h2] * 100:.1f} cm — its front "
                    f"faces sideways in the az-0 view",
            fix_hint=f"rotate the whole object 90° about {_AXES[up]} so the {planned[wide] * 100:.1f} cm side runs "
                     f"along {_AXES[wide]} ({label})",
            data={**data, "best_axis": _AXES[wide], "pose": "turned"},
        )
    return None


# --------------------------------------------------------------------------- gate
def _check_object_plan(m: Measurement, plan: StaticPlan, language: str, tol_m: float) -> list[GateFinding]:
    findings: list[GateFinding] = []
    matched, extra = match_parts(plan.parts, m.parts)
    for pp in plan.parts:
        rows = matched[pp.name]
        target = pp.name
        if not rows:
            findings.append(GateFinding(
                gate=GATE, severity=Severity.ERROR, target=target,
                message=f"plan part '{pp.name}' is missing from the GLB",
                fix_hint=f"create a part named exactly '{pp.name}'" + (f" (×{pp.instances} as {pp.name}_0..{pp.instances - 1})" if pp.instances > 1 else "")
                + f" — {pp.role}; planned size {fmt_extent_cm(pp.bbox.extents)} cm",
                data={"expected_instances": pp.instances},
            ))
            continue
        box = plan_bbox_to_glb(pp.bbox, language)
        if pp.instances > 1 and len(rows) != pp.instances:
            findings.append(GateFinding(
                gate=GATE, severity=Severity.WARN, target=target,
                message=f"'{pp.name}': found {len(rows)} instance(s), plan asks for {pp.instances}",
                fix_hint=f"name the copies {pp.name}_0 .. {pp.name}_{pp.instances - 1}",
                data={"found": len(rows), "expected": pp.instances},
            ))
        if len(rows) == 1 and pp.instances == 1:
            d = _box_delta(np.asarray(rows[0].bbox_min), np.asarray(rows[0].bbox_max), box, tol_m)
            findings.extend(_box_findings(target, d, box, what=f"part '{pp.name}'", language=language))
        else:
            # instances: each copy should have the planned extents; the centre is unknowable
            per = [_box_delta(np.asarray(r.bbox_min), np.asarray(r.bbox_max), box, tol_m) for r in rows]
            union = _box_delta(np.min([r.bbox_min for r in rows], axis=0), np.max([r.bbox_max for r in rows], axis=0), box, tol_m)
            worst_each = max(per, key=lambda d: float(np.max(np.abs(d.extents) / d.tol)))
            # accept whichever reading (per-instance or union) fits the plan better
            if float(np.max(np.abs(union.extents) / union.tol)) < float(np.max(np.abs(worst_each.extents) / worst_each.tol)):
                findings.extend(_box_findings(target, union, box, what=f"'{pp.name}' (all instances)", language=language, check_center=False))
            else:
                findings.extend(_box_findings(target, worst_each, box, what=f"'{pp.name}' (each instance)", language=language, check_center=False))
    # overall bbox
    ob = plan_bbox_to_glb(plan.overall_bbox, language)
    d = _box_delta(np.asarray(m.bbox_min), np.asarray(m.bbox_max), ob, tol_m)
    findings.extend(_box_findings("overall", d, ob, what="overall", language=language))
    if (orient := _orientation_finding(m, plan, language)) is not None:
        findings.append(orient)
    # ground + footprint
    if abs(m.ground_gap_m) > tol_m:
        where = "above" if m.ground_gap_m > 0 else "below"
        findings.append(GateFinding(
            gate=GATE, severity=Severity.WARN if abs(m.ground_gap_m) <= ERROR_FACTOR * tol_m else Severity.ERROR,
            target="overall", message=f"object floats {abs(m.ground_gap_m) * 1000:.1f} mm {where} the ground",
            fix_hint=f"translate everything by {-m.ground_gap_m:+.4f} m along the up axis so the lowest point is at 0",
            data={"ground_gap_m": m.ground_gap_m},
        ))
    if m.footprint_offset_m > max(tol_m, REL_TOL * max(m.extents[0], m.extents[2], 1e-9)):
        findings.append(GateFinding(
            gate=GATE, severity=Severity.WARN, target="overall",
            message=f"footprint centre is {m.footprint_offset_m * 100:.1f} cm off the up axis",
            fix_hint=f"translate everything by {fmt_vec(glb_vec_to_plan((-m.center[0], 0.0, -m.center[2]), language))} m "
                     f"({frame_label(language)}) to centre the footprint",
            data={"footprint_offset_m": m.footprint_offset_m, "frame": language_frame(language).value},
        ))
    for r in extra:
        findings.append(GateFinding(gate=GATE, severity=Severity.INFO, target=r.name,
                                    message=f"GLB part '{r.name}' is not in the plan ({fmt_extent_cm(np.subtract(r.bbox_max, r.bbox_min))} cm)"))
    return findings


def _check_scene_plan(m: Measurement, plan: ScenePlan, language: str, tol_m: float) -> list[GateFinding]:
    findings: list[GateFinding] = []
    names = {to_snake(p.name) for p in m.parts}
    for z in plan.zones:
        if to_snake(z.name) not in names:
            findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=z.name,
                                        message=f"zone group '{z.name}' not found in the exported scene",
                                        fix_hint=f"put the zone's objects under a THREE.Group named '{z.name}'"))
    b = plan_bbox_to_glb(plan.bounds, language)
    lo, hi = np.asarray(b.min), np.asarray(b.max)
    over = np.maximum(lo - np.asarray(m.bbox_min), 0) + np.maximum(np.asarray(m.bbox_max) - hi, 0)
    if float(np.max(over)) > max(tol_m, REL_TOL * float(np.max(b.extents))):
        over_plan = glb_vec_to_plan(over, language, extents=True)
        findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target="bounds",
                                    message=f"scene geometry exceeds the planned bounds by {_fmt_delta(over_plan)}",
                                    fix_hint=f"keep everything inside centre {plan.bounds.center}, extents {plan.bounds.extents} m "
                                             f"({frame_label(language)})",
                                    data={"overshoot_m": over_plan.tolist(), "frame": language_frame(language).value}))
    return findings


def check_contract(measurement: Measurement, plan: Plan, *, language: str, tol_m: float = BBOX_TOLERANCE_M) -> GateReport:
    """Compare ``measurement`` (from the GLB) with ``plan`` authored in ``language``'s frame."""
    t0 = time.time()
    if isinstance(plan, ScenePlan):
        findings = _check_scene_plan(measurement, plan, language, tol_m)
    else:
        findings = _check_object_plan(measurement, plan, language, tol_m)
    if not findings:
        findings.append(GateFinding(gate=GATE, severity=Severity.INFO, message="all plan parts present and within tolerance"))
    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))
