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
* not standing on the ground / footprint off-centre → WARN
* GLB parts the plan never mentioned → INFO
* ScenePlan: zone groups missing → WARN; model outside ``bounds`` → WARN
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import numpy as np

from codeverse.contracts.artifacts import (
    GateFinding,
    GateReport,
    Measurement,
    PartMeasure,
    Severity,
)
from codeverse.contracts.plan import BBox, PartPlan, Plan, ScenePlan, StaticPlan
from codeverse.conventions import BBOX_TOLERANCE_M, LANGUAGE_FRAME, Frame, to_snake
from codeverse.spatial.measure import fmt_extent_cm, fmt_vec

GATE = "contract"
#: relative tolerance on extents (fraction of the plan's extent)
REL_TOL = 0.10
#: a delta beyond ERROR_FACTOR × tolerance is an error, not a warning
ERROR_FACTOR = 3.0
_AXES = ("x", "y", "z")


# --------------------------------------------------------------------------- frames
def language_frame(language: str) -> Frame:
    """Authoring frame of ``language`` (unknown / empty → the GLB frame itself)."""
    return LANGUAGE_FRAME.get(str(language), Frame.Y_UP_POS_Z_FRONT)


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
    """Inverse of the glTF mapping for a GLB-frame vector: Z-up languages get
    ``(x, y, z) → (x, -z, y)``; ``extents`` (per-axis sizes / size deltas) are only
    permuted, never sign-flipped.  Hints must be written in the frame the agent codes in."""
    v = np.asarray(v, dtype=float)
    if language_frame(language) is Frame.Y_UP_POS_Z_FRONT:
        return v
    return np.array([v[0], v[2] if extents else -v[2], v[1]])


# --------------------------------------------------------------------------- matching
def _instance_re(snake: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(snake)}(?:[_.-]?\d{{1,3}})?$")


def match_parts(plan_parts: list[PartPlan], measured: list[PartMeasure]) -> tuple[dict[str, list[PartMeasure]], list[PartMeasure]]:
    """Map each plan part → measured rows (``Name``, ``Name_0``… accepted); plus unmatched rows.

    Exact names are claimed FIRST, and the ``Name_0..Name_N`` instance pass never takes
    a node that another plan part names exactly.  ``_instance_re("shelf")`` matches
    ``shelf2``, so a plan of ``Shelf`` + ``Shelf2`` used to have ``Shelf`` swallow the
    ``Shelf2`` node before ``Shelf2`` was considered — a false "missing from the GLB"
    ERROR and a real 0.75 judge cap on geometry that matched the plan exactly.
    ``Shelf``/``Shelf2``, ``Slat``/``Slat1``, ``Tier``/``Tier2`` are ordinary planner
    output for PascalCase part names.
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
        pat = _instance_re(to_snake(pp.name))
        hits = [m for n, m in list(remaining.items())
                if to_snake(n) not in reserved and pat.match(to_snake(n))]
        for m in hits:
            remaining.pop(m.name, None)
        matched[pp.name].extend(hits)
    return matched, list(remaining.values())


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
    passed = not any(f.severity == Severity.ERROR for f in findings)
    if passed and not findings:
        findings.append(GateFinding(gate=GATE, severity=Severity.INFO, message="all plan parts present and within tolerance"))
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.time() - t0) * 1000))
