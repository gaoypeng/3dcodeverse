"""Objective complexity of a built artifact — no VLM, no renders, no model call."""

from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import trimesh
from pydantic import BaseModel, Field

__all__ = [
    "ComplexityVector",
    "COMPLEXITY_WEIGHTS",
    "COMPLEXITY_VERSION",
    "complexity_of_parts",
    "complexity_of_glb",
    "band_of",
]

COMPLEXITY_VERSION = 1

SILHOUETTE_RES = 128
_SAMPLES_PER_CELL = 12
_SAMPLE_SEED = 0
_SHARP_DEG = 20.0
_SMALL_EDGE_FRAC = 0.02
_DUP_TOL_M = 1e-3

COMPLEXITY_WEIGHTS: dict[str, float] = {
    "part_count": 0.20,
    "assembly_depth": 0.07,
    "tri_count": 0.13,
    "materials": 0.08,
    "silhouette": 0.15,
    "feature_density": 0.22,
    "symmetry_groups": 0.06,
    "hollowness": 0.09,
}

_NORMALISERS: dict[str, tuple[str, float, float]] = {
    "part_count": ("log", 1.0, 30.0),
    "assembly_depth": ("lin", 1.0, 4.0),
    "tri_count": ("log", 300.0, 60_000.0),
    "materials": ("log", 1.0, 8.0),
    "silhouette": ("log", 1.0, 40.0),
    "feature_density": ("log", 50.0, 8_000.0),
    "symmetry_groups": ("log", 1.0, 9.0),
    "hollowness": ("lin", 0.0, 1.0),
}

_BANDS: tuple[tuple[float, str], ...] = (
    (0.25, "trivial"),
    (0.40, "simple"),
    (0.55, "moderate"),
    (0.70, "complex"),
    (1.01, "intricate"),
)


def band_of(index: float) -> str:
    """The band label for a complexity index (``trivial`` … ``intricate``)."""
    for hi, name in _BANDS:
        if index < hi:
            return name
    return _BANDS[-1][1]


def _norm(axis: str, value: float) -> float:
    kind, lo, hi = _NORMALISERS[axis]
    if kind == "log":
        v = max(float(value), 1e-9)
        num, den = math.log(v) - math.log(lo), math.log(hi) - math.log(lo)
    else:
        num, den = float(value) - lo, hi - lo
    return float(np.clip(num / den if den else 0.0, 0.0, 1.0))


class ComplexityVector(BaseModel):
    """Measured complexity of one built artifact (see the module docstring)."""

    version: int = COMPLEXITY_VERSION
    part_count: int = 0
    assembly_depth: int = 1
    tri_count: int = 0
    materials: int = 0
    silhouette: float = 1.0
    feature_density: float = 0.0
    symmetry_groups: int = 0
    hollowness: float = 0.0
    index: float = 0.0
    band: str = "trivial"
    components: dict[str, float] = Field(
        default_factory=dict, description="axis -> normalised 0-1 contribution input"
    )
    extra: dict[str, Any] = Field(default_factory=dict, description="descriptors that do not enter the index")


# --------------------------------------------------------------------- silhouette
def _raster(pts2: np.ndarray, res: int) -> np.ndarray:
    """Boolean occupancy grid of 2-D points, fitted to their own bounds."""
    lo, hi = pts2.min(axis=0), pts2.max(axis=0)
    span = float(np.max(np.maximum(hi - lo, 1e-9)))
    scale = (res - 3) / span
    ij = np.floor((pts2 - lo) * scale).astype(np.int64) + 1
    grid = np.zeros((res, res), dtype=bool)
    grid[np.clip(ij[:, 0], 0, res - 1), np.clip(ij[:, 1], 0, res - 1)] = True
    return grid


def _close(grid: np.ndarray) -> np.ndarray:
    """3×3 binary closing (dilate then erode) — seals sampling pinholes without bridging real
    gaps wider than one pixel."""
    out = grid
    for op in (np.logical_or, np.logical_and):
        acc = out.copy()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                acc = op(acc, np.roll(np.roll(out, dx, axis=0), dy, axis=1))
        out = acc
    return out


