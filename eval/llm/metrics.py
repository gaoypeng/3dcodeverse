"""Metric primitives.  Definitions are documented in docs/metrics.md; keep the two in sync.

Geometry (generated mesh vs reference mesh, both normalised to a unit bounding box):
  chamfer     symmetric Chamfer-L2 distance = mean_a min_b ||a-b|| + mean_b min_a ||a-b||   (sum of the two
              directional means — the convention used throughout finetune/docs/REPORT.md; halve it for the
              "average" convention used by some CAD papers)
  f@tau       F-score at threshold tau: precision = frac of generated points within tau of the reference,
              recall = frac of reference points within tau of the generated mesh, F = 2PR/(P+R)
  iou_vox     volumetric IoU on a 64^3 voxel grid of the normalised bounding box (solid voxelisation; needs
              reasonably closed meshes — reported as None when either voxelisation is empty)
  rotation search: the generated point cloud is rotated by k*90 deg (k = 0..3) about the up axis and the
              rotation with the smallest Chamfer is kept (objects may face any of the four cardinal directions)

Aggregates:
  exec_rate           OK / n
  f@tau_mean_scored   mean over executed tasks;   f@tau_mean_all   fail counted as 0 (the headline number)
  pass@k              unbiased estimator of Chen et al. 2021 given n samples with c successes per task
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

UP_AXIS = {"glb": 1, "stl": 2}   # glTF is Y-up; CadQuery/OpenSCAD STL are Z-up


def load_points(path: str | Path, n: int = 10000, seed: int = 0) -> np.ndarray | None:
    import trimesh

    m = trimesh.load(str(path), force="mesh")
    if isinstance(m, trimesh.Scene):
        geoms = list(m.geometry.values())
        if not geoms:
            return None
        m = trimesh.util.concatenate(geoms)
    if m.is_empty or len(m.faces) == 0:
        return None
    m = normalise(m)
    if m is None:
        return None
    pts, _ = trimesh.sample.sample_surface(m, n, seed=seed)
    return np.asarray(pts)


def normalise(m):
    """Centre at the bbox centre, scale so the largest bbox extent is 1 (in place; returns None if degenerate)."""
    v = m.vertices
    c = (v.max(0) + v.min(0)) / 2
    s = (v.max(0) - v.min(0)).max()
    if not np.isfinite(s) or s <= 0:
        return None
    m.apply_translation(-c)
    m.apply_scale(1.0 / s)
    return m


def rot_about(p: np.ndarray, axis: int, k: int) -> np.ndarray:
    th = np.pi / 2 * k
    c, s = np.cos(th), np.sin(th)
    if axis == 1:    # Y-up
        R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    elif axis == 2:  # Z-up
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    else:
        R = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    return p @ R.T


def chamfer_f(a: np.ndarray, b: np.ndarray, taus=(0.05, 0.1)) -> tuple[float, dict[str, float]]:
    from scipy.spatial import cKDTree

    ta, tb = cKDTree(a), cKDTree(b)
    da, _ = tb.query(a)
    db, _ = ta.query(b)
    cd = float(da.mean() + db.mean())
    fs = {}
    for t in taus:
        p = float((da < t).mean())
        r = float((db < t).mean())
        fs[f"f@{t}"] = 2 * p * r / (p + r) if p + r > 0 else 0.0
    return cd, fs


def voxel_iou(gen_path: str | Path, ref_path: str | Path, pitch_div: int = 64) -> float | None:
    """Solid-voxel IoU in the unit cube, or None when a mesh cannot be voxelised."""
    import trimesh

    def solid(path):
        m = trimesh.load(str(path), force="mesh")
        if isinstance(m, trimesh.Scene):
            m = trimesh.util.concatenate(list(m.geometry.values()))
        if m.is_empty or len(m.faces) == 0 or normalise(m) is None:
            return None
        vox = m.voxelized(pitch=1.0 / pitch_div).fill()
        return vox

    try:
        a, b = solid(gen_path), solid(ref_path)
        if a is None or b is None:
            return None
        # align both grids on the unit cube centred at the origin
        grid = np.zeros((pitch_div + 2,) * 3, dtype=bool)

        def paint(vox):
            g = grid.copy()
            idx = np.round((vox.points + 0.5) * pitch_div).astype(int)
            idx = idx[(idx >= 0).all(1) & (idx < pitch_div + 2).all(1)]
            g[idx[:, 0], idx[:, 1], idx[:, 2]] = True
            return g

        ga, gb = paint(a), paint(b)
        inter, union = np.logical_and(ga, gb).sum(), np.logical_or(ga, gb).sum()
        return float(inter / union) if union else None
    except Exception:  # noqa: BLE001
        return None


def geometry_metrics(gen_path: str | Path, ref_path: str | Path, n_points: int = 10000, up_axis: int | None = None,
                     with_iou: bool = True) -> dict:
    """Chamfer / F@0.05 / F@0.1 (best of 4 yaw rotations) + voxel IoU between two mesh files."""
    if up_axis is None:
        up_axis = UP_AXIS.get(Path(ref_path).suffix.lower().lstrip("."), 1)
    pg, pr = load_points(gen_path, n_points), load_points(ref_path, n_points)
    if pg is None or pr is None:
        return {"metric_error": "empty mesh"}
    best = None
    for k in range(4):
        cd, fs = chamfer_f(rot_about(pg, up_axis, k), pr)
        if best is None or cd < best[0]:
            best = (cd, fs, k)
    out = {"chamfer": best[0], **best[1], "rot_k": best[2]}
    if with_iou:
        out["iou_vox"] = voxel_iou(gen_path, ref_path)
    return out


def chamfer_official(gen_path: str | Path, ref_path: str | Path, n_points: int = 8192, seed: int = 0) -> dict:
    """The official 3DCodeBench Chamfer (metrics/shape_chamfer.py): centroid-centred, unit-SPHERE normalised,
    n_points surface samples, CD = mean min ||.||^2 + mean min ||.||^2 (squared), min over 4 yaw rotations
    about Z of the generated cloud.  Reported separately from `chamfer` (unit-bbox, unsquared) so the numbers
    are comparable to the paper's frontier-model table."""
    import trimesh
    from scipy.spatial import cKDTree

    rng = np.random.default_rng(seed)

    def pts(path):
        m = trimesh.load(str(path), force="mesh")
        if isinstance(m, trimesh.Scene):
            m = trimesh.util.concatenate(list(m.geometry.values()))
        if m.is_empty or len(m.faces) == 0:
            return None
        p, _ = trimesh.sample.sample_surface(m, n_points, seed=int(rng.integers(0, 2**31 - 1)))
        p = np.asarray(p, dtype=np.float64)
        p = p - p.mean(0, keepdims=True)
        r = np.linalg.norm(p, axis=1).max()
        return p / r if r > 1e-9 else p

    def cd2(a, b):
        da, _ = cKDTree(b).query(a)
        db, _ = cKDTree(a).query(b)
        return float((da ** 2).mean() + (db ** 2).mean())

    ref, gen = pts(ref_path), pts(gen_path)
    if ref is None or gen is None:
        return {"cd_official": None, "cd_official_yawmin": None}
    per_yaw = [cd2(ref, rot_about(gen, 2, k)) for k in range(4)]
    return {"cd_official": per_yaw[0], "cd_official_yawmin": float(min(per_yaw))}


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k (Chen et al. 2021): 1 - C(n-c, k) / C(n, k)."""
    if n - c < k:
        return 1.0
    return 1.0 - math.prod((n - c - i) / (n - i) for i in range(k))


def mean(xs) -> float | None:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(np.mean(xs)) if xs else None


def median(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else None
