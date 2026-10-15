"""lib/place.py — seating, facing and the zone's own placement self-check.

The placement gate (``runtime_js/lib/host_placement.mjs``, run on the census GLB) samples up to
five FOOT COLUMNS per placed thing — the lowest vertex and the ±x / ±y extremes of the band
within max(2 cm, 5 %) of its height above its minimum — and reports a gap > 0.05 m outdoors
(0.02 m indoors) as floating, a burial > 0.10 m as sunk and AABB overlaps > 10 % of the
smaller box as interpenetration.  ``seat`` uses the SAME columns, so what it seats the gate
reads as supported.  World matrices are composed here from ``matrix_basis`` because
``obj.matrix_world`` is stale until the depsgraph evaluates (an object just given a
``location`` still reports the identity).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import bpy
import numpy as np
from mathutils import Matrix, Vector

#: the gate's foot band: max(FOOT_BAND_MIN_M, FOOT_BAND_FRAC x height) above the lowest vertex
FOOT_BAND_MIN_M = 0.02
FOOT_BAND_FRAC = 0.05
CONTACT_M = 0.02
_EVALUATED_TYPES = ("CURVE", "FONT", "SURFACE", "META")


def world_matrix(obj: Any) -> Matrix:
    """``obj``'s world matrix NOW (parent chain x ``matrix_basis``), not the depsgraph's last."""
    m = obj.matrix_basis.copy()
    if obj.parent is not None:
        return world_matrix(obj.parent) @ obj.matrix_parent_inverse @ m
    return m


def _mesh_points(obj: Any, matrix: Matrix, *, evaluated: bool) -> np.ndarray:
    """World-space vertices of one object's own geometry (empty for a non-geometric object)."""
    me = None
    owner = None
    if obj.type == "MESH" and (not evaluated or not obj.modifiers):
        me = obj.data
    elif obj.type == "MESH" or obj.type in _EVALUATED_TYPES:
        owner = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        try:
            me = owner.to_mesh()
        except RuntimeError:
            me = None
    if me is None or len(me.vertices) == 0:
        if owner is not None:
            owner.to_mesh_clear()
        return np.empty((0, 3), np.float64)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    if owner is not None:
        owner.to_mesh_clear()
    pts = co.reshape(-1, 3).astype(np.float64)
    m = np.array(matrix, np.float64)
    return pts @ m[:3, :3].T + m[:3, 3]


def _collection_points(coll: Any, matrix: Matrix, *, evaluated: bool) -> list[np.ndarray]:
    off = Vector(coll.instance_offset)
    out = []
    for o in coll.all_objects:
        if o.type == "EMPTY" or o.hide_render:
            continue
        local = world_matrix(o)
        local.translation = local.translation - off
        out.append(_mesh_points(o, matrix @ local, evaluated=evaluated))
    return out


def world_points(obj: Any, *, evaluated: bool = True) -> np.ndarray:
    """World-space vertices of ``obj``, its children and (for a collection instance) its
    source — the point set the gate measures as this placement row."""
    parts: list[np.ndarray] = []
    stack = [obj]
    while stack:
        o = stack.pop()
        m = world_matrix(o)
        if o.type == "EMPTY" and o.instance_type == "COLLECTION" and o.instance_collection is not None:
            parts.extend(_collection_points(o.instance_collection, m, evaluated=evaluated))
        else:
            parts.append(_mesh_points(o, m, evaluated=evaluated))
        stack.extend(o.children)
    parts = [p for p in parts if len(p)]
    return np.concatenate(parts) if parts else np.empty((0, 3), np.float64)


def bbox_world(obj: Any, *, evaluated: bool = True) -> tuple[Vector, Vector]:
    """(min, max) corners of ``obj``'s geometry in world space (children and instances included);
    a thing with no geometry answers its own location twice."""
    pts = world_points(obj, evaluated=evaluated)
    if not len(pts):
        p = world_matrix(obj).translation
        return Vector(p), Vector(p)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return Vector(lo.tolist()), Vector(hi.tolist())