def _mask_perimeter(grid: np.ndarray) -> float:
    """4-connected crack-boundary length of the mask, in pixels."""
    p = int(np.count_nonzero(grid[:-1, :] ^ grid[1:, :]))
    p += int(np.count_nonzero(grid[:, :-1] ^ grid[:, 1:]))
    p += int(np.count_nonzero(grid[0, :]) + np.count_nonzero(grid[-1, :]))
    p += int(np.count_nonzero(grid[:, 0]) + np.count_nonzero(grid[:, -1]))
    return float(p)


def _silhouette_stats(mesh: trimesh.Trimesh) -> tuple[float, float, float]:
    """``(isoperimetric quotient, fill fraction, mirror symmetry)`` averaged over the three axis-
    aligned projections."""
    n = _SAMPLES_PER_CELL * SILHOUETTE_RES * SILHOUETTE_RES
    quotients: list[float] = []
    fills: list[float] = []
    mirrors: list[float] = []
    try:
        normals = np.abs(np.asarray(mesh.face_normals))
        areas = np.asarray(mesh.area_faces)
    except Exception:
        return 1.0, 1.0, 1.0
    for a, b in ((0, 1), (0, 2), (1, 2)):
        view = 3 - a - b
        weight = normals[:, view] * areas
        if not np.isfinite(weight).all() or weight.sum() <= 0:
            continue
        try:
            pts, _ = trimesh.sample.sample_surface(mesh, n, face_weight=weight, seed=_SAMPLE_SEED)
        except Exception:
            continue
        grid = _close(_raster(np.asarray(pts)[:, [a, b]], SILHOUETTE_RES))
        area = int(np.count_nonzero(grid))
        if area < 16:
            continue
        per = _mask_perimeter(grid)
        quotients.append(per * per / (4.0 * math.pi * area))
        rows = np.any(grid, axis=1)
        cols = np.any(grid, axis=0)
        bbox = max(int(np.count_nonzero(rows)) * int(np.count_nonzero(cols)), 1)
        fills.append(area / bbox)
        flip = grid[::-1, :]
        union = int(np.count_nonzero(grid | flip))
        mirrors.append(int(np.count_nonzero(grid & flip)) / union if union else 1.0)
    if not quotients:
        return 1.0, 1.0, 1.0
    return float(np.mean(quotients)), float(np.mean(fills)), float(np.max(mirrors))


# --------------------------------------------------------------------- other axes
def _feature_density(mesh: trimesh.Trimesh) -> tuple[float, int]:
    """Small sharp edges per ``diag²`` of surface area, and their raw count."""
    diag = float(np.linalg.norm(mesh.bounds[1] - mesh.bounds[0]))
    area = float(mesh.area)
    if diag <= 0 or area <= 0:
        return 0.0, 0
    try:
        angles = np.asarray(mesh.face_adjacency_angles)
        edges = np.asarray(mesh.face_adjacency_edges)
        verts = np.asarray(mesh.vertices)
        lengths = np.linalg.norm(verts[edges[:, 0]] - verts[edges[:, 1]], axis=1)
    except Exception:
        return 0.0, 0
    if angles.size == 0:
        return 0.0, 0
    n = int(np.count_nonzero((angles > math.radians(_SHARP_DEG)) & (lengths < _SMALL_EDGE_FRAC * diag)))
    return n * diag * diag / area, n


def _hollowness(parts: dict[str, trimesh.Trimesh | None]) -> tuple[float, float]:
    """``(mean per-part concavity over watertight parts, watertight fraction)``."""
    concavity: list[float] = []
    solid = 0
    total = 0
    for mesh in parts.values():
        if mesh is None or not len(mesh.faces):
            continue
        total += 1
        if not bool(mesh.is_watertight):
            continue
        solid += 1
        try:
            vol = abs(float(mesh.volume))
            hull = abs(float(mesh.convex_hull.volume))
        except Exception:
            continue
        if hull > 0:
            concavity.append(float(np.clip(1.0 - vol / hull, 0.0, 1.0)))
    hollow = float(np.mean(concavity)) if concavity else 0.0
    return hollow, (solid / total if total else 0.0)


