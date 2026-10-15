"""lib/mesh.py — a Batch of geometry → a few mesh objects under ONE root.

Why a Batch: the placement census is per OBJECT and Cycles syncs per object — a prop built as
sixty bpy primitives is sixty placement rows and sixty objects, a prop built as one Batch is
ONE row (its root Empty) and one mesh per material.  Nothing here touches ``bpy.ops``: vertices
and faces stay plain lists until ``finish`` writes them with ``from_pydata``.

Frame: Z up, -Y front, metres.  Primitive ``loc`` is the CENTRE (``cone`` / ``lathe``: the base
on the axis); every finished object gets its origin at its base centre, so ``place.seat`` and
``place.face_toward`` act about the foot of the prop.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import bmesh
import bpy
from mathutils import Matrix, Vector

Vec3 = tuple[float, float, float]
_SPHERES: dict[int, tuple[list[Vec3], list[tuple[int, ...]]]] = {}


def sphere_template(level: int) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    """A unit icosphere's (vertices, triangles): level 1 = 20 faces, 2 = 80, 3 = 320, 4 = 1280
    (cached; copy before mutating)."""
    level = max(1, min(5, int(level)))
    if level not in _SPHERES:
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=level, radius=1.0)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        verts = [(v.co.x, v.co.y, v.co.z) for v in bm.verts]
        faces = [tuple(v.index for v in f.verts) for f in bm.faces]
        bm.free()
        _SPHERES[level] = (verts, faces)
    return _SPHERES[level]


def _axes(d: Vector) -> tuple[Vector, Vector]:
    """Two unit vectors orthogonal to ``d`` (right-handed with it)."""
    d = d.normalized()
    helper = Vector((0.0, 0.0, 1.0)) if abs(d.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    u = d.cross(helper).normalized()
    return u, d.cross(u).normalized()


def _ring(center: Vector, u: Vector, v: Vector, radius: float, sides: int, phase: float = 0.0) -> list[Vec3]:
    out = []
    for j in range(sides):
        a = phase + math.tau * j / sides
        p = center + u * (radius * math.cos(a)) + v * (radius * math.sin(a))
        out.append((p.x, p.y, p.z))
    return out


def _band(a: int, b: int, sides: int) -> list[tuple[int, ...]]:
    """Quads between ring ``a`` and ring ``b`` (vertex offsets), outward for CCW rings."""
    return [(a + j, a + (j + 1) % sides, b + (j + 1) % sides, b + j) for j in range(sides)]


def box_geometry(loc: Sequence[float], size: float | Sequence[float], rotation: float = 0.0) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    """A box centred at ``loc`` (yaw ``rotation`` radians about Z); 8 vertices, 6 quads."""
    sx, sy, sz = (size, size, size) if isinstance(size, (int, float)) else size
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    c, s = math.cos(rotation), math.sin(rotation)
    verts = []
    for x, y, z in ((-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
                    (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)):
        verts.append((loc[0] + x * c - y * s, loc[1] + x * s + y * c, loc[2] + z))
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return verts, faces


def lathe_geometry(xy: Sequence[float], profile: Sequence[Sequence[float]], sides: int = 32,
                   phase: float = 0.0) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    """``profile`` = [(radius, z), ...] revolved about the vertical axis through ``xy``; a radius
    of 0 collapses to a point, an open end is capped."""
    sides = max(3, int(sides))
    verts: list[Vec3] = []
    faces: list[tuple[int, ...]] = []
    starts: list[int | None] = []   # ring start index, or the apex vertex index when r == 0
    apex: list[bool] = []
    for r, z in profile:
        if r <= 1e-6:
            starts.append(len(verts))
            apex.append(True)
            verts.append((float(xy[0]), float(xy[1]), float(z)))
        else:
            starts.append(len(verts))
            apex.append(False)
            verts.extend(_ring(Vector((xy[0], xy[1], z)), Vector((1, 0, 0)), Vector((0, 1, 0)), float(r), sides, phase))
    for i in range(len(profile) - 1):
        a, b = starts[i], starts[i + 1]
        if apex[i] and apex[i + 1]:
            continue
        if apex[i]:
            faces.extend((a, b + (j + 1) % sides, b + j) for j in range(sides))
        elif apex[i + 1]:
            faces.extend((a + j, a + (j + 1) % sides, b) for j in range(sides))
        else:
            faces.extend(_band(a, b, sides))
    if not apex[0]:
        faces.append(tuple(starts[0] + j for j in reversed(range(sides))))
    if not apex[-1]:
        faces.append(tuple(starts[-1] + j for j in range(sides)))
    return verts, faces


def beam_geometry(a: Sequence[float], b: Sequence[float], radius: float, end: float | None = None,
                  sides: int = 8) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    """A capped cylinder from ``a`` to ``b`` (radius ``end`` at ``b`` for a taper)."""
    sides = max(3, int(sides))
    pa, pb = Vector(a), Vector(b)
    d = pb - pa
    if d.length < 1e-6:
        d = Vector((0, 0, 1e-3))
    u, v = _axes(d)
    verts = _ring(pa, u, v, radius, sides) + _ring(pb, u, v, radius if end is None else end, sides)
    faces = _band(0, sides, sides)
    faces.append(tuple(reversed(range(sides))))
    faces.append(tuple(range(sides, 2 * sides)))
    return verts, faces


def torus_geometry(loc: Sequence[float], radius: float, tube: float, major: int = 64, minor: int = 8,
                   axis: Sequence[float] = (0.0, 0.0, 1.0)) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    """A torus centred at ``loc`` with its hole along ``axis``."""
    major, minor = max(3, int(major)), max(3, int(minor))
    rot = Vector((0.0, 0.0, 1.0)).rotation_difference(Vector(axis).normalized()).to_matrix()
    centre = Vector(loc)
    verts: list[Vec3] = []
    for i in range(major):
        a = math.tau * i / major
        ring_c = Vector((radius * math.cos(a), radius * math.sin(a), 0.0))
        radial = Vector((math.cos(a), math.sin(a), 0.0))
        for j in range(minor):
            t = math.tau * j / minor
            p = ring_c + radial * (tube * math.cos(t)) + Vector((0.0, 0.0, tube * math.sin(t)))
            p = centre + rot @ p
            verts.append((p.x, p.y, p.z))
    faces = []
    for i in range(major):
        i2 = (i + 1) % major
        for j in range(minor):
            j2 = (j + 1) % minor
            faces.append((i * minor + j, i2 * minor + j, i2 * minor + j2, i * minor + j2))
    return verts, faces


def sphere_geometry(loc: Sequence[float], scale: float | Sequence[float], subdivision: int = 1) -> tuple[list[Vec3], list[tuple[int, ...]]]:
    sx, sy, sz = (scale, scale, scale) if isinstance(scale, (int, float)) else scale
    verts, faces = sphere_template(subdivision)
    return [(loc[0] + x * sx, loc[1] + y * sy, loc[2] + z * sz) for x, y, z in verts], list(faces)


class _Part:
    __slots__ = ("faces", "material", "smooth", "verts")

    def __init__(self, material: Any) -> None:
        self.material = material
        self.verts: list[Vec3] = []
        self.faces: list[tuple[int, ...]] = []
        self.smooth: list[bool] = []


class Batch:
    """Geometry accumulated per material; ``finish`` writes the objects.

    ``Batch("Oak_3")`` then ``b.box(...)``, ``b.beam(...)`` …; ``b.transformed(matrix)`` is a view
    that transforms what is added through it (a figure facing ``heading``, a lantern hung at an
    angle); ``b.finish(ctx.collection)`` returns ``[root, mesh, …]`` — the root is the placement
    row and the thing to ``place.seat`` / tag ``root["placement"] = "free"``.
    """

    def __init__(self, name: str) -> None:
        self.name = str(name)
        self._parts: dict[str, _Part] = {}
        self._matrix: Matrix | None = None
        self._root: Batch = self

    # ------------------------------------------------------------------ accumulation
    def add(self, mat: Any, vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], smooth: bool | Sequence[bool] = False) -> int:
        """Append raw geometry under material ``mat``; returns the part's vertex count before."""
        key = mat.name if mat is not None else ""
        part = self._root._parts.get(key)
        if part is None:
            part = self._root._parts[key] = _Part(mat)
        base = len(part.verts)
        if self._matrix is not None:
            m = self._matrix
            part.verts.extend(tuple(m @ Vector(v)) for v in vertices)
        else:
            part.verts.extend((float(v[0]), float(v[1]), float(v[2])) for v in vertices)
        part.faces.extend(tuple(int(i) + base for i in f) for f in faces)
        if isinstance(smooth, bool):
            part.smooth.extend([smooth] * len(faces))
        else:
            flags = list(smooth)
            part.smooth.extend(bool(flags[i]) if i < len(flags) else False for i in range(len(faces)))
        return base

    def transformed(self, matrix: Matrix | Sequence[Sequence[float]]) -> Batch:
        """A view of this batch whose additions are transformed by ``matrix`` first."""
        view = Batch.__new__(Batch)
        view.name = self.name
        view._parts = self._root._parts
        view._root = self._root
        m = Matrix(matrix)
        view._matrix = (self._matrix @ m) if self._matrix is not None else m
        return view

    def box(self, mat: Any, loc: Sequence[float], size: float | Sequence[float], rotation: float = 0.0) -> None:
        self.add(mat, *box_geometry(loc, size, rotation))

    def sphere(self, mat: Any, loc: Sequence[float], scale: float | Sequence[float], subdivision: int = 1) -> None:
        self.add(mat, *sphere_geometry(loc, scale, subdivision), smooth=True)

    def lathe(self, mat: Any, xy: Sequence[float], profile: Sequence[Sequence[float]], sides: int = 32, phase: float = 0.0) -> None:
        self.add(mat, *lathe_geometry(xy, profile, sides, phase), smooth=True)

    def cone(self, mat: Any, loc: Sequence[float], radius: float, height: float, top: float = 0.0, sides: int = 24) -> None:
        """A cone / frustum standing on its base centre ``loc``."""
        self.lathe(mat, (loc[0], loc[1]), [(radius, loc[2]), (top, loc[2] + height)], sides)

    def beam(self, mat: Any, a: Sequence[float], b: Sequence[float], radius: float, end: float | None = None, sides: int = 8) -> None:
        self.add(mat, *beam_geometry(a, b, radius, end, sides), smooth=sides >= 12)

    def torus(self, mat: Any, loc: Sequence[float], radius: float, tube: float, major: int = 64, minor: int = 8) -> None:
        self.add(mat, *torus_geometry(loc, radius, tube, major, minor), smooth=True)

    def ring(self, mat: Any, center: Sequence[float], normal: Sequence[float], radius: float, tube: float, segments: int = 96) -> None:
        """A hoop of radius ``radius`` whose axis is ``normal`` (an arch rib, a wheel rim)."""
        self.add(mat, *torus_geometry(center, radius, tube, segments, 8, axis=normal), smooth=True)

    # ------------------------------------------------------------------ output
    def bounds(self) -> tuple[Vector, Vector] | None:
        lo = Vector((math.inf, math.inf, math.inf))
        hi = Vector((-math.inf, -math.inf, -math.inf))
        seen = False
        for part in self._root._parts.values():
            for x, y, z in part.verts:
                seen = True
                lo.x, lo.y, lo.z = min(lo.x, x), min(lo.y, y), min(lo.z, z)
                hi.x, hi.y, hi.z = max(hi.x, x), max(hi.y, y), max(hi.z, z)
        return (lo, hi) if seen else None

    def finish(self, collection: Any, *, bevel: float = 0.0, parent: bool = True, name_prefix: str = "") -> list[Any]:
        """Write one mesh object per material into ``collection``: under ONE root Empty named
        after the batch (``parent=True``, the default — one placement row however many
        materials), else the meshes alone.  Every object's origin is the batch's base centre.
        ``bevel`` > 0 adds a Bevel modifier (angle-limited) to each mesh."""
        parts = [p for p in self._root._parts.values() if p.faces]
        if not parts:
            return []
        lo, hi = self.bounds()  # type: ignore[misc]
        origin = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
        base_name = f"{name_prefix}{self.name}"
        out: list[Any] = []
        root = None
        if parent:
            root = bpy.data.objects.new(base_name, None)
            root.empty_display_size = 0.25
            root.location = origin
            collection.objects.link(root)
            out.append(root)
        for part in parts:
            mat_tag = part.material.name.replace(" ", "") if part.material is not None else "Mesh"
            name = f"{base_name}_{mat_tag}" if (parent or len(parts) > 1) else base_name
            me = bpy.data.meshes.new(name)
            me.from_pydata([(x - origin.x, y - origin.y, z - origin.z) for x, y, z in part.verts], [], part.faces)
            me.update()
            if len(me.polygons) == len(part.smooth):
                me.polygons.foreach_set("use_smooth", part.smooth)
            if part.material is not None:
                me.materials.append(part.material)
            ob = bpy.data.objects.new(name, me)
            if root is not None:
                ob.parent = root
            else:
                ob.location = origin
            collection.objects.link(ob)
            if bevel > 0:
                mod = ob.modifiers.new("Bevel", "BEVEL")
                mod.width = float(bevel)
                mod.segments = 2
                mod.limit_method = "ANGLE"
                mod.angle_limit = math.radians(40.0)
            out.append(ob)
        return out


