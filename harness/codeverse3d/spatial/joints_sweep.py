"""Pose sweep QC on a :class:`Robot`: overlaps, contacts, floating links, motion direction.

Collision bodies live in :mod:`joints_collide`."""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from pydantic import BaseModel, Field

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.conventions import CONTACT_GAP_M
from codeverse3d.spatial import joints_collide as collide
from codeverse3d.spatial.joints_model import Robot, UrdfError, fk, link_world_meshes, load_urdf
from codeverse3d.spatial.joints_poses import pose_label, pose_samples
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

SWEEP_GATE = "joint_sweep"
#: rest penetration beyond this fails the URDF build; a moving child must be this close to its parent
REST_PENETRATION_MAX_M = 0.005
HINGE_CLEARANCE_M = 0.01


# ------------------------------------------------------------------ report types
class Overlap(BaseModel):
    a: str
    b: str
    depth_m: float = Field(description="max distance of a penetrating surface point from the other surface")
    approx: bool = Field(default=False, description="depth estimated on non-watertight meshes")
    rigid: bool = Field(default=False, description="only fixed joints between the links: the overlap is structural "
                                                   "(a weld), identical in every pose — judged with the rest policy")


class FloatingLink(BaseModel):
    """A link that is not attached to its parent link the way its joint type requires."""

    link: str
    nearest: str = Field(default="", description="the parent link it should attach to")
    gap_m: float = Field(default=math.inf, description="closest distance to the parent link")
    joint_type: str = ""


class PoseReport(BaseModel):
    label: str
    q: dict[str, float]
    overlaps: list[Overlap] = Field(default_factory=list)
    floating: list[FloatingLink] = Field(default_factory=list)


class SweepReport(BaseModel):
    tol_m: float
    contact_gap_m: float
    per_pose: list[PoseReport]
    rest_max_penetration_m: float = Field(default=0.0, description="deepest rest-pose or rigid (welded) overlap")


class MotionCheck(BaseModel):
    joint: str
    expected: str
    observed_dir: tuple[float, float, float]
    ok: bool
    message: str
    cos: float = 0.0
    suggested_axis: tuple[float, float, float] | None = None


