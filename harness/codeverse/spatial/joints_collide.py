"""Per-link collision bodies for the pose sweep (:mod:`joints_sweep`).

FCL (``python-fcl``, a hard dependency) answers the boolean surface-collide and
the exact separation distance on BVH models.  Penetration *depth* is measured
deterministically on the link's **watertight islands**: a surface sample of
body A counts as inside body B when a fixed-direction parity ray test (forward
and backward rays agree) places it inside one of B's islands, and its depth is
the unsigned distance to that island's surface.  This is independent of the
mesh winding (agent meshes routinely come out inverted) and does not depend on
``Trimesh.contains`` / signed normals on the merged link mesh, which is usually
non-watertight (touching panels share vertices) and was re-cast with *random*
rays.  Islands that are not watertight are tested the same way but flagged
``approx``.  When FCL is missing the same deterministic inside test doubles as
the collision predicate (``backend == "trimesh"``).
"""

from __future__ import annotations

import numpy as np
import trimesh
from trimesh.ray import ray_util

try:  # declared in pyproject; guarded so a broken wheel degrades to the trimesh path
    import fcl as _fcl
except Exception:  # pragma: no cover - depends on the environment
    _fcl = None

_MAX_SAMPLE_POINTS = 1500
#: fixed, non-axis-aligned ray direction for parity tests (never re-cast at random)
_RAY_DIR = np.array([0.4395064455, 0.617598629942, 0.652231566745])


def backend_name() -> str:
    return "fcl" if _fcl is not None else "trimesh"


def _sample_points(mesh: trimesh.Trimesh) -> np.ndarray:
    pts = np.asarray(mesh.vertices)
    if len(pts) > _MAX_SAMPLE_POINTS:
        idx = np.linspace(0, len(pts) - 1, _MAX_SAMPLE_POINTS).astype(int)
        pts = pts[idx]
    n_surf = max(64, _MAX_SAMPLE_POINTS - len(pts))
    surf, _ = trimesh.sample.sample_surface(mesh, n_surf, seed=0)
    return np.vstack([pts, surf])