def mesh_object(name: str, vertices: Sequence[Sequence[float]], faces: Sequence[Sequence[int]], mat: Any, collection: Any,
                *, smooth: bool = False) -> Any:
    """One mesh object from raw lists, linked into ``collection`` (origin at the world origin)."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(float(c) for c in v) for v in vertices], [], [tuple(int(i) for i in f) for f in faces])
    me.update()
    if smooth:
        me.shade_smooth()
    if mat is not None:
        me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    collection.objects.link(ob)
    return ob


def curve_object(name: str, points: Sequence[Sequence[float]], radius: float, mat: Any, collection: Any,
                 *, cyclic: bool = False) -> Any:
    """A tube along ``points`` (a POLY spline with a round bevel) — a rope, a cable, a vine."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = float(radius)
    cu.bevel_resolution = 3
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for i, p in enumerate(points):
        sp.points[i].co = (float(p[0]), float(p[1]), float(p[2]), 1.0)
    sp.use_cyclic_u = bool(cyclic)
    if mat is not None:
        cu.materials.append(mat)
    ob = bpy.data.objects.new(name, cu)
    collection.objects.link(ob)
    return ob


def parent_under(objects: Sequence[Any], collection: Any, name: str) -> Any:
    """An Empty named ``name`` in ``collection`` that becomes the parent of ``objects`` (their
    world poses kept): several objects as ONE placement row."""
    root = bpy.data.objects.new(name, None)
    root.empty_display_size = 0.25
    collection.objects.link(root)
    for ob in objects:
        ob.parent = root
    return root
