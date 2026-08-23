"""Pose sweep QC on a :class:`Robot`: overlaps, contacts, floating links, motion
direction.  FCL (``python-fcl``) does the boolean collide / distance queries on
BVH models; penetration depth is measured with trimesh signed distances of
surface samples (FCL's mesh-mesh contact depths are per-triangle artefacts).
Falls back to trimesh proximity when FCL is unavailable.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import trimesh
from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateFinding, Severity
from codeverse.conventions import CONTACT_GAP_M
from codeverse.spatial.joints_model import Robot, UrdfError, fk
from codeverse.spatial.joints_poses import pose_label

try:  # optional accelerator
    import fcl as _fcl
except Exception:  # pragma: no cover - depends on the environment
    _fcl = None

_MAX_SAMPLE_POINTS = 1500


# ------------------------------------------------------------------ report types
class Overlap(BaseModel):
    a: str
    b: str
    depth_m: float = Field(description="max distance of a penetrating surface point from the other surface")
    volume_m3: float | None = None
    approx: bool = Field(default=False, description="depth estimated on non-watertight meshes")


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


# ------------------------------------------------------------------ collision backends
def _sample_points(mesh: trimesh.Trimesh) -> np.ndarray:
    pts = np.asarray(mesh.vertices)
    if len(pts) > _MAX_SAMPLE_POINTS:
        idx = np.linspace(0, len(pts) - 1, _MAX_SAMPLE_POINTS).astype(int)
        pts = pts[idx]
    n_surf = max(64, _MAX_SAMPLE_POINTS - len(pts))
    surf, _ = trimesh.sample.sample_surface(mesh, n_surf, seed=0)
    return np.vstack([pts, surf])


class _LinkBody:
    """Per-link collision state reused across poses."""

    def __init__(self, name: str, mesh: trimesh.Trimesh):
        self.name = name
        self.mesh = mesh
        self.local_points = _sample_points(mesh)
        self.watertight = bool(mesh.is_watertight)
        self.obj = None
        if _fcl is not None:
            model = _fcl.BVHModel()
            model.beginModel(len(mesh.vertices), len(mesh.faces))
            model.addSubModel(np.asarray(mesh.vertices, dtype=float), np.asarray(mesh.faces, dtype=np.int64))
            model.endModel()
            self.obj = _fcl.CollisionObject(model, _fcl.Transform())
        self.T = np.eye(4)
        self.T_inv = np.eye(4)
        self._pq = trimesh.proximity.ProximityQuery(mesh)  # built once, queried in the LOCAL frame

    def set_pose(self, T: np.ndarray) -> None:
        self.T = T
        self.T_inv = np.linalg.inv(T)
        if self.obj is not None:
            self.obj.setTransform(_fcl.Transform(np.ascontiguousarray(T[:3, :3]), np.ascontiguousarray(T[:3, 3])))

    @property
    def posed(self) -> trimesh.Trimesh:
        m = self.mesh.copy()
        m.apply_transform(self.T)
        return m

    def signed_distance(self, points_world: np.ndarray) -> np.ndarray:
        """trimesh convention: > 0 inside this body."""
        return self._pq.signed_distance(trimesh.transform_points(points_world, self.T_inv))

    def contains(self, points_world: np.ndarray) -> np.ndarray:
        return self.mesh.contains(trimesh.transform_points(points_world, self.T_inv))

    @property
    def points(self) -> np.ndarray:
        return trimesh.transform_points(self.local_points, self.T)

    @property
    def aabb(self) -> np.ndarray:
        corners = trimesh.bounds.corners(self.mesh.bounds)
        pts = trimesh.transform_points(corners, self.T)
        return np.vstack([pts.min(axis=0), pts.max(axis=0)])


def _aabb_gap(a: np.ndarray, b: np.ndarray) -> float:
    """Largest per-axis separation of two AABBs (≤ 0 when they intersect)."""
    gaps = np.maximum(a[0] - b[1], b[0] - a[1])
    return float(gaps.max())


def _collide(a: _LinkBody, b: _LinkBody) -> bool:
    if a.obj is not None and b.obj is not None:
        req = _fcl.CollisionRequest(num_max_contacts=1, enable_contact=False)
        res = _fcl.CollisionResult()
        return int(_fcl.collide(a.obj, b.obj, req, res)) > 0
    # trimesh fallback: any surface sample of one inside the other, or AABB overlap + near-zero distance
    return _penetration(a, b)[0] > 0.0


def _contained(a: _LinkBody, b: _LinkBody, n: int = 24) -> bool:
    """True when a few surface samples of one body lie inside the other (FCL's
    surface-only collide misses a part fully swallowed by another)."""
    pa = a.points[:: max(1, len(a.points) // n)][:n]
    pb = b.points[:: max(1, len(b.points) // n)][:n]
    return bool(b.contains(pa).any() or a.contains(pb).any())


def _distance(a: _LinkBody, b: _LinkBody) -> float:
    if a.obj is not None and b.obj is not None:
        req = _fcl.DistanceRequest()
        res = _fcl.DistanceResult()
        return max(0.0, float(_fcl.distance(a.obj, b.obj, req, res)))
    d_ab = b.signed_distance(a.points)
    d_ba = a.signed_distance(b.points)
    return max(0.0, float(min(-d_ab.max(), -d_ba.max())))


def _depth_into(points_world: np.ndarray, other: _LinkBody, cap: int = 400) -> float:
    """Max distance-to-surface of the ``points_world`` that lie INSIDE ``other``.
    contains() first (cheap) so exact distances run only on the contained few."""
    inside = other.contains(points_world)
    if not inside.any():
        return 0.0
    pts = points_world[inside]
    if len(pts) > cap:
        pts = pts[np.linspace(0, len(pts) - 1, cap).astype(int)]
    return max(0.0, float(other.signed_distance(pts).max()))


def _penetration(a: _LinkBody, b: _LinkBody) -> tuple[float, bool]:
    """Max penetration depth (m) of A's surface into B or B's into A; ``approx``
    when a mesh is not watertight (inside test is then heuristic)."""
    depth = max(_depth_into(a.points, b), _depth_into(b.points, a))
    return depth, not (a.watertight and b.watertight)


def _intersection_volume(a: _LinkBody, b: _LinkBody) -> float | None:
    if not (a.watertight and b.watertight):
        return None
    try:
        inter = trimesh.boolean.intersection([a.posed, b.posed], engine="manifold")
        return float(abs(inter.volume)) if isinstance(inter, trimesh.Trimesh) and not inter.is_empty else 0.0
    except Exception:
        return None


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
    bodies = {n: _LinkBody(n, robot.links[n].mesh) for n in names}  # type: ignore[arg-type]
    allowed = {tuple(sorted(p)) for p in allow_pairs}
    per_pose: list[PoseReport] = []
    islands = {n: max(1, len(robot.links[n].mesh.split(only_watertight=False))) for n in names}  # type: ignore[union-attr]

    for q in poses:
        T = fk(robot, q)
        for n, body in bodies.items():
            body.set_pose(T[n])
        overlaps: list[Overlap] = []
        touching: set[tuple[str, str]] = set()
        nearest: dict[str, dict[str, float]] = {n: {} for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                ba, bb = bodies[a], bodies[b]
                gap_aabb = _aabb_gap(ba.aabb, bb.aabb)
                if gap_aabb > max(contact_gap_m, hinge_clearance_m * 3):
                    nearest[a][b] = nearest[b][a] = gap_aabb  # lower bound is enough this far apart
                    continue
                if _collide(ba, bb) or (gap_aabb <= 0.0 and _contained(ba, bb)):
                    depth, approx = _penetration(ba, bb)
                    dist = 0.0
                    if depth > tol_m and tuple(sorted((a, b))) not in allowed:
                        overlaps.append(
                            Overlap(a=a, b=b, depth_m=round(depth, 6), approx=approx,
                                    volume_m3=_intersection_volume(ba, bb) if volumes else None)
                        )
                else:
                    dist = _distance(ba, bb)
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
                    if _aabb_gap(bodies[n].aabb, bodies[j.parent].aabb) <= 0.0:
                        continue  # inserted into the parent's envelope (drawer in cavity)
                    thr = hinge_clearance_m
                else:  # revolute / continuous / planar / floating
                    thr = hinge_clearance_m
                if d > thr:
                    floating.append(FloatingLink(link=n, nearest=j.parent, joint_type=j.type,
                                                 gap_m=round(d, 6) if math.isfinite(d) else math.inf))
        per_pose.append(PoseReport(label=pose_label(robot, q), q=dict(q), overlaps=overlaps, floating=floating,
                                   n_contacts=len(touching)))

    summary = _summarise(per_pose, len(names), islands)
    return SweepReport(tol_m=tol_m, contact_gap_m=contact_gap_m, per_pose=per_pose, summary=summary)


def _summarise(per_pose: list[PoseReport], n_links: int, islands: dict[str, int]) -> SweepSummary:
    s = SweepSummary(n_poses=len(per_pose), n_links=n_links, link_islands=islands,
                     backend="fcl" if _fcl is not None else "trimesh")
    floating_any: set[str] = set()
    for pr in per_pose:
        for o in pr.overlaps:
            if o.depth_m > s.max_penetration_m:
                s.max_penetration_m, s.worst_pose, s.worst_pair = o.depth_m, pr.label, (o.a, o.b)
            if pr.label == "rest":
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
            at_rest = pr.label == "rest"
            sev = Severity.ERROR if (not at_rest or o.depth_m > rest_max_m) else Severity.WARN
            out.append(GateFinding(
                gate=gate, severity=sev, target=f"{o.a}|{o.b}",
                message=f"links '{o.a}' and '{o.b}' overlap by {o.depth_m*1000:.1f} mm at pose {pr.label}"
                        + (f" (pose q={pr.q})" if pr.q else "") + (" [approx: non-watertight mesh]" if o.approx else ""),
                fix_hint=(f"At q=0 the meshes in model.py interpenetrate: shrink/move one of '{o.a}', '{o.b}' so they touch (≤ {report.tol_m*1000:.0f} mm) instead of overlapping."
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
