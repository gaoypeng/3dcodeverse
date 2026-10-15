"""lib/vegetation.py — trees, shrubs, ferns, grass and flower beds as batch geometry.

A crown is LEAVES: hundreds of small leaf cards in a volume, never one displaced ball — a
closed blob has no silhouette, lets no light through and carries one hue.  Every recipe stands
on the z you pass (``loc`` / ``center`` = the ground point: pass ``height_at(x, y)``); counts
are capped so a tree costs 3-9 k triangles (``max_leaves``).  Ground cover that must be dense
(blades, tufts) goes through ``grass_patch`` = a geometry-nodes instance cloud, which the
census counts as instances and the placement gate leaves alone.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import bpy
from mathutils import Vector

from lib.materials import material
from lib.mesh import mesh_object, sphere_template
from lib.noise import rng
from lib.scatter import gn_scatter

HeightFn = Callable[[float, float], float]
Vec3 = tuple[float, float, float]
_CROWN = {   # kind: (trunk share of the height, crown rx / height, crown rz / height, leaf size, crown lift)
    "broadleaf": (0.40, 0.30, 0.32, 0.17, 0.0),
    "oak": (0.33, 0.44, 0.30, 0.20, 0.02),
    "cypress": (0.08, 0.13, 0.46, 0.12, 0.0),
    "pine": (0.30, 0.26, 0.35, 0.14, 0.0),
}


def _leaf(center: Vector, normal: Vector, size: float, base: int) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    n = normal.normalized()
    helper = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    u = n.cross(helper).normalized() * size
    v = n.cross(u).normalized() * size
    pts = [center - u - v, center + u - v, center + u + v, center - u + v]
    return [(p.x, p.y, p.z) for p in pts], [(base, base + 1, base + 2, base + 3)]


def _leaf_cloud(batch: Any, mat: Any, center: Vector, radii: Sequence[float], count: int, size: float, r: Any,
                *, shell: float = 0.55) -> None:
    """``count`` leaf cards inside the ellipsoid ``radii`` about ``center``, biased to the
    outer ``shell`` (the inside of a crown is never seen); one ``add`` for the whole cloud."""
    verts: list[Vec3] = []
    faces: list[tuple[int, ...]] = []
    rx, ry, rz = radii
    for _ in range(max(0, int(count))):
        d = Vector((r.gauss(0.0, 1.0), r.gauss(0.0, 1.0), r.gauss(0.0, 1.0)))
        if d.length < 1e-6:
            continue
        d.normalize()
        rad = shell + (1.0 - shell) * r.uniform(0.0, 1.0) ** 0.5
        p = center + Vector((d.x * rx * rad, d.y * ry * rad, d.z * rz * rad))
        normal = d + Vector((r.uniform(-0.6, 0.6), r.uniform(-0.6, 0.6), r.uniform(-0.6, 0.6)))
        v, f = _leaf(p, normal, size * 0.5 * r.uniform(0.7, 1.3), len(verts))   # size = the card's full side
        verts.extend(v)
        faces.extend(f)
    batch.add(mat, verts, faces)


def tree(batch: Any, loc: Sequence[float], height: float, bark: Any, foliage: Any, *, seed: int = 0, kind: str = "broadleaf",
         max_leaves: int = 4000) -> None:
    """A tree ``height`` m tall standing on ``loc`` (x, y, ground z): a tapered trunk, 4-7
    branches and a crown of leaf cards (``broadleaf`` / ``oak`` / ``cypress``) or tiered
    needle cones (``pine``).  3-9 k triangles; ``max_leaves`` caps the crown."""
    r = rng(seed)
    x, y, z = (float(v) for v in loc)
    kind = kind if kind in _CROWN else "broadleaf"
    trunk_share, krx, krz, leaf, lift = _CROWN[kind]
    trunk_h = height * trunk_share
    r0 = max(0.04, height * (0.028 if kind == "cypress" else 0.036))
    batch.lathe(bark, (x, y), [(r0 * 1.3, z), (r0, z + 0.25), (r0 * 0.72, z + trunk_h), (r0 * 0.3, z + trunk_h + height * 0.28)], sides=10)
    crown_c = Vector((x, y, z + trunk_h + height * krz * (1.0 + lift)))
    radii = (height * krx, height * krx * r.uniform(0.85, 1.0), height * krz)
    if kind == "pine":
        tiers = 5
        for k in range(tiers):
            t = k / (tiers - 1)
            base_z = z + trunk_h * 0.7 + (height - trunk_h * 0.7) * t * 0.78
            rad = height * krx * (1.0 - 0.75 * t) * r.uniform(0.9, 1.1)
            batch.cone(foliage, (x, y, base_z), rad, height * 0.28 * (1.0 - 0.3 * t), top=rad * 0.08, sides=14)
        return
    for _ in range(r.randint(4, 7)):
        a = r.uniform(0.0, math.tau)
        start = Vector((x, y, z + trunk_h * r.uniform(0.75, 1.0)))
        end = crown_c + Vector((math.cos(a) * radii[0] * 0.55, math.sin(a) * radii[1] * 0.55, r.uniform(-0.2, 0.5) * radii[2]))
        batch.beam(bark, start, end, r0 * 0.35, end=r0 * 0.12, sides=6)
    count = min(int(max_leaves), int(height * height * (34 if kind != "cypress" else 48)))
    _leaf_cloud(batch, foliage, crown_c, radii, count, leaf, r)


def shrub(batch: Any, bark: Any, foliage: Any, center: Sequence[float], *, scale: float = 1.0, seed: int = 0) -> None:
    """A bush ~1.1 x ``scale`` m tall standing on ``center``: a few stems and 140 leaf cards."""
    r = rng(seed)
    x, y, z = (float(v) for v in center)
    for _ in range(4):
        a, d = r.uniform(0.0, math.tau), r.uniform(0.1, 0.4) * scale
        batch.beam(bark, (x, y, z), (x + math.cos(a) * d, y + math.sin(a) * d, z + r.uniform(0.6, 0.9) * scale), 0.02 * scale, end=0.008, sides=5)
    _leaf_cloud(batch, foliage, Vector((x, y, z + 0.62 * scale)), (0.55 * scale, 0.5 * scale, 0.5 * scale), 140, 0.11 * scale, r, shell=0.45)


def leaf_spray(batch: Any, foliage: Any, center: Sequence[float], rng_: Any, *, radius: float = 0.32, count: int = 35) -> None:
    """``count`` leaf cards within ``radius`` of ``center`` (a vine knot, a creeper on a wall)."""
    _leaf_cloud(batch, foliage, Vector(tuple(float(v) for v in center)), (radius, radius, radius), count, radius * 0.35, rng_, shell=0.2)


def _strip(batch: Any, mat: Any, spine: Sequence[Vector], widths: Sequence[float], up: Vector) -> None:
    """A ribbon of quads along ``spine`` (its width across ``up`` x direction)."""
    verts: list[Vec3] = []
    faces: list[tuple[int, ...]] = []
    for i, p in enumerate(spine):
        d = (spine[min(i + 1, len(spine) - 1)] - spine[max(i - 1, 0)])
        side = d.cross(up).normalized() * (widths[i] / 2) if d.length > 1e-6 else Vector((widths[i] / 2, 0, 0))
        verts.append((p.x - side.x, p.y - side.y, p.z - side.z))
        verts.append((p.x + side.x, p.y + side.y, p.z + side.z))
    for i in range(len(spine) - 1):
        a = 2 * i
        faces.append((a, a + 1, a + 3, a + 2))
    batch.add(mat, verts, faces, smooth=True)


def fern(batch: Any, mat: Any, center: Sequence[float], *, scale: float = 1.0, seed: int = 0) -> None:
    """A fern of 7 arching fronds, ~0.8 x ``scale`` m across, standing on ``center``."""
    r = rng(seed)
    x, y, z = (float(v) for v in center)
    for k in range(7):
        a = math.tau * k / 7 + r.uniform(-0.2, 0.2)
        L = scale * r.uniform(0.55, 0.85)
        spine = []
        widths = []
        for i in range(9):
            t = i / 8
            spine.append(Vector((x + math.cos(a) * L * t, y + math.sin(a) * L * t, z + 0.02 + L * 0.7 * t * (1.25 - t))))
            widths.append(0.22 * scale * math.sin(math.pi * min(1.0, t + 0.08)) ** 0.6)
        _strip(batch, mat, spine, widths, Vector((0.0, 0.0, 1.0)))


def tropical(batch: Any, leaves: Any, stem: Any, center: Sequence[float], *, scale: float = 1.0, seed: int = 0) -> None:
    """A banana-like plant ~2.3 x ``scale`` m tall standing on ``center``: a stem and seven
    broad arching leaves."""
    r = rng(seed)
    x, y, z = (float(v) for v in center)
    top = z + 1.4 * scale
    batch.beam(stem, (x, y, z), (x, y, top), 0.07 * scale, end=0.05 * scale, sides=8)
    for k in range(7):
        a = math.tau * k / 7 + r.uniform(-0.25, 0.25)
        L = scale * r.uniform(1.1, 1.5)
        spine = []
        widths = []
        for i in range(10):
            t = i / 9
            spine.append(Vector((x + math.cos(a) * L * t, y + math.sin(a) * L * t, top + L * (0.55 * t - 0.75 * t * t))))
            widths.append(0.42 * scale * math.sin(math.pi * min(1.0, t * 0.85 + 0.1)) ** 0.7)
        _strip(batch, leaves, spine, widths, Vector((0.0, 0.0, 1.0)))


def grass_clump(batch: Any, mats: Any, x: float, y: float, z: float, rng_: Any) -> None:
    """A tuft of 12 leaning blades 0.25-0.45 m tall at (x, y, z); ``mats`` a material or a
    list (one picked per blade).  24 triangles."""
    pool = list(mats) if isinstance(mats, (list, tuple)) else [mats]
    for _ in range(12):
        a, lean, h = rng_.uniform(0.0, math.tau), rng_.uniform(0.15, 0.5), rng_.uniform(0.25, 0.45)
        w = 0.014
        bx, by = x + rng_.uniform(-0.08, 0.08), y + rng_.uniform(-0.08, 0.08)
        tip = (bx + math.cos(a) * lean * h, by + math.sin(a) * lean * h, z + h)
        mid = (bx + math.cos(a) * lean * h * 0.35, by + math.sin(a) * lean * h * 0.35, z + h * 0.55)
        px, py = -math.sin(a) * w, math.cos(a) * w
        verts = [(bx - px, by - py, z), (bx + px, by + py, z), (mid[0] + px * 0.6, mid[1] + py * 0.6, mid[2]),
                 (mid[0] - px * 0.6, mid[1] - py * 0.6, mid[2]), tip]
        batch.add(pool[rng_.randrange(len(pool))], verts, [(0, 1, 2, 3), (3, 2, 4)])


def perennial_bed(batch: Any, center: Sequence[float], radius: float, height_at: HeightFn, foliage: Any, petals: Any, rng_: Any) -> None:
    """A flower bed within ``radius`` of ``center`` (x, y): 20 grass clumps and 30 flower heads
    on stems, each standing on ``height_at``.  ~1 100 triangles."""
    cx, cy = float(center[0]), float(center[1])
    for _ in range(20):
        a, d = rng_.uniform(0.0, math.tau), radius * math.sqrt(rng_.uniform(0.0, 1.0))
        x, y = cx + d * math.cos(a), cy + d * math.sin(a)
        grass_clump(batch, foliage, x, y, float(height_at(x, y)), rng_)
    sv, sf = sphere_template(1)
    for _ in range(30):
        a, d = rng_.uniform(0.0, math.tau), radius * math.sqrt(rng_.uniform(0.0, 1.0))
        x, y = cx + d * math.cos(a), cy + d * math.sin(a)
        z = float(height_at(x, y))
        h = rng_.uniform(0.25, 0.5)
        batch.beam(foliage, (x, y, z), (x, y, z + h), 0.006, sides=4)
        s = rng_.uniform(0.03, 0.05)
        batch.add(petals, [(x + vx * s, y + vy * s, z + h + vz * s * 0.6) for vx, vy, vz in sv], list(sf), smooth=True)


def _blade(name: str, collection: Any, height: float, color: str) -> Any:
    """The hidden blade a grass cloud copies: three tapered quads leaning +Y, base at z = 0."""
    h, w = float(height), 0.02
    verts = [(-w, 0, 0), (w, 0, 0), (w * 0.7, h * 0.12, h * 0.45), (-w * 0.7, h * 0.12, h * 0.45),
             (w * 0.35, h * 0.3, h * 0.8), (-w * 0.35, h * 0.3, h * 0.8), (0, h * 0.5, h)]
    faces = [(0, 1, 2, 3), (3, 2, 4, 5), (5, 4, 6)]
    blade = mesh_object(name, verts, faces, material(f"{name}Mat", color, roughness=0.65), collection)
    blade.location = (0.0, 0.0, -200.0)
    blade.hide_render = True
    blade.hide_viewport = True
    return blade


def grass_patch(name: str, collection: Any, surface: Any, *, blade: Any = None, density: float = 5.0, seed: int = 0,
                height: float = 0.3, color: str = "4A6B2A") -> Any:
    """Grass over ``surface`` (a mesh object — make a hidden ground patch with
    ``terrain.ground(..., center=…, size=…)`` and ``hide_render = True`` so it follows the
    terrain without drawing a second ground): ``density`` blades per m², ``height`` m tall in
    ``color``, or your own ``blade`` object.  One geometry-nodes instance cloud (exempt from
    per-asset placement); 3 quads per blade — a 12 x 12 m patch at density 5 is ~720 blades."""
    src = blade if blade is not None else _blade(f"{name}Blade", collection, height, color)
    return gn_scatter(name, collection, surface, src, density=density, seed=seed, align_normal=True, scale_range=(0.7, 1.3))


def tree_stand(collection: Any, batch: Any, positions: Sequence[Sequence[float]], height_at: HeightFn, rng_: Any, **tree_kw: Any) -> list[Any]:
    """Trees at every (x, y) of ``positions`` standing on ``height_at``, added to ``batch`` and
    finished into ``collection`` as ONE object (a copse, a treeline): returns the objects.
    ``tree_kw`` = ``bark`` and ``foliage`` (required), ``height`` (varied ±20 %), ``kind``,
    ``max_leaves``."""
    bark, foliage = tree_kw.pop("bark"), tree_kw.pop("foliage")
    base_h = float(tree_kw.pop("height", 7.0))
    for x, y in ((float(p[0]), float(p[1])) for p in positions):
        tree(batch, (x, y, float(height_at(x, y))), base_h * rng_.uniform(0.8, 1.2), bark, foliage,
             seed=rng_.randrange(1_000_000), **tree_kw)
    return batch.finish(collection)


def plant_count(obj: Any) -> int:
    """How many instances a ``grass_patch`` host places (its modifier's evaluated instances)."""
    dg = bpy.context.evaluated_depsgraph_get()
    return sum(1 for inst in dg.object_instances if inst.is_instance and inst.parent is not None and inst.parent.original == obj)