def foot_columns(points: np.ndarray, columns: int = 5) -> list[tuple[float, float, float]]:
    """The gate's foot columns of a point set: lowest, min x, max x, min y, max y of the foot
    band, deduplicated within 1 cm, up to ``columns``."""
    if not len(points):
        return []
    zmin, zmax = float(points[:, 2].min()), float(points[:, 2].max())
    band = points[points[:, 2] <= zmin + max(FOOT_BAND_MIN_M, FOOT_BAND_FRAC * (zmax - zmin))]

    def lowest_at(axis: int, sign: float) -> np.ndarray:
        """The lowest band vertex at the band's extreme along ``axis`` (a sill beam's top and
        bottom share an x: the top is not the foot)."""
        extreme = (sign * band[:, axis]).max()
        cand = band[sign * band[:, axis] >= extreme - 1e-4]
        return cand[cand[:, 2].argmin()]

    picks = [band[band[:, 2].argmin()], lowest_at(0, -1.0), lowest_at(0, 1.0), lowest_at(1, -1.0), lowest_at(1, 1.0)]
    out: list[tuple[float, float, float]] = []
    for p in picks:
        x, y, z = (float(v) for v in p)
        if any(math.hypot(q[0] - x, q[1] - y) < 0.01 for q in out):
            continue
        out.append((x, y, z))
        if len(out) >= max(1, int(columns)):
            break
    return out


def _drop(points: np.ndarray, height_at: Callable[[float, float], float], *, sink: float, columns: int) -> float:
    """The vertical move that puts the first foot on the ground (every other column at or below
    it): ``min`` over the columns of (ground - foot) - sink."""
    cols = foot_columns(points, columns)
    if not cols:
        return 0.0
    return min(float(height_at(x, y)) - z for x, y, z in cols) - float(sink)


def seat(obj: Any, height_at: Callable[[float, float], float], *, sink: float = 0.0, columns: int = 5) -> float:
    """Move ``obj`` vertically so its lowest foot column rests on ``height_at`` (``sink`` m
    deeper: an embedded rock, a driven post).  Returns the move in metres.  Seat the ROOT of a
    prop (the Empty ``Batch.finish`` returns), whose z is a world z."""
    dz = _drop(world_points(obj), height_at, sink=sink, columns=columns)
    obj.location.z += dz
    return dz


def seat_instance(empty: Any, source: Any, height_at: Callable[[float, float], float], *, sink: float = 0.0) -> float:
    """``seat`` for a collection-instance Empty of ``source`` (its rotation and scale applied to
    the source's geometry before the foot columns are read).  Returns the move in metres."""
    m = world_matrix(empty)
    parts = [p for p in _collection_points(source, m, evaluated=True) if len(p)]
    if not parts:
        return 0.0
    dz = _drop(np.concatenate(parts), height_at, sink=sink, columns=5)
    empty.location.z += dz
    return dz


def rest_on(obj: Any, support: Any, *, offset: float = 0.0) -> float:
    """Seat ``obj`` on ``support`` (a table, a shelf, a wall top): its lowest point meets the
    highest point of ``support`` under ``obj``'s footprint (+ ``offset``).  Returns the move."""
    pts = world_points(obj)
    sup = world_points(support)
    if not len(pts) or not len(sup):
        return 0.0
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    pad = 0.02
    under = sup[(sup[:, 0] >= lo[0] - pad) & (sup[:, 0] <= hi[0] + pad) & (sup[:, 1] >= lo[1] - pad) & (sup[:, 1] <= hi[1] + pad)]
    top = float(under[:, 2].max()) if len(under) else float(sup[:, 2].max())
    dz = top + float(offset) - float(lo[2])
    obj.location.z += dz
    return dz


