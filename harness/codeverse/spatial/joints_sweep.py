"""Pose sweep QC on a :class:`Robot`: overlaps, contacts, floating links, motion
direction.  Collision bodies live in :mod:`joints_collide` (FCL for the boolean
collide / distance queries; deterministic per-island containment for the
penetration depth — FCL's mesh-mesh contact depths are per-triangle artefacts).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import trimesh
from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateFinding, Severity
from codeverse.conventions import CONTACT_GAP_M
from codeverse.spatial import joints_collide as collide
from codeverse.spatial.joints_model import Robot, UrdfError, fk
from codeverse.spatial.joints_poses import pose_label


# ------------------------------------------------------------------ report types
class Overlap(BaseModel):
    a: str
    b: str
    depth_m: float = Field(description="max distance of a penetrating surface point from the other surface")
    volume_m3: float | None = None
    approx: bool = Field(default=False, description="depth estimated on non-watertight meshes")
    rigid: bool = Field(default=False, description="only fixed joints between the links: the overlap is structural "
                                                   "(a weld), identical in every pose — judged with the rest policy")


class FloatingLink(BaseModel):
    """A link that is not physically attached to its PARENT link the way its
    joint type requires (fixed → touching; revolute/continuous → hinge edge
    within ``hinge_clearance_m``; prismatic → inserted into / touching the parent)."""

    link: str
    nearest: str = Field(default="", description="the parent link it should attach to")
    gap_m: float = Field(default=math.inf, description="closest distance to the parent link")
    joint_type: str = ""


class PoseReport(BaseModel):
    label: str
    q: dict[str, float]
    overlaps: list[Overlap] = Field(default_factory=list)
    floating: list[FloatingLink] = Field(default_factory=list)
    n_contacts: int = 0


class SweepSummary(BaseModel):
    n_poses: int
    n_links: int
    max_penetration_m: float = 0.0
    worst_pose: str = ""
    worst_pair: tuple[str, str] | None = None
    rest_max_penetration_m: float = 0.0
    overlapping_poses: list[str] = Field(default_factory=list)
    floating_links: list[str] = Field(default_factory=list, description="floating in ANY sampled pose")
    floating_at_rest: list[str] = Field(default_factory=list)
    link_islands: dict[str, int] = Field(default_factory=dict)
    backend: str = "fcl"


class SweepReport(BaseModel):
    tol_m: float
    contact_gap_m: float
    per_pose: list[PoseReport]
    summary: SweepSummary


class MotionCheck(BaseModel):
    joint: str
    expected: str
    observed_dir: tuple[float, float, float]
    ok: bool
    message: str


# ------------------------------------------------------------------ sweep
def _components(links: list[str], edges: set[tuple[str, str]]) -> dict[str, int]:
    parent = {n: n for n in links}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = {}
    out = {}
    for n in links:
        r = find(n)
        out[n] = roots.setdefault(r, len(roots))
    return out


def sweep_collisions(
    robot: Robot,
    poses: list[dict[str, float]],
    *,
    tol_m: float = 0.002,
    contact_gap_m: float = CONTACT_GAP_M,
    allow_pairs: tuple[tuple[str, str], ...] = (),
    volumes: bool = True,
    hinge_clearance_m: float = 0.01,
) -> SweepReport:
    """Collide every link pair in every pose.  Overlaps deeper than ``tol_m``
    are reported; at the rest pose every link is checked for attachment to its
    PARENT link according to its joint type (``floating`` = not attached)."""
    names = robot.meshed_links()
    if not names:
        raise UrdfError("sweep_collisions: robot has no link meshes")
    bodies = {n: collide.LinkBody(n, robot.links[n].mesh) for n in names}  # type: ignore[arg-type]
    allowed = {tuple(sorted(p)) for p in allow_pairs}
    per_pose: list[PoseReport] = []
    islands = {n: len(bodies[n].islands) for n in names}
    # links joined only by fixed joints never move relative to each other: their overlap is
    # structural and is measured once (at rest when the sweep has a rest pose, else first pose)
    rigid_group = _components(list(robot.links), {(j.parent, j.child) for j in robot.joints.values() if j.type == "fixed"})
    labels = [pose_label(robot, q) for q in poses]
    rigid_pose = labels.index("rest") if "rest" in labels else 0

    for idx, q in enumerate(poses):
        T = fk(robot, q)
        for n, body in bodies.items():
            body.set_pose(T[n])
        overlaps: list[Overlap] = []
        touching: set[tuple[str, str]] = set()
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
                        pass  # structural overlap, already measured in pose ``rigid_pose``
                    elif tuple(sorted((a, b))) not in allowed:
                        depth, approx = collide.penetration(ba, bb)
                        if round(depth, 4) > tol_m:  # 0.1 mm: float32 mesh coordinates carry no finer meaning
                            overlaps.append(
                                Overlap(a=a, b=b, depth_m=round(depth, 6), approx=approx, rigid=rigid,
                                        volume_m3=collide.intersection_volume(ba, bb) if volumes else None)
                            )
                else:
                    dist = collide.distance(ba, bb)
                nearest[a][b] = nearest[b][a] = dist
                if dist <= contact_gap_m:
                    touching.add((a, b))
        floating: list[FloatingLink] = []
        if pose_label(robot, q) == "rest":  # attachment is a structural (rest-pose) property
            for n in names:
                j = robot.parent_joint(n)
                if j is None or j.parent not in bodies:
                    continue
                d = nearest[n].get(j.parent, math.inf)
                if j.type == "fixed":
                    thr = contact_gap_m
                elif j.type == "prismatic":
                    if collide.aabb_gap(bodies[n].aabb, bodies[j.parent].aabb) <= 0.0:
                        continue  # inserted into the parent's envelope (drawer in cavity)
                    thr = hinge_clearance_m
                else:  # revolute / continuous / planar / floating
                    thr = hinge_clearance_m
                if d > thr:
                    floating.append(FloatingLink(link=n, nearest=j.parent, joint_type=j.type,
                                                 gap_m=round(d, 6) if math.isfinite(d) else math.inf))
        per_pose.append(PoseReport(label=labels[idx], q=dict(q), overlaps=overlaps, floating=floating,
                                   n_contacts=len(touching)))

    summary = _summarise(per_pose, len(names), islands)
    return SweepReport(tol_m=tol_m, contact_gap_m=contact_gap_m, per_pose=per_pose, summary=summary)


def _summarise(per_pose: list[PoseReport], n_links: int, islands: dict[str, int]) -> SweepSummary:
    s = SweepSummary(n_poses=len(per_pose), n_links=n_links, link_islands=islands, backend=collide.backend_name())
    floating_any: set[str] = set()
    for pr in per_pose:
        for o in pr.overlaps:
            if o.depth_m > s.max_penetration_m:
                s.max_penetration_m, s.worst_pose, s.worst_pair = o.depth_m, pr.label, (o.a, o.b)
            if pr.label == "rest" or o.rigid:  # structural overlaps count as rest penetration
                s.rest_max_penetration_m = max(s.rest_max_penetration_m, o.depth_m)
        if pr.overlaps:
            s.overlapping_poses.append(pr.label)
        floating_any.update(f.link for f in pr.floating)
        if pr.label == "rest":
            s.floating_at_rest = [f.link for f in pr.floating]
    s.floating_links = sorted(floating_any)
    return s


# ------------------------------------------------------------------ gate findings
def sweep_findings(report: SweepReport, *, rest_max_m: float = 0.005, hinge_clearance_m: float = 0.01) -> list[GateFinding]:
    """Turn a sweep report into gate findings (ERROR for rest penetration > ``rest_max_m``,
    overlaps in moved poses and floating links beyond ``hinge_clearance_m``; WARN otherwise)."""
    gate = "articulation"
    out: list[GateFinding] = []
    for pr in report.per_pose:
        for o in pr.overlaps:
            at_rest = pr.label == "rest" or o.rigid  # a weld between rigidly joined links is a rest-pose property
            sev = Severity.ERROR if (not at_rest or o.depth_m > rest_max_m) else Severity.WARN
            out.append(GateFinding(
                gate=gate, severity=sev, target=f"{o.a}|{o.b}",
                message=f"links '{o.a}' and '{o.b}' overlap by {o.depth_m*1000:.1f} mm at pose {pr.label}"
                        + (f" (pose q={pr.q})" if pr.q else "") + (" [approx: non-watertight mesh]" if o.approx else ""),
                fix_hint=(f"The meshes in model.py interpenetrate{'' if pr.label == 'rest' else ' (rigidly joined links: same in every pose)'}: "
                          f"shrink/move one of '{o.a}', '{o.b}' so they touch (≤ {report.tol_m*1000:.0f} mm) instead of overlapping."
                          if at_rest else
                          f"Moving joint(s) {sorted(pr.q)} drives '{o.a}' into '{o.b}'. Either move the pivot/axis in robot.urdf so the part swings/slides clear, shrink the limits, or carve the clearance in model.py."),
                data={"kind": "penetration", "pose": pr.q, "depth_m": o.depth_m, "volume_m3": o.volume_m3},
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


#: ERROR findings kept after aggregation (the deepest pairs); the rest fold into one WARN
MAX_PAIR_FINDINGS = 8


def aggregate_findings(findings: list[GateFinding]) -> list[GateFinding]:
    """One finding per link pair (or per floating link), the worst pose first.

    compare_art_v3 (2026-08-28): a run produced 59 penetration findings for a handful of
    pairs — one per sampled pose — and the refine prompt carried them as 59 lines the
    agent could not act on; the loop burnt its budget without converging.  Merge the
    poses of a pair into one line that says how many poses, the worst depth and where,
    keep the deepest ``MAX_PAIR_FINDINGS`` as ERRORs and summarise the remainder.
    """
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


# ------------------------------------------------------------------ motion direction
_DIRS: dict[str, tuple[float, float, float]] = {
    "+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1),
    "right": (1, 0, 0), "left": (-1, 0, 0), "back": (0, 1, 0), "front": (0, -1, 0), "up": (0, 0, 1), "down": (0, 0, -1),
    "open_up": (0, 0, 1), "open_down": (0, 0, -1), "open_front": (0, -1, 0), "out": (0, -1, 0), "in": (0, 1, 0),
}


def motion_direction_check(robot: Robot, joint: str, expected: str, *, probe: float | None = None) -> MotionCheck:
    """Does the child's centroid move along ``expected`` (``'+z'``, ``'-y'``, ``'up'``,
    ``'front'``, ``'open_up'`` ...) when ``joint`` moves positively from rest?
    Revolute joints are probed with a small angle so the initial tangent direction is tested."""
    key = expected.strip().lower()
    if key not in _DIRS:
        raise UrdfError(f"motion_direction_check: unknown direction {expected!r}; use one of {sorted(_DIRS)}")
    j = robot.joints.get(joint)
    if j is None or not j.movable:
        raise UrdfError(f"motion_direction_check: {joint!r} is not a movable joint")
    mesh = robot.links[j.child].mesh
    if mesh is None:
        raise UrdfError(f"motion_direction_check: link {j.child!r} has no mesh")
    if probe is None:
        hi = j.upper if j.upper is not None else math.pi
        lo = j.lower if j.lower is not None else -math.pi
        probe = hi if abs(hi) >= abs(lo) else lo
        if j.type != "prismatic":
            probe = math.copysign(min(abs(probe), 0.35), probe) if probe else 0.35
    c0 = trimesh.transform_points([mesh.centroid], fk(robot, {})[j.child])[0]
    c1 = trimesh.transform_points([mesh.centroid], fk(robot, {joint: probe})[j.child])[0]
    delta = c1 - c0
    n = float(np.linalg.norm(delta))
    d = delta / n if n > 1e-9 else delta
    want = np.asarray(_DIRS[key], dtype=float)
    cos = float(d @ want)
    ok = cos > 0.5
    return MotionCheck(joint=joint, expected=expected, observed_dir=tuple(round(float(v), 4) for v in d), ok=ok,
                       message=(f"{joint}: child '{j.child}' moves {tuple(round(float(v),3) for v in d)} for q={probe:+.3g}; "
                                f"expected {expected} ({'ok' if ok else 'WRONG — flip the axis sign or swap limits'})"))


def summary_text(report: SweepReport) -> str:
    """Compact human/LLM-readable summary of a sweep."""
    s = report.summary
    lines = [f"pose sweep: {s.n_poses} poses, {s.n_links} links, backend={s.backend}"]
    if s.max_penetration_m > 0:
        lines.append(f"max penetration {s.max_penetration_m*1000:.1f} mm at pose {s.worst_pose} between {s.worst_pair}; overlapping poses: {s.overlapping_poses}")
    else:
        lines.append("no overlaps deeper than tolerance in any pose")
    if s.floating_links:
        lines.append(f"floating links (not touching the grounded assembly): {s.floating_links}")
    multi = {k: v for k, v in s.link_islands.items() if v > 1}
    if multi:
        lines.append(f"links made of several disconnected islands: {multi}")
    return "\n".join(lines)


def report_numbers(report: SweepReport) -> dict[str, Any]:
    return report.summary.model_dump(mode="json")
