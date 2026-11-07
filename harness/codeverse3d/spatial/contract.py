"""Contract gate: does the built GLB honour the plan's part list and boxes?"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from codeverse3d.contracts.artifacts import (
    PLACEHOLDER_PART,
    GateFinding,
    GateReport,
    Measurement,
    PartMeasure,
    Severity,
)
from codeverse3d.contracts.plan import BBox, PartPlan, Plan, StaticPlan
from codeverse3d.contracts.spec import FIXED_PARTS_GATE as FIXED_GATE
from codeverse3d.contracts.spec import FixedPart
from codeverse3d.conventions import (
    BBOX_TOLERANCE_M,
    CONTACT_GAP_M,
    FRAME_AXES,
    GROUND_TOL_M,
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
REL_TOL = 0.10
ERROR_FACTOR = 3.0
_AXES = ("x", "y", "z")

ORIENT_FIT = float(np.log(1.25))
ORIENT_LYING = float(np.log(1.5))
ORIENT_STOOD = float(np.log(2.0))
ORIENT_THIRD = float(np.log(1.5))
ORIENT_ANISO = 1.5
ORIENT_OVER = 1.4

PLACEHOLDER_MAX_TRIS = 12
PLACEHOLDER_TOL_M = 0.001
PLACEHOLDER_MIN_FILL = 1.0 - 1e-3


# --------------------------------------------------------------------------- frames
def frame_label(language: str) -> str:
    """Short frame tag for fix hints, e.g."""
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
    """``conventions.to_authoring_frame`` as an array."""
    return np.asarray(to_authoring_frame(v, language, extents=extents), dtype=float)


# --------------------------------------------------------------------------- matching
def match_parts(plan_parts: list[PartPlan], measured: list[PartMeasure]) -> tuple[dict[str, list[PartMeasure]], list[PartMeasure]]:
    """Map each plan part → measured rows (``Name``, ``Name_0``… accepted)."""
    remaining = {m.name: m for m in measured}
    matched: dict[str, list[PartMeasure]] = {pp.name: [] for pp in plan_parts}
    reserved = {to_snake(pp.name) for pp in plan_parts}
    for pp in plan_parts:
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


def is_placeholder(rows: list[PartMeasure], plan_box: BBox) -> bool:
    """Is this plan part (its measured rows, one per instance) still the skeleton's box?
    ``plan_box`` is in the GLB frame (:func:`plan_bbox_to_glb`)."""
    if not rows:
        return False
    ext, ctr = np.asarray(plan_box.extents, dtype=float), np.asarray(plan_box.center, dtype=float)
    for r in rows:
        if not 0 < r.tri_count <= PLACEHOLDER_MAX_TRIS or not r.watertight or r.volume_m3 is None:
            return False
        lo, hi = np.asarray(r.bbox_min, dtype=float), np.asarray(r.bbox_max, dtype=float)
        if np.any(np.abs((hi - lo) - ext) > PLACEHOLDER_TOL_M):
            return False
        if abs(r.volume_m3) < PLACEHOLDER_MIN_FILL * float(np.prod(hi - lo)):
            return False
        if len(rows) == 1 and np.any(np.abs((hi + lo) / 2 - ctr) > PLACEHOLDER_TOL_M):
            return False
    return True


def planned_joins(plan: Plan | None, measurement: Measurement | None) -> list[tuple[str, tuple[str, ...]]]:
    """The plan's ``attach_to`` edges spelled in the GLB's part names, for the connectivity
    ledger."""
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
    """Euclidean distance between two ``(min, max)`` corner boxes, 0 when they overlap."""
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


def _box_findings(target: str, d: _BoxDelta, plan: BBox, *, what: str, kind: str, language: str,
                  check_center: bool = True) -> list[GateFinding]:
    """WARN/ERROR findings for one box comparison (empty when within tolerance)."""
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
        data={"kind": kind, "delta_center_m": d_ctr.tolist(), "delta_extents_m": d_ext.tolist(),
              "tol_m": glb_vec_to_plan(d.tol, language, extents=True).tolist(), "frame": language_frame(language).value},
    ))
    return out


def _log_ratio(a: float, b: float) -> float:
    return abs(float(np.log(a / b)))


def _orientation_finding(m: Measurement, plan: StaticPlan, language: str) -> GateFinding | None:
    """Is the object lying down / stood on end (ERROR) or turned 90° about up (WARN)? Everything
    is in the plan frame."""
    planned = np.asarray(plan.overall_bbox.extents, dtype=float)
    meas = glb_vec_to_plan(m.extents, language, extents=True)
    if np.any(planned <= 1e-6) or np.any(meas <= 1e-6):
        return None
    up = int(np.argmax(FRAME_AXES[language_frame(language)]["up"]))
    label = frame_label(language)
    horizontal = [a for a in range(3) if a != up]
    best: tuple[float, str, int] | None = None  # (fit, pose, h)
    for h in horizontal:
        k = 3 - up - h
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
                data={"kind": "missing_part", "expected_instances": pp.instances},
            ))
            continue
        box = plan_bbox_to_glb(pp.bbox, language)
        if is_placeholder(rows, box):
            joined = f", joined to {pp.attach_to}" if pp.attach_to else ""
            findings.append(GateFinding(
                gate=GATE, severity=Severity.ERROR, target=target,
                message=f"plan part '{pp.name}' is still the skeleton's placeholder box "
                        f"({max(r.tri_count for r in rows)} triangles at its plan box)",
                fix_hint=f"build the real '{pp.name}' ({pp.role}) in place of the box, at its planned size "
                         f"{fmt_extent_cm(pp.bbox.extents)} cm{joined}",
                data={"kind": PLACEHOLDER_PART, "tri_count": sum(r.tri_count for r in rows), "instances": len(rows)},
            ))
            continue
        if pp.instances > 1 and len(rows) != pp.instances:
            findings.append(GateFinding(
                gate=GATE, severity=Severity.WARN, target=target,
                message=f"'{pp.name}': found {len(rows)} instance(s), plan asks for {pp.instances}",
                fix_hint=f"name the copies {pp.name}_0 .. {pp.name}_{pp.instances - 1}",
                data={"kind": "instance_count", "found": len(rows), "expected": pp.instances},
            ))
        if len(rows) == 1 and pp.instances == 1:
            d = _box_delta(np.asarray(rows[0].bbox_min), np.asarray(rows[0].bbox_max), box, tol_m)
            findings.extend(_box_findings(target, d, box, what=f"part '{pp.name}'", kind="part_bbox", language=language))
        else:
            per = [_box_delta(np.asarray(r.bbox_min), np.asarray(r.bbox_max), box, tol_m) for r in rows]
            union = _box_delta(np.min([r.bbox_min for r in rows], axis=0), np.max([r.bbox_max for r in rows], axis=0), box, tol_m)
            worst_each = max(per, key=lambda d: float(np.max(np.abs(d.extents) / d.tol)))
            if float(np.max(np.abs(union.extents) / union.tol)) < float(np.max(np.abs(worst_each.extents) / worst_each.tol)):
                findings.extend(_box_findings(target, union, box, what=f"'{pp.name}' (all instances)", kind="instance_bbox", language=language, check_center=False))
            else:
                findings.extend(_box_findings(target, worst_each, box, what=f"'{pp.name}' (each instance)", kind="instance_bbox", language=language, check_center=False))
    ob = plan_bbox_to_glb(plan.overall_bbox, language)
    d = _box_delta(np.asarray(m.bbox_min), np.asarray(m.bbox_max), ob, tol_m)
    findings.extend(_box_findings("overall", d, ob, what="overall", kind="overall_bbox", language=language))
    if (orient := _orientation_finding(m, plan, language)) is not None:
        findings.append(orient)
    if abs(m.ground_gap_m) > GROUND_TOL_M:
        where = "above" if m.ground_gap_m > 0 else "below"
        findings.append(GateFinding(
            gate=GATE, severity=Severity.WARN if abs(m.ground_gap_m) <= ERROR_FACTOR * GROUND_TOL_M else Severity.ERROR,
            target="overall", message=f"object floats {abs(m.ground_gap_m) * 1000:.1f} mm {where} the ground",
            fix_hint=f"translate everything by {-m.ground_gap_m:+.4f} m along the up axis so the lowest point is at 0",
            data={"kind": "ground_gap", "ground_gap_m": m.ground_gap_m},
        ))
    if m.footprint_offset_m > max(tol_m, REL_TOL * max(m.extents[0], m.extents[2], 1e-9)):
        findings.append(GateFinding(
            gate=GATE, severity=Severity.WARN, target="overall",
            message=f"footprint centre is {m.footprint_offset_m * 100:.1f} cm off the up axis",
            fix_hint=f"translate everything by {fmt_vec(glb_vec_to_plan((-m.center[0], 0.0, -m.center[2]), language))} m "
                     f"({frame_label(language)}) to centre the footprint",
            data={"kind": "footprint_offset", "footprint_offset_m": m.footprint_offset_m, "frame": language_frame(language).value},
        ))
    for r in extra:
        findings.append(GateFinding(gate=GATE, severity=Severity.INFO, target=r.name,
                                    message=f"GLB part '{r.name}' is not in the plan ({fmt_extent_cm(np.subtract(r.bbox_max, r.bbox_min))} cm)"))
    return findings


def check_contract(measurement: Measurement, plan: StaticPlan, *, language: str, tol_m: float = BBOX_TOLERANCE_M) -> GateReport:
    """Compare ``measurement`` (from the GLB) with an object ``plan`` authored in ``language``'s
    frame."""
    t0 = time.time()
    findings = _check_object_plan(measurement, plan, language, tol_m)
    if not findings:
        findings.append(GateFinding(gate=GATE, severity=Severity.INFO, message="all plan parts present and within tolerance"))
    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# --------------------------------------------------------------------------- fixed parts
#: a fixed part is built by code the agent may not change, so its box must come out as declared: any face of
#: it further than this from the declared box is an ERROR
FIXED_TOL_M = 0.05


def check_fixed_parts(measurement: Measurement, fixed: Sequence[FixedPart]) -> GateReport:
    """The spec's fixed parts (``Spec.fixed_parts``) that build a part, measured in the GLB: present under
    their name, and inside ``FIXED_TOL_M`` of the declared box.  The ``contract*`` cap applies to the
    ERRORs and they become refine tasks; a part rescaled or moved in the entry file is caught here even
    though the read-only file itself is untouched."""
    t0 = time.time()
    findings: list[GateFinding] = []
    by_name = {to_snake(p.name): p for p in measurement.parts}
    for f in fixed:
        if not f.name:
            continue
        row = by_name.get(to_snake(f.name))
        if row is None:
            findings.append(GateFinding(
                gate=FIXED_GATE, severity=Severity.ERROR, target=f.name,
                message=f"fixed part '{f.name}' ({f.path}) is missing from the GLB",
                fix_hint=f"import {f.export or 'its builder'} from {f.path} and add what it returns to the root, unchanged",
                data={"kind": "fixed_part_missing"}))
            continue
        if f.bbox_min is None or f.bbox_max is None:
            continue
        lo = np.subtract(row.bbox_min, f.bbox_min)
        hi = np.subtract(row.bbox_max, f.bbox_max)
        worst = float(np.max(np.abs(np.concatenate([lo, hi]))))
        if worst > FIXED_TOL_M:
            findings.append(GateFinding(
                gate=FIXED_GATE, severity=Severity.ERROR, target=f.name,
                message=(f"fixed part '{f.name}' measures {fmt_vec(row.bbox_min)}..{fmt_vec(row.bbox_max)}, declared "
                         f"{fmt_vec(f.bbox_min)}..{fmt_vec(f.bbox_max)} (off by {worst:.2f} m): it was moved, scaled or edited"),
                fix_hint=f"add what {f.export or 'its builder'} returns to the root unchanged: no transform, no "
                         "post-processing of its children; dress beside it",
                data={"kind": "fixed_part_bbox", "off_m": round(worst, 3)}))
    if not findings:
        findings.append(GateFinding(gate=FIXED_GATE, severity=Severity.INFO,
                                    message="fixed parts present and as declared"))
    return GateReport.of(FIXED_GATE, findings, duration_ms=int((time.time() - t0) * 1000))