def heading(a: Sequence[float], b: Sequence[float]) -> float:
    """The Z rotation (radians) that points a -Y-front object standing at ``a`` toward ``b``."""
    return math.atan2(float(b[1]) - float(a[1]), float(b[0]) - float(a[0])) + math.pi / 2


def face_toward(obj: Any, target: Sequence[float]) -> float:
    """Yaw ``obj`` (front = -Y) toward ``target``; returns the angle set."""
    pos = world_matrix(obj).translation
    ang = heading((pos.x, pos.y), target)
    obj.rotation_mode = "XYZ"
    obj.rotation_euler[2] = ang
    return ang


def clear_of(objs: Sequence[Any], min_gap: float = CONTACT_M, *, overlap: float = 0.10) -> list[tuple[str, str, float]]:
    """The zone's self-check: pairs of ``objs`` whose world AABBs overlap by more than
    ``overlap`` of the smaller box (the gate's interpenetration threshold) as
    ``(name_a, name_b, fraction)``; boxes merely within ``min_gap`` are contact, not reported."""
    boxes = []
    for o in objs:
        lo, hi = bbox_world(o)
        boxes.append((o.name, np.array(lo), np.array(hi)))
    out: list[tuple[str, str, float]] = []
    for i in range(len(boxes)):
        na, la, ha = boxes[i]
        for j in range(i + 1, len(boxes)):
            nb, lb, hb = boxes[j]
            if np.any(la - min_gap > hb) or np.any(lb - min_gap > ha):
                continue
            ov = np.minimum(ha, hb) - np.maximum(la, lb)
            if np.any(ov <= 0):
                continue
            va = float(np.prod(np.maximum(ha - la, 0.01)))
            vb = float(np.prod(np.maximum(hb - lb, 0.01)))
            frac = float(np.prod(ov)) / min(va, vb)
            if frac > overlap:
                out.append((na, nb, round(frac, 3)))
    return out


def clear_mask(objs: Sequence[Any], margin: float = 0.3) -> Callable[[float, float], float]:
    """A ``scatter.poisson(mask=…)`` filter that keeps scatter points OUT of the footprints of
    ``objs`` — their world AABBs grown by ``margin`` m: 0.0 inside one, 1.0 clear.  Build it from
    the roots placed by hand BEFORE the small stuff is scattered: 9 of the 13 Blender zones that
    shipped an interpenetration ERROR in the argon loop's iteration 0 (2026-10-15) had scattered
    a small prop into a cart, a rack, a shrub or a mound (100 % of the smaller box)."""
    boxes: list[tuple[float, float, float, float]] = []
    for o in objs:
        lo, hi = bbox_world(o)
        boxes.append((lo.x - margin, lo.y - margin, hi.x + margin, hi.y + margin))

    def mask(x: float, y: float) -> float:
        for x0, y0, x1, y1 in boxes:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return 0.0
        return 1.0

    return mask


def keep_inside(obj: Any, bbox: tuple[Sequence[float], Sequence[float]]) -> Vector:
    """Slide ``obj`` horizontally so its AABB stays within ``bbox`` = (centre, extents) — the
    zone's plan box, in this frame.  Returns the move."""
    (cx, cy, _), (ex, ey, _) = bbox
    lo, hi = bbox_world(obj)
    move = Vector((0.0, 0.0, 0.0))
    for k, c, e in ((0, cx, ex), (1, cy, ey)):
        if lo[k] < c - e / 2:
            move[k] = (c - e / 2) - lo[k]
        elif hi[k] > c + e / 2:
            move[k] = (c + e / 2) - hi[k]
    obj.location.x += move.x
    obj.location.y += move.y
    return move


def unique_name(base: str, i: int) -> str:
    """``Base_i`` — or the next free index when that object already exists (names are global
    in Blender: a second ``Lantern_0`` would silently become ``Lantern_0.001``)."""
    i = int(i)
    name = f"{base}_{i}"
    while name in bpy.data.objects:
        i += 1
        name = f"{base}_{i}"
    return name
