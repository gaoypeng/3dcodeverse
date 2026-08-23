"""Analytic UVs per part: box (per-face dominant axis), cylinder (around the long
axis, planar caps) or planar projections, all in WORLD metres divided by
``tile_size_m`` so a pattern has the same physical size on every part.

Vertices are split only where two incident faces disagree on the projection key
(box axis, cylinder seam / cap) so smooth shading is preserved elsewhere; vertex
normals are carried along.  The mesh geometry (positions, node transforms) is
never changed — only TEXCOORD_0 is added.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: long/mid extent ratio above which ``auto`` picks a cylinder projection ...
CYLINDER_ASPECT = 2.5
#: ... provided the cross-section is roughly round (mid/min below this); slabs → box
CYLINDER_SECTION = 2.0
#: |n · axis| above which a face is a cylinder cap (planar projected)
CAP_COS = 0.75


@dataclass
class Unwrapped:
    vertices: np.ndarray  # (N', 3) LOCAL coordinates (possibly split)
    faces: np.ndarray  # (F, 3)
    normals: np.ndarray | None  # (N', 3) local normals or None
    uv: np.ndarray  # (N', 2)
    projection: str  # box | cylinder | planar_y | planar_z
    axis: int  # projection axis (cylinder axis / planar normal axis)
    n_split: int  # vertices added by seam splitting


def choose_projection(extents_world: np.ndarray, requested: str) -> tuple[str, int]:
    """Resolve ``auto`` → (projection, axis).  Elongated with a round-ish section →
    cylinder around the long axis; everything else (slabs, boxes, frames) → box.  Explicit projections pass through (cylinder axis =
    the longest world axis; planar axes are fixed: planar_y → 1, planar_z → 2)."""
    e = np.asarray(extents_world, dtype=float)
    order = np.argsort(e)[::-1]
    long_axis = int(order[0])
    if requested == "cylinder":
        return "cylinder", long_axis
    if requested == "planar_y":
        return "planar_y", 1
    if requested == "planar_z":
        return "planar_z", 2
    if requested == "box":
        return "box", int(np.argmin(e))
    mid = max(float(e[order[1]]), 1e-9)
    small = max(float(e[order[2]]), 1e-9)
    if float(e[order[0]]) / mid >= CYLINDER_ASPECT and mid / small <= CYLINDER_SECTION:
        return "cylinder", long_axis
    return "box", int(np.argmin(e))


def _face_normals(P: np.ndarray, F: np.ndarray) -> np.ndarray:
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(ln, 1e-12)


def _box_uv(P: np.ndarray, F: np.ndarray, tile: float) -> tuple[np.ndarray, np.ndarray]:
    """Per-face dominant axis of the face normal → planar projection on the other two."""
    n = _face_normals(P, F)
    axis = np.argmax(np.abs(n), axis=1)  # (F,)
    sign = np.sign(n[np.arange(len(F)), axis])
    sign[sign == 0] = 1.0
    other = np.array([[2, 1], [0, 2], [0, 1]])  # axis → (u axis, v axis)
    corners = P[F]  # (F,3,3)
    ua, va = other[axis, 0], other[axis, 1]
    u = np.take_along_axis(corners, ua[:, None, None].repeat(3, 1), axis=2)[:, :, 0]
    v = np.take_along_axis(corners, va[:, None, None].repeat(3, 1), axis=2)[:, :, 0]
    u = u * sign[:, None]  # keep handedness consistent on opposite faces
    uv = np.stack([u, v], axis=2) / tile
    key = axis[:, None].repeat(3, 1) * 2 + (sign[:, None].repeat(3, 1) < 0).astype(int)
    return uv, key


def _planar_uv(P: np.ndarray, F: np.ndarray, axis: int, tile: float) -> tuple[np.ndarray, np.ndarray]:
    other = {0: (2, 1), 1: (0, 2), 2: (0, 1)}[axis]
    corners = P[F]
    uv = np.stack([corners[:, :, other[0]], corners[:, :, other[1]]], axis=2) / tile
    return uv, np.zeros((len(F), 3), dtype=int)


def _cylinder_uv(P: np.ndarray, F: np.ndarray, axis: int, tile: float) -> tuple[np.ndarray, np.ndarray]:
    a, b = [i for i in range(3) if i != axis]
    center = 0.5 * (P.min(axis=0) + P.max(axis=0))
    d = P - center
    radius = float(np.mean(np.hypot(d[:, a], d[:, b])))
    radius = max(radius, 1e-4)
    circ = 2.0 * np.pi * radius
    ang = np.arctan2(d[:, b], d[:, a])  # (-π, π]
    u_v = (ang + np.pi) / (2.0 * np.pi) * circ / tile  # 0..circ/tile
    v_v = P[:, axis] / tile
    u = u_v[F]  # (F,3)
    v = v_v[F]
    key = np.zeros((len(F), 3), dtype=int)
    # seam: faces straddling angle ±π have a u spread > half circumference → shift the low corners
    span = u.max(axis=1) - u.min(axis=1)
    wrap = span > 0.5 * circ / tile
    if wrap.any():
        low = u < (u.max(axis=1, keepdims=True) - 0.5 * circ / tile)
        shift = wrap[:, None] & low
        u = u + shift * (circ / tile)
        key = key + shift.astype(int)
    # caps: planar
    n = _face_normals(P, F)
    cap = np.abs(n[:, axis]) > CAP_COS
    if cap.any():
        corners = P[F]
        u = np.where(cap[:, None], corners[:, :, a] / tile, u)
        v = np.where(cap[:, None], corners[:, :, b] / tile, v)
        key = np.where(cap[:, None], 2, key)
    return np.stack([u, v], axis=2), key


def split_by_key(
    vertices: np.ndarray, faces: np.ndarray, normals: np.ndarray | None, uv_corner: np.ndarray, key_corner: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray, int]:
    """Duplicate a vertex once per distinct (vertex, key) pair; UV per new vertex =
    mean of its corner UVs (they agree within a key by construction)."""
    F = faces
    flat_v = F.reshape(-1)
    flat_k = key_corner.reshape(-1)
    pair = flat_v.astype(np.int64) * (int(flat_k.max()) + 1 if flat_k.size else 1) + flat_k
    uniq, inverse = np.unique(pair, return_inverse=True)
    n_new = len(uniq)
    src = np.zeros(n_new, dtype=np.int64)
    src[inverse] = flat_v
    new_vertices = vertices[src]
    new_normals = normals[src] if normals is not None else None
    new_uv = np.zeros((n_new, 2), dtype=np.float64)
    counts = np.zeros(n_new, dtype=np.float64)
    np.add.at(new_uv, inverse, uv_corner.reshape(-1, 2))
    np.add.at(counts, inverse, 1.0)
    new_uv /= np.maximum(counts, 1.0)[:, None]
    new_faces = inverse.reshape(-1, 3)
    return new_vertices, new_faces, new_normals, new_uv, int(n_new - len(vertices))


def unwrap(
    vertices_local: np.ndarray,
    faces: np.ndarray,
    transform: np.ndarray,
    *,
    projection: str = "auto",
    tile_size_m: float = 0.3,
    normals_local: np.ndarray | None = None,
) -> Unwrapped:
    """Compute UVs for one mesh given its 4×4 world ``transform``.  Returns the
    (possibly vertex-split) mesh in LOCAL coordinates plus per-vertex UVs."""
    V = np.asarray(vertices_local, dtype=np.float64)
    F = np.asarray(faces, dtype=np.int64)
    if V.ndim != 2 or V.shape[1] != 3 or F.ndim != 2 or F.shape[1] != 3 or len(F) == 0:
        raise ValueError("unwrap needs (N,3) vertices and (F,3) triangles")
    tile = float(tile_size_m)
    if not tile > 0:
        raise ValueError("tile_size_m must be > 0")
    T = np.asarray(transform, dtype=np.float64)
    P = V @ T[:3, :3].T + T[:3, 3]
    ext = P.max(axis=0) - P.min(axis=0)
    proj, axis = choose_projection(ext, projection)
    if proj == "box":
        uv_c, key_c = _box_uv(P, F, tile)
    elif proj == "cylinder":
        uv_c, key_c = _cylinder_uv(P, F, axis, tile)
    else:
        uv_c, key_c = _planar_uv(P, F, axis, tile)
    nv, nf, nn, uv, n_split = split_by_key(V, F, normals_local, uv_c, key_c)
    # keep numbers small for float32 TEXCOORD precision (pattern is periodic anyway)
    uv = uv - np.floor(uv.min(axis=0))
    return Unwrapped(vertices=nv, faces=nf, normals=nn, uv=uv, projection=proj, axis=axis, n_split=n_split)
