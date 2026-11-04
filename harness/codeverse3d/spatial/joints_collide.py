"""Per-link collision bodies for the pose sweep (:mod:`joints_sweep`)."""

from __future__ import annotations

import fcl as _fcl
import numpy as np
import trimesh
from trimesh.ray import ray_util

_MAX_SAMPLE_POINTS = 1500
_RAY_DIR = np.array([0.4395064455, 0.617598629942, 0.652231566745])


def components(names: list[str], edges: set[tuple[str, str]]) -> list[set[str]]:
    """Connected components of ``names`` under ``edges`` (union-find) — the contact graph of the
    connectivity gate and the fixed-joint groups of the pose sweep."""
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    comps: dict[str, set[str]] = {}
    for n in names:
        comps.setdefault(find(n), set()).add(n)
    return list(comps.values())


def fcl_collision_object(mesh: trimesh.Trimesh):
    """``fcl.CollisionObject`` for ``mesh`` (identity transform)."""
    if mesh.is_convex:
        fs = np.concatenate((3 * np.ones((len(mesh.faces), 1), dtype=np.int64), mesh.faces), axis=1)
        return _fcl.CollisionObject(_fcl.Convex(np.asarray(mesh.vertices, dtype=float), len(fs), fs.flatten()),
                                    _fcl.Transform())
    model = _fcl.BVHModel()
    model.beginModel(len(mesh.vertices), len(mesh.faces))
    model.addSubModel(np.asarray(mesh.vertices, dtype=float), np.asarray(mesh.faces, dtype=np.int64))
    model.endModel()
    return _fcl.CollisionObject(model, _fcl.Transform())


def _sample_points(mesh: trimesh.Trimesh) -> np.ndarray:
    pts = np.asarray(mesh.vertices)
    if len(pts) > _MAX_SAMPLE_POINTS:
        idx = np.linspace(0, len(pts) - 1, _MAX_SAMPLE_POINTS).astype(int)
        pts = pts[idx]
    n_surf = max(64, _MAX_SAMPLE_POINTS - len(pts))
    surf, _ = trimesh.sample.sample_surface(mesh, n_surf, seed=0)
    return np.vstack([pts, surf])


def oriented_islands(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """Connected components of ``mesh``."""
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


def inside_island(island: trimesh.Trimesh, points: np.ndarray) -> np.ndarray:
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
        self.obj = fcl_collision_object(mesh)
        self.T = np.eye(4)
        self.T_inv = np.eye(4)

    def set_pose(self, T: np.ndarray) -> None:
        self.T = T
        self.T_inv = np.linalg.inv(T)
        self.obj.setTransform(_fcl.Transform(np.ascontiguousarray(T[:3, :3]), np.ascontiguousarray(T[:3, 3])))

    @property
    def points(self) -> np.ndarray:
        return trimesh.transform_points(self.local_points, self.T)

    @property
    def aabb(self) -> np.ndarray:
        corners = trimesh.bounds.corners(self.mesh.bounds)
        pts = trimesh.transform_points(corners, self.T)
        return np.vstack([pts.min(axis=0), pts.max(axis=0)])

    def depth_of(self, points_world: np.ndarray) -> float:
        """Max distance-to-surface of the ``points_world`` that lie inside one of this body's
        islands."""
        local = trimesh.transform_points(points_world, self.T_inv)
        depth = 0.0
        for island, pq in zip(self.islands, self._island_pq, strict=True):
            inside = inside_island(island, local)
            if inside.any():
                depth = max(depth, float(pq.on_surface(local[inside])[1].max()))
        return depth

    def any_inside(self, points_world: np.ndarray) -> bool:
        local = trimesh.transform_points(points_world, self.T_inv)
        return any(bool(inside_island(i, local).any()) for i in self.islands)


def aabb_gap(a: np.ndarray, b: np.ndarray) -> float:
    """Largest per-axis separation of two AABBs (≤ 0 when they intersect)."""
    gaps = np.maximum(a[0] - b[1], b[0] - a[1])
    return float(gaps.max())


def collide(a: LinkBody, b: LinkBody) -> bool:
    """Surfaces intersect (FCL)."""
    req = _fcl.CollisionRequest(num_max_contacts=1, enable_contact=False)
    res = _fcl.CollisionResult()
    return int(_fcl.collide(a.obj, b.obj, req, res)) > 0


def contained(a: LinkBody, b: LinkBody, n: int | None = 24) -> bool:
    """True when surface samples of one body lie inside the other (FCL's surface-only collide
    misses a part fully swallowed by another)."""
    if n is None:
        pa, pb = a.points, b.points
    else:
        pa = a.points[:: max(1, len(a.points) // n)][:n]
        pb = b.points[:: max(1, len(b.points) // n)][:n]
    return b.any_inside(pa) or a.any_inside(pb)


def distance(a: LinkBody, b: LinkBody) -> float:
    """Minimum surface separation (0 when touching/overlapping)."""
    req = _fcl.DistanceRequest()
    res = _fcl.DistanceResult()
    return max(0.0, float(_fcl.distance(a.obj, b.obj, req, res)))


def penetration(a: LinkBody, b: LinkBody) -> tuple[float, bool]:
    """Max penetration depth (m) of A's surface into B or B's into A."""
    depth = max(b.depth_of(a.points), a.depth_of(b.points))
    return depth, not (a.watertight and b.watertight)