def _symmetry_groups(parts: dict[str, trimesh.Trimesh | None]) -> tuple[int, int]:
    """``(families of geometrically identical parts, parts inside such a family)``."""
    buckets: dict[tuple[int, tuple[int, ...]], int] = {}
    for mesh in parts.values():
        if mesh is None or not len(mesh.faces):
            continue
        ext = np.sort(mesh.bounds[1] - mesh.bounds[0])
        key = (int(len(mesh.faces)), tuple(int(round(float(v) / _DUP_TOL_M)) for v in ext))
        buckets[key] = buckets.get(key, 0) + 1
    groups = [n for n in buckets.values() if n > 1]
    return len(groups), int(sum(groups))


def _graph_depth(scene: trimesh.Scene | None, tops: list[str]) -> int:
    """Deepest chain of nodes under the effective top-level parts (1 = flat)."""
    if scene is None or not tops:
        return 1
    children = scene.graph.transforms.children
    best = 1
    for top in tops:
        stack: list[tuple[str, int]] = [(top, 1)]
        seen: set[str] = set()
        while stack:
            node, depth = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            best = max(best, depth)
            stack.extend((c, depth + 1) for c in children.get(node, ()))
    return best


def _materials(scene: trimesh.Scene | None, fallback: int) -> int:
    if scene is None:
        return fallback
    seen = {
        id(g.visual.material)
        for g in scene.geometry.values()
        if hasattr(g, "visual") and hasattr(g.visual, "material")
    }
    return len(seen) or fallback


# --------------------------------------------------------------------- front doors
def complexity_of_parts(
    parts: OrderedDict[str, trimesh.Trimesh | None] | dict[str, trimesh.Trimesh | None],
    *,
    scene: trimesh.Scene | None = None,
    materials: int = 0,
) -> ComplexityVector:
    """Complexity of already-loaded world-space part meshes."""
    solid = OrderedDict((k, v) for k, v in parts.items() if v is not None and len(v.faces))
    if not solid:
        return ComplexityVector(extra={"error": "no geometry"})
    try:
        whole = trimesh.util.concatenate(list(solid.values()))
    except Exception as e:  # pragma: no cover - trimesh raises many types
        return ComplexityVector(part_count=len(solid), extra={"error": f"{type(e).__name__}: {e}"})

    sil, fill, mirror = _silhouette_stats(whole)
    fdens, fedges = _feature_density(whole)
    hollow, watertight = _hollowness(solid)
    groups, repeated = _symmetry_groups(solid)
    from codeverse3d.spatial.measure import effective_top_nodes

    tops = effective_top_nodes(scene) if scene is not None else []
    vec = ComplexityVector(
        part_count=len(solid),
        assembly_depth=_graph_depth(scene, [str(t) for t in tops]),
        tri_count=int(len(whole.faces)),
        materials=_materials(scene, materials),
        silhouette=round(sil, 3),
        feature_density=round(fdens, 1),
        symmetry_groups=groups,
        hollowness=round(hollow, 3),
        extra={
            "silhouette_fill": round(fill, 3),
            "mirror_symmetry": round(mirror, 3),
            "watertight_fraction": round(watertight, 3),
            "repeated_parts": repeated,
            "small_sharp_edges": fedges,
        },
    )
    vec.components = {axis: round(_norm(axis, getattr(vec, axis)), 4) for axis in COMPLEXITY_WEIGHTS}
    vec.index = round(sum(COMPLEXITY_WEIGHTS[a] * v for a, v in vec.components.items()), 4)
    vec.band = band_of(vec.index)
    return vec


def complexity_of_glb(glb: Path | str) -> ComplexityVector:
    """Complexity of a canonical GLB on disk (uses the shared parse cache)."""
    from codeverse3d.spatial.measure import cached_parts, load_scene

    parts = cached_parts(glb)
    try:
        scene: trimesh.Scene | None = load_scene(glb)
    except Exception:
        scene = None
    return complexity_of_parts(parts, scene=scene)


