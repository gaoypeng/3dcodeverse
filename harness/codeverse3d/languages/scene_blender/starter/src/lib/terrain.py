"""lib/terrain.py — the ground and the rock that sits on it.

INTERFACE: ``src/env.py`` exports ``height_at(x, y) -> z``, the one ground truth the zones,
``place.seat`` / ``scatter.instance`` and the placement gate sample.  Any function with that
signature qualifies — a slope, a terrace, a valley floor, a riverbed, whatever land the brief
describes; ``make_height_at`` below is one optional landform, not the ground.  Batch recipes
(``boulder``, ``talus``, ``basalt_column`` …) stand their lowest vertex ON the ``center`` z you
pass — pass ``height_at(x, y)``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

from lib.mesh import mesh_object, sphere_template
from lib.noise import fbm2, fbm3, hash01, rng

HeightFn = Callable[[float, float], float]


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0))) if e1 > e0 else (1.0 if x >= e1 else 0.0)
    return t * t * (3.0 - 2.0 * t)


def make_height_at(*, seed: int = 0, relief: float = 3.0, scale: float = 60.0, flat_radius: float = 0.0,
                   base: float = 0.0) -> HeightFn:
    """One landform among many: rolling fbm hills of up to ``relief`` m above ``base`` with
    features ``scale`` m wide, and — only when asked — a level pad within ``flat_radius`` m of
    the origin (0 = no pad; the hills blend in over ``scale`` m beyond it).  Whether a pad
    exists, where it sits and what the land does beyond it is the brief's: for a slope, a
    terrace, a ridge, a riverbed, write your own ``height_at`` — only the signature is the
    contract.  What is measured: every seated object rests on ``height_at`` within the placement
    gate's contact tolerance, and a camera's eye stays above the ground under it (the frame
    gate's ``camera_underground``) — lift the camera, not the land."""
    blend = max(1.0, scale)
    pad = max(0.0, float(flat_radius))

    def height_at(x: float, y: float) -> float:
        n = 0.5 + 0.5 * fbm2(x / scale, y / scale, octaves=4, seed=seed)
        if pad <= 0.0:
            return base + relief * n
        r = math.hypot(x, y)
        if r <= pad:
            return base
        return base + _smoothstep(pad, pad + blend, r) * relief * n

    return height_at


def ground(name: str, collection: Any, *, size: float | Sequence[float], segments: int, height_at: HeightFn,
           material: Any, center: Sequence[float] = (0.0, 0.0), smooth: bool = True) -> Any:
    """The terrain mesh: a ``size`` m grid (a number, or (x, y)) of ``segments`` cells around
    ``center`` following ``height_at``.  Extent, shape and resolution are the brief's, within
    the triangle budget (2 x ``segments`` ** 2); what is measured is that no authored camera sees
    a hard world edge, a terrain cliff or a missing horizon (rubric ``world_edge_visible``), so
    the ground reaches whatever closes the view.  Name it ``Ground…`` / ``Terrain…``: the census
    classifies the ground by name and shape, and the placement gate seats on it."""
    sx, sy = (float(size), float(size)) if isinstance(size, (int, float)) else (float(size[0]), float(size[1]))
    n = max(1, int(segments))
    cx, cy = float(center[0]), float(center[1])
    verts = []
    for iy in range(n + 1):
        y = cy - sy / 2 + sy * iy / n
        for ix in range(n + 1):
            x = cx - sx / 2 + sx * ix / n
            verts.append((x, y, float(height_at(x, y))))
    faces = []
    for iy in range(n):
        for ix in range(n):
            a = iy * (n + 1) + ix
            faces.append((a, a + 1, a + n + 2, a + n + 1))
    return mesh_object(name, verts, faces, material, collection, smooth=smooth)


def cliff(name: str, collection: Any, *, length: float, height: float, height_at: HeightFn, material: Any,
          seed: int = 0) -> Any:
    """A rock face ``length`` m long along +X (centred on the origin, facing -Y) and ``height``
    m tall, its foot on ``height_at`` and its face broken by ledges; move / rotate the object
    to place it.  ~2 400 triangles."""
    nx, nz = max(2, int(length / 1.5)), max(2, int(height / 1.2))
    verts = []
    for iz in range(nz + 1):
        v = iz / nz
        for ix in range(nx + 1):
            u = ix / nx
            x = -length / 2 + length * u
            bulge = 1.4 * fbm2(u * 6.0, v * 4.0, octaves=4, seed=seed) + 0.6 * (1.0 - v)
            ledge = 0.35 * math.sin(v * 9.0 + fbm2(u * 3.0, 7.0, octaves=2, seed=seed) * 2.0)
            z = float(height_at(x, 0.0)) + height * v + (0.0 if iz == 0 else 0.4 * fbm2(u * 9.0, v * 9.0, octaves=2, seed=seed + 3))
            verts.append((x, -bulge - ledge, z))
    # a back wall and a top so the face is a closed slab
    top = len(verts)
    for ix in range(nx + 1):
        x = -length / 2 + length * ix / nx
        verts.append((x, 2.5, float(height_at(x, 2.5)) + height + 0.3))
    faces = []
    for iz in range(nz):
        for ix in range(nx):
            a = iz * (nx + 1) + ix
            faces.append((a, a + nx + 1, a + nx + 2, a + 1))
    for ix in range(nx):
        a = nz * (nx + 1) + ix
        faces.append((a, a + 1, top + ix + 1, top + ix))
    return mesh_object(name, verts, faces, material, collection, smooth=True)


def _sphere_cloud(center: Sequence[float], scale: Sequence[float], seed: int, detail: int, *, rough: float = 0.28, squash: float = 0.6
                  ) -> tuple[list[tuple[float, float, float]], list[tuple[int, ...]]]:
    """An icosphere displaced by fbm3, flattened underneath, standing on ``center`` z."""
    verts, faces = sphere_template(detail)
    out = []
    sx, sy, sz = scale
    for x, y, z in verts:
        d = 1.0 + rough * fbm3(x * 1.7 + seed, y * 1.7, z * 1.7 - seed, octaves=3, seed=seed)
        out.append((x * d * sx, y * d * sy, (z * d * sz) if z >= 0 else z * d * sz * squash))
    zmin = min(v[2] for v in out)
    cx, cy, cz = (float(c) for c in center)
    return [(cx + x, cy + y, cz + (z - zmin)) for x, y, z in out], list(faces)


def boulder(batch: Any, mat: Any, center: Sequence[float], scale: float | Sequence[float], *, seed: int = 0, detail: int = 3) -> None:
    """A rock of ``scale`` m (a number, or (x, y, z) extents) standing on ``center`` z;
    ``detail`` 2 = 80 triangles (a field), 3 = 320 (near the camera), 4 = 1 280 (a hero)."""
    s = (scale, scale, scale * 0.8) if isinstance(scale, (int, float)) else tuple(float(v) for v in scale)
    v, f = _sphere_cloud(center, (s[0] / 2, s[1] / 2, s[2] / 2), seed, detail)
    batch.add(mat, v, f, smooth=True)


def rock_field(batch: Any, mat: Any, center: Sequence[float], radius: float, count: int, height_at: HeightFn, rng_: Any) -> None:
    """``count`` boulders of 0.3-1.4 m scattered within ``radius`` of ``center`` (x, y), each
    standing on ``height_at`` — 80 triangles apiece."""
    cx, cy = float(center[0]), float(center[1])
    for i in range(max(0, int(count))):
        a, r = rng_.uniform(0.0, math.tau), radius * math.sqrt(rng_.uniform(0.0, 1.0))
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        s = rng_.uniform(0.3, 1.4)
        boulder(batch, mat, (x, y, float(height_at(x, y))), (s, s * rng_.uniform(0.7, 1.2), s * rng_.uniform(0.5, 0.8)),
                seed=int(rng_.uniform(0, 10_000)) + i, detail=2)


def talus(batch: Any, mat: Any, center: Sequence[float], scale: float, seed: int) -> None:
    """A scree heap ``scale`` m across standing on ``center`` z: ~40 small rocks piled in a cone
    (the lower ones on the ground, the upper ones on the heap) — ~800 triangles."""
    r = rng(seed)
    cx, cy, cz = (float(c) for c in center)
    heap_h = scale * 0.45
    for i in range(40):
        a, d = r.uniform(0.0, math.tau), math.sqrt(r.uniform(0.0, 1.0))
        x, y = cx + d * scale / 2 * math.cos(a), cy + d * scale / 2 * math.sin(a)
        surface = heap_h * max(0.0, 1.0 - d) ** 1.3
        s = scale * r.uniform(0.08, 0.2)
        boulder(batch, mat, (x, y, cz + surface * 0.85), (s, s * r.uniform(0.6, 1.0), s * 0.7), seed=seed * 7 + i, detail=1)


def basalt_column(batch: Any, materials: Sequence[Any], center: Sequence[float], radius: float, height: float, rng_: Any,
                  lean: Sequence[float] = (0.0, 0.0)) -> None:
    """A cluster of hexagonal basalt columns within ``radius`` of ``center`` (x, y, ground z),
    the tallest ``height`` m, cycling ``materials``; ``lean`` (dx, dy per metre of height) tilts
    the whole cluster.  Each column is 8 quads."""
    cx, cy, cz = (float(c) for c in center)
    step = max(0.3, radius / 2.6)
    k = 0
    for i in range(-3, 4):
        for j in range(-3, 4):
            x = cx + step * (i + 0.5 * (j % 2))
            y = cy + step * 0.866 * j
            if math.hypot(x - cx, y - cy) > radius:
                continue
            h = height * (0.35 + 0.65 * (1.0 - math.hypot(x - cx, y - cy) / radius)) * rng_.uniform(0.75, 1.0)
            r = step * 0.52
            top = [(x + r * math.cos(a) + lean[0] * h, y + r * math.sin(a) + lean[1] * h, cz + h) for a in
                   (math.tau * s / 6 for s in range(6))]
            bottom = [(x + r * math.cos(a), y + r * math.sin(a), cz) for a in (math.tau * s / 6 for s in range(6))]
            verts = bottom + top
            faces = [(s, (s + 1) % 6, 6 + (s + 1) % 6, 6 + s) for s in range(6)] + [tuple(range(6, 12))]
            batch.add(materials[k % len(materials)], verts, faces)
            k += 1


def shoreline_mask(x: float, y: float, water_level: float, height_at: HeightFn) -> float:
    """0 under water, 1 half a metre above it, smooth between — a scatter mask that keeps
    reeds on the bank and trees off the beach."""
    return _smoothstep(0.0, 0.5, float(height_at(x, y)) - float(water_level))


def jitter(i: int, seed: int = 0) -> float:
    """-1..1 from an index: the cheap per-item variation (``hash01``) in signed form."""
    return hash01(i, seed) * 2.0 - 1.0