# ------------------------------------------------------------------ sweep
def sweep_collisions(
    robot: Robot,
    poses: list[dict[str, float]],
    *,
    tol_m: float = 0.002,
    contact_gap_m: float = CONTACT_GAP_M,
    hinge_clearance_m: float = HINGE_CLEARANCE_M,
) -> SweepReport:
    """Collide every link pair in every pose."""
    names = robot.meshed_links()
    if not names:
        raise UrdfError("sweep_collisions: robot has no link meshes")
    bodies = {n: collide.LinkBody(n, robot.links[n].mesh) for n in names}  # type: ignore[arg-type]
    per_pose: list[PoseReport] = []
    comps = collide.components(list(robot.links), {(j.parent, j.child) for j in robot.joints.values() if j.type == "fixed"})
    rigid_group = {n: i for i, comp in enumerate(comps) for n in comp}
    labels = [pose_label(robot, q) for q in poses]
    rigid_pose = labels.index("rest") if "rest" in labels else 0

    for idx, q in enumerate(poses):
        T = fk(robot, q)
        for n, body in bodies.items():
            body.set_pose(T[n])
        overlaps: list[Overlap] = []
        nearest: dict[str, dict[str, float]] = {n: {} for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                ba, bb = bodies[a], bodies[b]
                gap_aabb = collide.aabb_gap(ba.aabb, bb.aabb)
                if gap_aabb > max(contact_gap_m, hinge_clearance_m * 3):
                    nearest[a][b] = nearest[b][a] = gap_aabb  # lower bound is enough this far apart
                    continue
                rigid = rigid_group[a] == rigid_group[b]
                if collide.collide(ba, bb) or (gap_aabb <= 0.0 and collide.contained(ba, bb)):
                    dist = 0.0
                    if rigid and idx != rigid_pose:
                        pass
                    else:
                        depth, approx = collide.penetration(ba, bb)
                        if round(depth, 4) > tol_m:
                            overlaps.append(
                                Overlap(a=a, b=b, depth_m=round(depth, 6), approx=approx, rigid=rigid)
                            )
                else:
                    dist = collide.distance(ba, bb)
                nearest[a][b] = nearest[b][a] = dist
        floating: list[FloatingLink] = []
        if pose_label(robot, q) == "rest":
            for n in names:
                j = robot.parent_joint(n)
                if j is None or j.parent not in bodies:
                    continue
                d = nearest[n].get(j.parent, math.inf)
                if j.type == "fixed":
                    thr = contact_gap_m
                elif j.type == "prismatic":
                    if collide.aabb_gap(bodies[n].aabb, bodies[j.parent].aabb) <= 0.0:
                        continue
                    thr = hinge_clearance_m
                else:  # revolute / continuous / planar / floating
                    thr = hinge_clearance_m
                if d > thr:
                    floating.append(FloatingLink(link=n, nearest=j.parent, joint_type=j.type,
                                                 gap_m=round(d, 6) if math.isfinite(d) else math.inf))
        per_pose.append(PoseReport(label=labels[idx], q=dict(q), overlaps=overlaps, floating=floating))

    rest_max = max((o.depth_m for pr in per_pose for o in pr.overlaps if pr.label == "rest" or o.rigid), default=0.0)
    return SweepReport(tol_m=tol_m, contact_gap_m=contact_gap_m, per_pose=per_pose, rest_max_penetration_m=rest_max)


# ------------------------------------------------------------------ gate findings
def sweep_findings(report: SweepReport, *, rest_max_m: float = REST_PENETRATION_MAX_M,
                   hinge_clearance_m: float = HINGE_CLEARANCE_M) -> list[GateFinding]:
    """Turn a sweep report into gate findings: rest penetration beyond ``rest_max_m``, moved-pose overlaps, and floating links beyond ``hinge_clearance_m``."""
    gate = SWEEP_GATE
    out: list[GateFinding] = []
    for pr in report.per_pose:
        for o in pr.overlaps:
            at_rest = pr.label == "rest" or o.rigid
            sev = Severity.ERROR if (not at_rest or o.depth_m > rest_max_m) else Severity.WARN
            out.append(GateFinding(
                gate=gate, severity=sev, target=f"{o.a}|{o.b}",
                message=f"links '{o.a}' and '{o.b}' overlap by {o.depth_m*1000:.1f} mm at pose {pr.label}"
                        + (f" (pose q={pr.q})" if pr.q else "") + (" [approx: non-watertight mesh]" if o.approx else ""),
                fix_hint=(f"The meshes in model.py interpenetrate{'' if pr.label == 'rest' else ' (rigidly joined links: same in every pose)'}: "
                          f"shrink/move one of '{o.a}', '{o.b}' so they touch (≤ {report.tol_m*1000:.0f} mm) instead of overlapping."
                          if at_rest else
                          f"Moving joint(s) {sorted(pr.q)} drives '{o.a}' into '{o.b}'. Either move the pivot/axis in robot.urdf so the part swings/slides clear, shrink the limits, or carve the clearance in model.py."),
                data={"kind": "penetration", "pose": pr.q, "depth_m": o.depth_m},
            ))
        for f in pr.floating:
            fixed = f.joint_type == "fixed"
            sev = Severity.ERROR if (fixed or f.gap_m > hinge_clearance_m * 3) else Severity.WARN
            what = {"fixed": "is rigidly attached to", "prismatic": "slides in", "revolute": "hinges on",
                    "continuous": "rotates on"}.get(f.joint_type, "attaches to")
            out.append(GateFinding(
                gate=gate, severity=sev, target=f.link,
                message=f"link '{f.link}' {what} '{f.nearest}' ({f.joint_type} joint) but the meshes are "
                        f"{f.gap_m*1000:.1f} mm apart at rest — nothing physically connects them",
                fix_hint=(f"Extend '{f.link}' so it touches/overlaps '{f.nearest}' by ≥ {report.contact_gap_m*1000:.0f} mm (a fixed child must sit on its parent)."
                          if fixed else
                          f"Move the hinge/rail edge of '{f.link}' within {hinge_clearance_m*1000:.0f} mm of '{f.nearest}' (or model the hinge/bracket/runner) and put the joint pivot on that edge in robot.urdf."),
                data={"kind": "unattached", "pose": pr.q, "gap_m": f.gap_m, "parent": f.nearest, "joint_type": f.joint_type},
            ))
    return out


MAX_PAIR_FINDINGS = 8


def aggregate_findings(findings: list[GateFinding]) -> list[GateFinding]:
    """One finding per link pair (or per floating link), the worst pose first."""
    groups: dict[tuple[str, str | None], list[GateFinding]] = {}
    order: list[tuple[str, str | None]] = []
    for f in findings:
        key = (f.data.get("kind", ""), f.target)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    merged: list[GateFinding] = []
    for key in order:
        fs = groups[key]
        if len(fs) == 1 or key[0] != "penetration":
            merged.extend(fs if key[0] != "penetration" else [fs[0]])
            continue
        worst = max(fs, key=lambda f: float(f.data.get("depth_m") or 0.0))
        rest = [f for f in fs if not f.data.get("pose")]
        moved = [f for f in fs if f.data.get("pose")]
        sev = Severity.ERROR if any(f.severity == Severity.ERROR for f in fs) else Severity.WARN
        worst_pose = worst.data.get("pose") or {}
        where = "at rest" if not worst_pose else "at " + ", ".join(f"{k}={v:.2f}" for k, v in worst_pose.items())
        msg = (f"links '{key[1]}' overlap in {len(fs)} of the sampled poses (worst {float(worst.data.get('depth_m') or 0) * 1000:.1f} mm "
               f"{where}" + (f"; {len(rest)} at rest" if rest and moved else "") + ")")
        merged.append(GateFinding(gate=worst.gate, severity=sev, target=worst.target, message=msg, fix_hint=worst.fix_hint,
                                  data={**worst.data, "n_poses": len(fs), "poses": [f.data.get("pose") for f in fs][:12],
                                        "max_depth_m": float(worst.data.get("depth_m") or 0.0)}))
    errors = sorted([f for f in merged if f.severity == Severity.ERROR],
                    key=lambda f: -float(f.data.get("max_depth_m") or f.data.get("depth_m") or f.data.get("gap_m") or 0.0))
    warns = [f for f in merged if f.severity != Severity.ERROR]
    if len(errors) > MAX_PAIR_FINDINGS:
        extra = errors[MAX_PAIR_FINDINGS:]
        errors = errors[:MAX_PAIR_FINDINGS]
        warns.append(GateFinding(gate=extra[0].gate, severity=Severity.WARN, target="overall",
                                 message=f"{len(extra)} more overlapping pair(s) not listed: " + ", ".join(str(f.target) for f in extra[:10]),
                                 fix_hint="fix the listed pairs first; the rest are re-measured after the next build",
                                 data={"kind": "penetration_summary", "pairs": [f.target for f in extra]}))
    return errors + warns


def _pair_depth(robot: Robot, bodies: dict[str, Any], a: str, b: str, q: dict[str, float]) -> float:
    T = fk(robot, q)
    bodies[a].set_pose(T[a])
    bodies[b].set_pose(T[b])
    ba, bb = bodies[a], bodies[b]
    if collide.aabb_gap(ba.aabb, bb.aabb) > 0.0 or not (collide.collide(ba, bb) or collide.contained(ba, bb)):
        return 0.0
    return collide.penetration(ba, bb)[0]


def _clear_travel(clear_at: Any, lim: float, steps: int) -> float:
    """The joint value nearest ``lim`` (from rest, 0) with the pair clear at it and at 8 points before it."""
    ok, bad = 0.0, lim
    for _ in range(steps):
        mid = 0.5 * (ok + bad)
        ok, bad = (mid, bad) if clear_at(mid) else (ok, mid)
    while abs(ok) > 1e-6 and not all(clear_at(ok * k / 8) for k in range(1, 9)):
        ok *= 0.9
    return math.trunc(ok * 100) / 100  # inward to a round number


def name_clearing_limits(robot: Robot, findings: list[GateFinding], *, tol_m: float, steps: int = 10) -> None:
    """A moving overlap between the two links of ONE joint depends on that joint alone: bisect how far it can travel
    before they meet and put that limit in the fix hint, so one robot.urdf edit clears the ERROR."""
    for f in findings:
        if f.severity != Severity.ERROR or f.data.get("kind") != "penetration" or not f.data.get("pose") or "|" not in str(f.target):
            continue
        a, b = str(f.target).split("|", 1)
        j = next((j for j in robot.joints.values() if {j.parent, j.child} == {a, b} and j.type in ("revolute", "prismatic")
                  and j.mimic is None and j.lower is not None and j.upper is not None and j.lower <= 0.0 <= j.upper), None)
        if j is None or robot.links[a].mesh is None or robot.links[b].mesh is None:
            continue
        bodies = {n: collide.LinkBody(n, robot.links[n].mesh) for n in (a, b)}
        rest = _pair_depth(robot, bodies, a, b, {})
        if rest > tol_m:
            continue
        thr = 0.5 * (rest + tol_m)  # the sampled depth is noisy near tol_m: aim halfway between rest contact and tol_m
        clear: dict[str, float] = {}
        for side, lim in (("lower", j.lower), ("upper", j.upper)):
            assert lim is not None
            if abs(lim) < 1e-9 or _pair_depth(robot, bodies, a, b, {j.name: lim}) <= tol_m:
                continue
            clear[side] = _clear_travel(lambda v, j=j, a=a, b=b, bodies=bodies, thr=thr:
                                        _pair_depth(robot, bodies, a, b, {j.name: v}) <= thr, lim, steps)
        if clear:
            limits = " and ".join(f'{side}="{v:.2f}"' for side, v in clear.items())
            depth = float(f.data.get("max_depth_m") or f.data.get("depth_m") or 0.0)
            f.fix_hint = (f"Set '{j.name}' {limits} in robot.urdf — one edit, measured clear over that whole range (this "
                          f"pair's every pose depends on that joint alone). Reshape model.py instead ONLY when the brief "
                          f"names the full range, and then remove at least {depth * 1000 + 2:.0f} mm where they meet; "
                          f"geometry nudges smaller than that come back as the same ERROR.")
            f.data["clear_limits"] = {j.name: clear}


BURIED_FRACTION = 0.98
BURIED_SAMPLES = 200


def _inside_fraction(body: Any, points_world: np.ndarray) -> float:
    """Share of ``points_world`` inside any of ``body``'s islands (these LinkBodies are built
    from world meshes, so the pose transform is the identity)."""
    local = trimesh.transform_points(points_world, body.T_inv)
    mask = np.zeros(len(local), dtype=bool)
    for isl in body.islands:
        mask |= np.asarray(collide.inside_island(isl, local), dtype=bool)
    return float(mask.mean()) if len(mask) else 0.0


def buried_links(robot: Robot, *, samples: int = BURIED_SAMPLES, fraction: float = BURIED_FRACTION) -> list[GateFinding]:
    """Findings for links whose surface lies (almost) entirely inside another link at rest, so the part can neither be seen nor move."""
    meshes = link_world_meshes(robot, {})
    bodies = {n: collide.LinkBody(n, m) for n, m in meshes.items() if not m.is_empty and m.area > 0}
    out: list[GateFinding] = []
    for name in bodies:
        pts, _ = trimesh.sample.sample_surface(meshes[name], samples, seed=0)
        lo, hi = meshes[name].bounds
        for other, ob in bodies.items():
            if other == name:
                continue
            olo, ohi = meshes[other].bounds
            if not (np.all(olo <= lo + 1e-6) and np.all(hi <= ohi + 1e-6)):
                continue  # not even inside its box
            try:
                frac = _inside_fraction(ob, pts)
            except Exception:  # noqa: BLE001 — a degenerate mesh; skip, never fail the gate
                continue
            if frac >= fraction:
                out.append(GateFinding(
                    gate=SWEEP_GATE, severity=Severity.ERROR, target=name,
                    message=f"link '{name}' lies entirely inside '{other}' at rest ({frac * 100:.0f}% of its surface): "
                            f"it can neither be seen nor move",
                    fix_hint=f"move '{name}' outside '{other}' (or cut a pocket in '{other}' where it sits) so the part "
                             f"is visible in the rest pose; if it is really internal, merge it into '{other}'",
                    data={"kind": "buried", "inside": other, "fraction": round(frac, 3)}))
                break
    return out


# ------------------------------------------------------------------ the gate
def find_urdf(ws: Workspace) -> Path | None:
    """The BUILT ``artifacts/robot.urdf`` — or None, which means "run build first"."""
    urdf = ws.artifacts / "robot.urdf"
    return urdf if urdf.is_file() else None


def sweep_gate(ws: Workspace) -> tuple[GateReport, Robot | None]:
    """THE ``joint_sweep`` verdict on the built robot, and the robot (None when none loaded)."""
    urdf = find_urdf(ws)
    if urdf is None:
        return GateReport.of(SWEEP_GATE, [GateFinding(
            gate=SWEEP_GATE, severity=Severity.ERROR, target="robot.urdf", message="no robot.urdf found after build",
            fix_hint="write src/robot.urdf (native URDF) referencing meshes/<link>.glb for every link")]), None
    t0 = time.time()
    try:
        robot = load_urdf(urdf, ws.artifacts / "meshes")
        report = sweep_collisions(robot, pose_samples(robot))
    except UrdfError as e:
        return GateReport.of(SWEEP_GATE, [GateFinding(
            gate=SWEEP_GATE, severity=Severity.ERROR, target="robot.urdf", message=f"URDF sweep failed: {e}",
            fix_hint="fix robot.urdf so every link has a mesh under meshes/<link>.glb and joints form one tree")]), None
    findings = aggregate_findings(sweep_findings(report))
    try:
        name_clearing_limits(robot, findings, tol_m=report.tol_m)
    except Exception as e:  # noqa: BLE001 — a hint never fails the gate
        log.warning("limit bisection failed: %s", e)
    try:
        findings += buried_links(robot)
    except Exception as e:  # noqa: BLE001 — an extra check never fails the gate
        log.warning("buried-link check failed: %s", e)
    return GateReport.of(SWEEP_GATE, findings, duration_ms=int((time.time() - t0) * 1000)), robot


# ------------------------------------------------------------------ motion direction
_DIRS: dict[str, tuple[float, float, float]] = {
    "+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1),
    "right": (1, 0, 0), "left": (-1, 0, 0), "back": (0, 1, 0), "front": (0, -1, 0), "up": (0, 0, 1), "down": (0, 0, -1),
    "open_up": (0, 0, 1), "open_down": (0, 0, -1), "open_front": (0, -1, 0), "out": (0, -1, 0), "in": (0, 1, 0),
}


def motion_direction_check(robot: Robot, joint: str, expected: str, *, probe: float | None = None) -> MotionCheck:
    """Does the child's centroid move along ``expected`` (``'+z'``, ``'-y'``, ``'up'``,
    ``'front'``, ``'open_up'`` ...) when ``joint`` moves positively from rest? Revolute joints
    are judged by the child centroid's swing about the pivot, so a wrong sign is reported with the corrected axis."""
    key = expected.strip().lower()
    if key not in _DIRS:
        raise UrdfError(f"motion_direction_check: unknown direction {expected!r}; use one of {sorted(_DIRS)}")
    j = robot.joints.get(joint)
    if j is None or not j.movable:
        raise UrdfError(f"motion_direction_check: {joint!r} is not a movable joint")
    driven_by = None
    if j.mimic is not None:
        driven_by = j
        while driven_by.mimic is not None:
            driven_by = robot.joints[driven_by.mimic.joint]
    mesh = robot.links[j.child].mesh
    if mesh is None:
        raise UrdfError(f"motion_direction_check: link {j.child!r} has no mesh")
    src = driven_by or j          # the joint the probe is applied to
    if probe is None:
        hi = src.upper if src.upper is not None else math.pi
        lo = src.lower if src.lower is not None else -math.pi
        probe = hi if abs(hi) >= abs(lo) else lo
        if src.type != "prismatic":
            probe = math.copysign(min(abs(probe), 0.35), probe) if probe else 0.35
    drive = src.name
    c0 = trimesh.transform_points([mesh.centroid], fk(robot, {})[j.child])[0]
    c1 = trimesh.transform_points([mesh.centroid], fk(robot, {drive: probe})[j.child])[0]
    delta = c1 - c0
    n = float(np.linalg.norm(delta))
    d = delta / n if n > 1e-9 else delta
    want = np.asarray(_DIRS[key], dtype=float)
    cos = float(d @ want)
    ok = cos > 0.5
    suggested = None
    if not ok:
        R_joint = fk(robot, {})[j.child][:3, :3]
        if j.type == "prismatic":
            suggested = tuple(round(float(v), 3) for v in R_joint.T @ want)
        else:
            pivot = fk(robot, {})[j.child][:3, 3]
            r = c0 - pivot
            s = np.cross(r, want)
            n_s = float(np.linalg.norm(s))
            if n_s > 1e-6:
                suggested = tuple(round(float(v), 3) for v in R_joint.T @ (s / n_s))
    return MotionCheck(joint=joint, expected=expected, observed_dir=tuple(round(float(v), 4) for v in d), ok=ok,
                       cos=round(cos, 4), suggested_axis=suggested,
                       message=((f"{joint} (driven by {drive})" if driven_by is not None else f"{joint}")
                                + f": child '{j.child}' moves {tuple(round(float(v),3) for v in d)} for q={probe:+.3g}; "
                                f"expected {expected} ({'ok' if ok else 'WRONG — negate the axis; keep the limits'})"))
