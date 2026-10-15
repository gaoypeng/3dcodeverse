"""lib/water.py — water surfaces, falls, jets and spray.

A water surface is ONE refracting plane (``materials.water``); the renderer refracts the bed
through it, so give it a bed (the ground, a basin).  Name water things ``…Water`` / ``…Pond``
/ ``…Lake``: the census files a water-named surface as a backdrop and a foot in it as
"on water" (a boat, a jetty post), never floating or sunk.  Falls and jets are foam sheets
and thin tubes — no fluid solver; with ``ctx`` their texture drifts between ``ctx.times``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import bpy

from lib.materials import _bump, _noise, _ramp
from lib.materials import water as water_material
from lib.mesh import Batch, mesh_object
from lib.motion import drift, scroll
from lib.noise import fbm2, rng
from lib.place import unique_name

G = 9.81


def foam_shader(name: str) -> Any:
    """White water: a rough, slightly translucent surface with noisy alpha (edges thin out)."""
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nodes, links, bsdf = nt.nodes, nt.links, nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.9, 0.94, 0.97, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.75
    bsdf.inputs["Transmission Weight"].default_value = 0.2
    tc = nodes.new("ShaderNodeTexCoord")
    mp = nodes.new("ShaderNodeMapping")
    mp.name = "FlowMapping"
    mp.inputs["Scale"].default_value = (2.0, 2.0, 0.6)
    links.new(tc.outputs["Object"], mp.inputs["Vector"])
    n = _noise(nodes, links, mp.outputs["Vector"], scale=3.0, detail=5.0, roughness=0.6)
    alpha = _ramp(nodes, links, n.outputs["Fac"], [(0.3, (0.15, 0.15, 0.15, 1)), (0.7, (0.95, 0.95, 0.95, 1))])
    links.new(alpha, bsdf.inputs["Alpha"])
    _bump(nodes, links, n.outputs["Fac"], bsdf, strength=0.3, distance=0.02)
    mat.surface_render_method = "BLENDED"
    return mat


def water_surface(name: str, collection: Any, *, size: float | Sequence[float], level: float, material: Any = None,
                  ripple: float = 0.045, seed: int = 0, height_fn: Callable[[float, float], float] | None = None) -> Any:
    """A ``size`` m water plane at z = ``level`` with ``ripple`` m of gentle swell, centred on
    the origin (move the object to place it).  With ``height_fn`` the cells where the ground
    stands more than 0.25 m above the water are dropped, so a lake never pokes through its
    banks.  Default material: ``materials.water``.  ~4 600 triangles."""
    sx, sy = (float(size), float(size)) if isinstance(size, (int, float)) else (float(size[0]), float(size[1]))
    n = 48
    verts = []
    keep = []
    for iy in range(n + 1):
        y = -sy / 2 + sy * iy / n
        for ix in range(n + 1):
            x = -sx / 2 + sx * ix / n
            verts.append((x, y, float(level) + ripple * fbm2(x * 0.35, y * 0.35, octaves=3, seed=seed)))
            keep.append(height_fn is None or float(height_fn(x, y)) <= level + 0.25)
    faces = []
    for iy in range(n):
        for ix in range(n):
            a = iy * (n + 1) + ix
            quad = (a, a + 1, a + n + 2, a + n + 1)
            if any(keep[i] for i in quad):
                faces.append(quad)
    used = sorted({i for f in faces for i in f})
    remap = {old: new for new, old in enumerate(used)}
    mat = material if material is not None else water_material(f"{name}Mat")
    return mesh_object(name, [verts[i] for i in used], [tuple(remap[i] for i in f) for f in faces], mat, collection, smooth=True)


def pool_height(x: float, y: float, *, level: float = -0.25) -> float:
    """A ``height_at`` for things that float: the water ``level`` everywhere (seat a boat, a
    lily pad or a lantern with ``place.seat(obj, lambda x, y: pool_height(x, y, level=-0.25))``)."""
    return float(level)


def _sheet(name: str, collection: Any, mat: Any, origin: Sequence[float], across: Sequence[float], along: Sequence[float],
           width: float, height: float, *, seed: int, speed: float, offset: float) -> Any:
    """One falling sheet: top edge ``width`` wide at ``origin`` (centre of the lip), falling
    ``height`` with a parabolic throw along ``along``; rows wobble with noise."""
    rows, cols = 24, 8
    ax, ay = float(across[0]), float(across[1])
    lx, ly = float(along[0]), float(along[1])
    ox, oy, oz = (float(v) for v in origin)
    verts = []
    for r in range(rows + 1):
        drop = height * r / rows
        throw = speed * math.sqrt(2.0 * drop / G) + offset
        for c in range(cols + 1):
            u = c / cols - 0.5
            wob = 0.05 * height * fbm2(u * 4.0 + seed, r / rows * 3.0, octaves=2, seed=seed)
            x = ox + ax * u * width + lx * (throw + wob)
            y = oy + ay * u * width + ly * (throw + wob)
            verts.append((x, y, oz - drop))
    faces = [(r * (cols + 1) + c, r * (cols + 1) + c + 1, (r + 1) * (cols + 1) + c + 1, (r + 1) * (cols + 1) + c)
             for r in range(rows) for c in range(cols)]
    return mesh_object(name, verts, faces, mat, collection, smooth=True)


def waterfall(name: str, collection: Any, center: Sequence[float], *, width: float, height: float, seed: int = 0,
              ctx: Any = None, layers: int = 4) -> list[Any]:
    """A fall ``width`` m wide dropping ``height`` m onto ``center`` (x, y, z of the impact —
    the pool level), the lip ``height`` above it; ``layers`` foam sheets staggered outward,
    all under one root Empty (one placement row, its foot at the impact).  Flow is toward -Y.
    With ``ctx`` the foam streams down between ``ctx.times``.  ~400 triangles per layer."""
    cx, cy, cz = (float(v) for v in center)
    root = bpy.data.objects.new(name, None)
    root.empty_display_size = 0.25
    root.location = (cx, cy, cz)
    collection.objects.link(root)
    mat = foam_shader(f"{name}Foam")
    out = [root]
    for k in range(max(1, int(layers))):
        sheet = _sheet(f"{name}Sheet_{k}", collection, mat, (cx, cy, cz + height), (1.0, 0.0), (0.0, -1.0), width * (1.0 + 0.06 * k),
                       height, seed=seed + k, speed=0.9 + 0.25 * k, offset=0.08 * k)
        sheet.parent = root
        sheet.matrix_parent_inverse = root.matrix_basis.inverted()
        out.append(sheet)
    if ctx is not None:
        scroll(mat.node_tree.nodes["FlowMapping"], (0.0, 0.0, -2.5), ctx)
    return out


def hanging_fall(name: str, collection: Any, outlet: Sequence[float], along: Sequence[float], across: Sequence[float], *,
                 width: float, height: float, seed: int = 0, ctx: Any = None) -> list[Any]:
    """A single sheet from an ``outlet`` (x, y, z of a spout, a gutter, a broken aqueduct)
    thrown along the unit direction ``along`` (x, y) and ``width`` m wide along ``across``,
    falling ``height`` m.  Root Empty at the outlet; tag it ``["placement"] = "free"`` when it
    hangs over nothing.  With ``ctx`` the foam streams down."""
    ox, oy, oz = (float(v) for v in outlet)
    root = bpy.data.objects.new(name, None)
    root.empty_display_size = 0.25
    root.location = (ox, oy, oz)
    collection.objects.link(root)
    mat = foam_shader(f"{name}Foam")
    sheet = _sheet(f"{name}Sheet", collection, mat, (ox, oy, oz), across, along, width, height, seed=seed, speed=1.2, offset=0.0)
    sheet.parent = root
    sheet.matrix_parent_inverse = root.matrix_basis.inverted()
    if ctx is not None:
        scroll(mat.node_tree.nodes["FlowMapping"], (0.0, 0.0, -2.5), ctx)
    return [root, sheet]


def droplets(batch: Any, mat: Any, center: Sequence[float], width: float, bottom: float, rng_: Any, count: int = 260) -> None:
    """``count`` tiny tetrahedra (spray, drips, sparks) in a cloud ``width`` m wide centred on
    ``center`` (x, y, z), its lowest at z = ``bottom``; 4 triangles each."""
    cx, cy, cz = (float(v) for v in center)
    top = cz + (cz - float(bottom))
    s = 0.012
    for _ in range(max(0, int(count))):
        x, y = cx + rng_.uniform(-width / 2, width / 2), cy + rng_.uniform(-width / 2, width / 2)
        z = rng_.uniform(float(bottom) + s, top)
        verts = [(x + s, y + s, z + s), (x - s, y - s, z + s), (x - s, y + s, z - s), (x + s, y - s, z - s)]
        batch.add(mat, verts, [(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)])


def fountain_jets(batch: Any, collection: Any, water_mat: Any, *, center: Sequence[float], nozzle_r: float, landing_r: float,
                  count: int = 14, rise: float = 1.85, ctx: Any = None, seed: int = 0) -> Any:
    """``count`` parabolic jets from a ring of radius ``nozzle_r`` to one of ``landing_r``
    around ``center`` (x, y, z of the basin's water level), peaking ``rise`` m up, as thin
    tubes in ``batch`` plus a spray cloud at the landing ring.  With ``ctx`` a second spray
    cloud is finished into ``collection`` (tagged free) and falls between ``ctx.times``;
    returned, else None.  ~220 triangles per jet."""
    cx, cy, cz = (float(v) for v in center)
    r = rng(seed)
    for i in range(max(1, int(count))):
        a = math.tau * i / count + r.uniform(-0.05, 0.05)
        pts = []
        for k in range(13):
            t = k / 12
            rad = nozzle_r + (landing_r - nozzle_r) * t
            z = cz + 4.0 * rise * t * (1.0 - t)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a), z))
        for k in range(12):
            batch.beam(water_mat, pts[k], pts[k + 1], 0.02, sides=6)
    droplets(batch, water_mat, (cx, cy, cz + 0.22), landing_r * 2.2, cz + 0.01, r, count=140)
    if ctx is None:
        return None
    spray = Batch(unique_name(f"{batch.name}Spray", 0))
    droplets(spray, water_mat, (cx, cy, cz + rise * 0.55), landing_r * 1.6, cz + rise * 0.2, r, count=160)
    objs = spray.finish(collection)
    objs[0]["placement"] = "free"
    drift(objs[0], (0.0, 0.0, -rise * 0.3), ctx)
    return objs[0]