def oriented_islands(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """Connected components of ``mesh``; watertight ones get outward-facing normals
    (inverted winding → ``invert()``), so volumes/booleans are meaningful."""
    try:
        islands = list(mesh.split(only_watertight=False))
    except Exception:
        islands = []
    if not islands:
        islands = [mesh.copy()]
    for isl in islands:
        if isl.is_watertight and float(isl.volume) < 0.0:
            isl.invert()
    return islands


def _inside(island: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
    """Deterministic parity containment (fixed ray direction, both directions must agree)."""
    if len(points) == 0:
        return np.zeros(0, dtype=bool)
    return np.asarray(ray_util.contains_points(island.ray, points, check_direction=_RAY_DIR), dtype=bool)


class LinkBody:
    """Per-link collision state reused across poses (geometry in the LINK frame)."""

    def __init__(self, name: str, mesh: trimesh.Trimesh):
        self.name = name
        self.mesh = mesh
        self.local_points = _sample_points(mesh)
        self.islands = oriented_islands(mesh)
        self.watertight = all(bool(i.is_watertight) for i in self.islands)
        self._island_pq = [trimesh.proximity.ProximityQuery(i) for i in self.islands]
        self._pq = trimesh.proximity.ProximityQuery(mesh)  # unsigned distances, LOCAL frame
        self.obj = None
        if _fcl is not None:
            model = _fcl.BVHModel()
            model.beginModel(len(mesh.vertices), len(mesh.faces))
            model.addSubModel(np.asarray(mesh.vertices, dtype=float), np.asarray(mesh.faces, dtype=np.int64))
            model.endModel()
            self.obj = _fcl.CollisionObject(model, _fcl.Transform())
        self.T = np.eye(4)
        self.T_inv = np.eye(4)

    def set_pose(self, T: np.ndarray) -> None:
        self.T = T
        self.T_inv = np.linalg.inv(T)
        if self.obj is not None:
            self.obj.setTransform(_fcl.Transform(np.ascontiguousarray(T[:3, :3]), np.ascontiguousarray(T[:3, 3])))

    @property
    def posed(self) -> trimesh.Trimesh:
        m = trimesh.util.concatenate(self.islands) if len(self.islands) > 1 else self.islands[0].copy()
        m.apply_transform(self.T)
        return m

    @property
    def points(self) -> np.ndarray:
        return trimesh.transform_points(self.local_points, self.T)

    @property
    def aabb(self) -> np.ndarray:
        corners = trimesh.bounds.corners(self.mesh.bounds)
        pts = trimesh.transform_points(corners, self.T)
        return np.vstack([pts.min(axis=0), pts.max(axis=0)])

    def surface_distance(self, points_world: np.ndarray) -> np.ndarray:
        """Unsigned distance of world points to this body's surface."""
        return self._pq.on_surface(trimesh.transform_points(points_world, self.T_inv))[1]

    def depth_of(self, points_world: np.ndarray) -> float:
        """Max distance-to-surface of the ``points_world`` that lie inside one of this body's islands."""
        local = trimesh.transform_points(points_world, self.T_inv)
        depth = 0.0
        for island, pq in zip(self.islands, self._island_pq, strict=True):
            inside = _inside(island, local)
            if inside.any():
                depth = max(depth, float(pq.on_surface(local[inside])[1].max()))
        return depth

    def any_inside(self, points_world: np.ndarray) -> bool:
        local = trimesh.transform_points(points_world, self.T_inv)
        return any(bool(_inside(i, local).any()) for i in self.islands)


def aabb_gap(a: np.ndarray, b: np.ndarray) -> float:
    """Largest per-axis separation of two AABBs (≤ 0 when they intersect)."""
    gaps = np.maximum(a[0] - b[1], b[0] - a[1])
    return float(gaps.max())


def collide(a: LinkBody, b: LinkBody) -> bool:
    """Surfaces intersect (FCL) — or, without FCL, a surface sample of one lies inside the other."""
    if a.obj is not None and b.obj is not None:
        req = _fcl.CollisionRequest(num_max_contacts=1, enable_contact=False)
        res = _fcl.CollisionResult()
        return int(_fcl.collide(a.obj, b.obj, req, res)) > 0
    return contained(a, b, n=None)


def contained(a: LinkBody, b: LinkBody, n: int | None = 24) -> bool:
    """True when surface samples of one body lie inside the other (FCL's surface-only
    collide misses a part fully swallowed by another).  ``n`` samples per side (None = all)."""
    if n is None:
        pa, pb = a.points, b.points
    else:
        pa = a.points[:: max(1, len(a.points) // n)][:n]
        pb = b.points[:: max(1, len(b.points) // n)][:n]
    return b.any_inside(pa) or a.any_inside(pb)


def distance(a: LinkBody, b: LinkBody) -> float:
    """Minimum surface separation (0 when touching/overlapping)."""
    if a.obj is not None and b.obj is not None:
        req = _fcl.DistanceRequest()
        res = _fcl.DistanceResult()
        return max(0.0, float(_fcl.distance(a.obj, b.obj, req, res)))
    d = min(float(b.surface_distance(a.points).min()), float(a.surface_distance(b.points).min()))
    return max(0.0, d)


def penetration(a: LinkBody, b: LinkBody) -> tuple[float, bool]:
    """Max penetration depth (m) of A's surface into B or B's into A; ``approx``
    when an island is not watertight (the inside test is then heuristic)."""
    depth = max(b.depth_of(a.points), a.depth_of(b.points))
    return depth, not (a.watertight and b.watertight)


def intersection_volume(a: LinkBody, b: LinkBody) -> float | None:
    if not (a.watertight and b.watertight):
        return None
    try:
        inter = trimesh.boolean.intersection([a.posed, b.posed], engine="manifold")
        return float(abs(inter.volume)) if isinstance(inter, trimesh.Trimesh) and not inter.is_empty else 0.0
    except Exception:
        return None
