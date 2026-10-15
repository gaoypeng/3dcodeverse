"""lib/architecture.py — buildings, bridges, roofs, paving, room shells and signs.

Batch recipes stand on the ground z you pass (``center`` = (x, y, ground z), pass
``height_at(x, y)``); the ridge / long axis runs along +X unless a ``direction`` says
otherwise (compass degrees: 0 = +X, 90 = +Y, for a window's outward normal).  Paving is 2 cm
thick so a prop seated on ``height_at`` still reads as resting on it (the gate's contact
tolerance).  ``room_shell`` is for ``src/env.py``: it names the lid ``Ceiling`` under a
``RoomShell`` root so the overview rig can lift it; ``glazed=True`` makes it a glass house (glass
panes on an iron-frame grid, the sun coming through).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import bpy
from mathutils import Matrix, Vector

from lib.materials import glass
from lib.mesh import box_geometry, mesh_object
from lib.noise import hash01

Vec3 = tuple[float, float, float]
_WALLS = ("front", "back", "left", "right")
_BAY, _BAR, _PANE = (1.6, 1.6, 2.4), 0.08, 0.02   # a glazed shell's frame spacing per world axis, bar section, glass (m)


def _yaw(direction_deg: float) -> Matrix:
    """The rotation that turns a -Y-facing thing to face compass ``direction_deg``."""
    return Matrix.Rotation(math.radians(direction_deg) + math.pi / 2, 4, "Z")


def arched_opening(batch: Any, mat: Any, center: Sequence[float], width: float, height: float, *, depth: float = 0.28,
                   thickness: float = 0.2) -> None:
    """A round arch surround in a wall along X: two jambs ``thickness`` wide and an arch ring
    over an opening ``width`` wide and ``height`` tall (to the crown), ``depth`` through the
    wall, standing on ``center`` (x, y, ground z)."""
    cx, cy, cz = (float(v) for v in center)
    r_in, r_out = width / 2, width / 2 + thickness
    jamb_h = max(0.1, height - r_in)
    for sx in (-1, 1):
        batch.box(mat, (cx + sx * (r_in + thickness / 2), cy, cz + jamb_h / 2), (thickness, depth, jamb_h))
    n = 14
    verts: list[Vec3] = []
    faces: list[tuple[int, ...]] = []
    for k in range(n + 1):
        a = math.pi * k / n
        ca, sa = math.cos(a), math.sin(a)
        for yy in (cy - depth / 2, cy + depth / 2):
            verts.append((cx + r_in * ca, yy, cz + jamb_h + r_in * sa))
            verts.append((cx + r_out * ca, yy, cz + jamb_h + r_out * sa))
    for k in range(n):
        a = 4 * k
        faces.append((a, a + 4, a + 5, a + 1))          # front face (-y)
        faces.append((a + 2, a + 3, a + 7, a + 6))      # back face (+y)
        faces.append((a + 1, a + 5, a + 7, a + 3))      # outer (extrados)
        faces.append((a, a + 2, a + 6, a + 4))          # inner (intrados)
    batch.add(mat, verts, faces)


def _slope(batch: Any, mats: Any, center: Sequence[float], width: float, depth: float, rise: float, side: int, rng_: Any,
           *, style: str) -> None:
    """One roof slope: a plank underlay plus tiles, built flat then tilted about X."""
    tile, trim = mats
    half = depth / 2
    run = math.hypot(half, rise)
    pitch = math.atan2(rise, half)
    cx, cy, cz = (float(v) for v in center)
    # local frame: x along the ridge, y from the eave (0) up the slope to the ridge (run), z the
    # slope's outward normal; the +Y slope is the -Y one yawed 180° about the ridge's foot
    flip = Matrix.Rotation(math.pi, 4, "Z") if side > 0 else Matrix.Identity(4)
    m = Matrix.Translation((cx, cy + side * half, cz)) @ flip @ Matrix.Rotation(pitch, 4, "X")
    view = batch.transformed(m)
    view.box(trim, (0.0, run / 2, 0.02), (width + 0.4, run + 0.25, 0.04))
    if style == "tiles":
        tw, th = 0.34, 0.3
        if (width / tw) * (run / th) > 1400:
            k = math.sqrt((width / tw) * (run / th) / 1400)
            tw, th = tw * k, th * k
        cols, rows = max(1, int(width / tw)), max(1, int(run / th))
        for r in range(rows):
            for c in range(cols):
                x = -width / 2 + tw * (c + 0.5) + (tw / 2 if r % 2 else 0.0)
                y = th * (r + 0.5) + 0.1
                view.box(tile, (x, y, 0.065 + 0.012 * rng_.uniform(-1, 1)), (tw * 0.96, th * 1.08, 0.03), rotation=rng_.uniform(-0.03, 0.03))
    else:
        pitch_w = 0.2
        n = max(1, int(width / pitch_w))
        for c in range(n + 1):
            x = -width / 2 + pitch_w * c
            view.beam(tile, (x, 0.0, 0.07), (x, run, 0.07), 0.08, sides=8)
            if c < n:
                view.beam(tile, (x + pitch_w / 2, 0.0, 0.0), (x + pitch_w / 2, run, 0.0), 0.08, sides=8)


def tiled_roof(batch: Any, tile: Any, trim: Any, center: Sequence[float], width: float, depth: float, rise: float, rng_: Any) -> None:
    """A pitched roof with its ridge along X: eaves ``width`` x ``depth`` resting on ``center``
    (x, y, eave z), ridge ``rise`` higher.  Flat tiles (≤ 1 400 per slope: 17 k triangles)
    over a plank underlay in ``trim``, a ridge beam on top."""
    for side in (-1, 1):
        _slope(batch, (tile, trim), center, width, depth, rise, side, rng_, style="tiles")
    cx, cy, cz = (float(v) for v in center)
    batch.box(trim, (cx, cy, cz + rise + 0.08), (width + 0.4, 0.22, 0.14))


def clay_roof(batch: Any, frame: Any, tiles: Any, timber: Any, center: Sequence[float], width: float, depth: float, rise: float,
              rng_: Any) -> None:
    """A pantile roof: half-round ``tiles`` rows over a ``frame`` underlay, rafter ends in
    ``timber`` showing under the eaves; same footprint rule as ``tiled_roof``."""
    for side in (-1, 1):
        _slope(batch, (tiles, frame), center, width, depth, rise, side, rng_, style="clay")
    cx, cy, cz = (float(v) for v in center)
    batch.box(timber, (cx, cy, cz + rise + 0.1), (width + 0.5, 0.26, 0.16))
    for i in range(max(2, int(width / 0.9)) + 1):
        x = cx - width / 2 + width * i / max(1, int(width / 0.9))
        for side in (-1, 1):
            batch.box(timber, (x, cy + side * (depth / 2 + 0.05), cz - 0.12), (0.12, 0.5, 0.14))


def fret_window(batch: Any, wood: Any, paper: Any, x: float, y: float, z: float, width: float, height: float, direction: float, *,
                pattern: int = 0) -> None:
    """A latticed window ``width`` x ``height`` whose sill centre is at (x, y, z), facing
    compass ``direction``: a frame and a fret in ``wood`` (``pattern`` 0 grid, 1 diagonal, 2
    nested) over a ``paper`` pane."""
    view = batch.transformed(Matrix.Translation((x, y, z)) @ _yaw(direction))
    f, d = 0.06, 0.08
    view.box(wood, (0.0, 0.0, f / 2), (width, d, f))
    view.box(wood, (0.0, 0.0, height - f / 2), (width, d, f))
    for sx in (-1, 1):
        view.box(wood, (sx * (width / 2 - f / 2), 0.0, height / 2), (f, d, height))
    bar = 0.025
    if pattern == 1:
        n = 4
        for k in range(1, n):
            t = k / n
            view.beam(wood, (-width / 2 + width * t, 0.0, f), (width / 2, 0.0, height - f * (1.0 - t)), bar / 2, sides=4)
            view.beam(wood, (-width / 2, 0.0, f + (height - 2 * f) * t), (width / 2 - width * t, 0.0, height - f), bar / 2, sides=4)
    elif pattern == 2:
        for k in range(1, 4):
            w, h = width * (1.0 - k * 0.22), height * (1.0 - k * 0.22)
            for sx in (-1, 1):
                view.box(wood, (sx * w / 2, 0.0, height / 2), (bar, bar, h))
            view.box(wood, (0.0, 0.0, height / 2 - h / 2), (w, bar, bar))
            view.box(wood, (0.0, 0.0, height / 2 + h / 2), (w, bar, bar))
    else:
        for k in range(1, 3):
            view.box(wood, (-width / 2 + width * k / 3, 0.0, height / 2), (bar, bar, height - 2 * f))
        for k in range(1, 4):
            view.box(wood, (0.0, 0.0, f + (height - 2 * f) * k / 4), (width - 2 * f, bar, bar))
    verts, faces = box_geometry((0.0, d * 0.3, height / 2), (width - 2 * f, 0.004, height - 2 * f))
    view.add(paper, verts, faces)


def timber_house(batch: Any, roofs: Any, x: float, y: float, width: float, depth: float, floors: int, mats: Any, rng_: Any,
                 index: int) -> float:
    """A half-timbered house ``width`` (along X) x ``depth`` with ``floors`` storeys (2.8 m
    each, upper ones jettied 0.2 m) on ground point (x, y, 0): walls, timber frame, a door and
    fret windows on the front (-Y), a ``tiled_roof``.  ``mats`` = {"wall", "timber", "door",
    "window"}, ``roofs`` = {"tile", "trim"}; ``index`` varies the door side and the fret.
    Returns the ridge height above the ground.  ~6-12 k triangles."""
    wall, timber, door, pane = mats["wall"], mats["timber"], mats["door"], mats["window"]
    storey = 2.8
    z0 = 0.0
    for fl in range(max(1, int(floors))):
        jetty = 0.2 * fl
        w, dd = width + 2 * jetty, depth + 2 * jetty
        zb = z0 + storey * fl
        batch.box(wall, (x, y, zb + storey / 2), (w, dd, storey))
        # sill beam and posts
        batch.box(timber, (x, y - dd / 2 - 0.04, zb + 0.08), (w + 0.1, 0.1, 0.16))
        batch.box(timber, (x, y - dd / 2 - 0.04, zb + storey - 0.08), (w + 0.1, 0.1, 0.16))
        n_posts = max(2, int(w / 1.4))
        for k in range(n_posts + 1):
            px = x - w / 2 + w * k / n_posts
            batch.box(timber, (px, y - dd / 2 - 0.04, zb + storey / 2), (0.14, 0.1, storey))
            for sy in (-1, 1):
                batch.box(timber, (x + sy * (w / 2 + 0.04), y - dd / 2 + dd * k / n_posts, zb + storey / 2), (0.1, 0.14, storey))
        if fl == 0:
            for k in range(n_posts):
                ax = x - w / 2 + w * k / n_posts
                bx = ax + w / n_posts
                batch.beam(timber, (ax, y - dd / 2 - 0.04, zb + 0.16), (bx, y - dd / 2 - 0.04, zb + storey * 0.45), 0.05, sides=4)
        # windows on the front, one per bay but the door bay on the ground floor
        door_bay = index % n_posts
        for k in range(n_posts):
            wx = x - w / 2 + w * (k + 0.5) / n_posts
            if fl == 0 and k == door_bay:
                batch.box(door, (wx, y - dd / 2 - 0.02, zb + 1.05), (0.95, 0.06, 2.1))
                continue
            fret_window(batch, timber, pane, wx, y - dd / 2 - 0.02, zb + 0.9, 0.9, 1.2, -90.0, pattern=(index + k) % 3)
        for sy in (-1, 1):
            fret_window(batch, timber, pane, x + sy * (w / 2 + 0.02), y + dd * 0.15, zb + 0.9, 0.8, 1.1, 0.0 if sy > 0 else 180.0, pattern=index % 3)
    top = z0 + storey * floors
    jetty = 0.2 * (floors - 1)
    rise = (depth + 2 * jetty) * 0.48
    tiled_roof(batch, roofs["tile"], roofs["trim"], (x, y, top), width + 2 * jetty + 0.4, depth + 2 * jetty + 0.4, rise, rng_)
    # gable infill under the roof at both ends
    for sx in (-1, 1):
        gx = x + sx * (width / 2 + jetty)
        verts = [(gx, y - (depth / 2 + jetty), top), (gx, y + (depth / 2 + jetty), top), (gx, y, top + rise)]
        batch.add(wall, verts, [(0, 1, 2) if sx > 0 else (0, 2, 1)])
    return top + rise + 0.15


def _footing(batch: Any, mat: Any, center: Sequence[float], size: Sequence[float], height_at: Any) -> None:
    """A foundation block of ``size`` (x, y, height) whose top is at ``center`` z: its bottom
    meets the LOWEST of the ground under its four corners (``height_at``) so no corner floats on
    a slope — the gate samples the corners; without ``height_at`` it is ``size[2]`` tall."""
    cx, cy, cz = (float(v) for v in center)
    sx, sy, h = (float(v) for v in size)
    corners = ((-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2))
    if height_at is None:
        bottoms = [cz - h] * 4
    else:   # a wedge: every corner 2 cm under its own ground, so a bank buries nothing deeper
        bottoms = [min(float(height_at(cx + dx, cy + dy)) - 0.02, cz - 0.05) for dx, dy in corners]
    verts = [(cx + dx, cy + dy, zb) for (dx, dy), zb in zip(corners, bottoms, strict=True)]
    verts += [(cx + dx, cy + dy, cz) for dx, dy in corners]
    batch.add(mat, verts, [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])


def stone_bridge(batch: Any, stone: Any, dark: Any, *, center: Sequence[float], span: float = 27.6, width: float = 4.7,
                 rise: float = 3.9, height_at: Any = None) -> None:
    """A hump-backed stone bridge along X over ``center`` (x, y, water / ground z): a barrel
    arch (intrados in ``dark``) rising ``rise`` m, spandrels, a cambered deck and parapets in
    ``stone``; the abutments' feet stand on the ``center`` z, or follow ``height_at`` under
    their corners when given.  ~2.6 k triangles."""
    cx, cy, cz = (float(v) for v in center)
    n = 24
    ha = span * 0.36          # the arch's half-span
    deck_h = rise + 0.9       # deck centre height above z
    verts: list[Vec3] = []
    faces: list[tuple[int, ...]] = []
    # arch barrel: intrados (dark) as a strip of quads, spandrel walls as the strip up to the deck
    for k in range(n + 1):
        a = math.pi * k / n
        xi, zi = cx - ha * math.cos(a), cz + rise * math.sin(a)
        for yy in (cy - width / 2, cy + width / 2):
            verts.append((xi, yy, zi))
    for k in range(n):
        a = 2 * k
        faces.append((a, a + 1, a + 3, a + 2))
    batch.add(dark, verts, faces, smooth=True)
    sv: list[Vec3] = []
    sf: list[tuple[int, ...]] = []
    for yy in (cy - width / 2, cy + width / 2):
        base = len(sv)
        for k in range(n + 1):
            a = math.pi * k / n
            xi = cx - ha * math.cos(a)
            sv.append((xi, yy, cz + rise * math.sin(a)))
            sv.append((xi, yy, cz + deck_h - 0.35 * (xi - cx) ** 2 / (span / 2) ** 2 * deck_h))
        for k in range(n):
            a = base + 2 * k
            sf.append((a, a + 2, a + 3, a + 1) if yy < cy else (a, a + 1, a + 3, a + 2))
    batch.add(stone, sv, sf)
    # deck (cambered), parapets and abutment blocks
    m = 16
    for k in range(m):
        t0, t1 = k / m, (k + 1) / m
        x0, x1 = cx - span / 2 + span * t0, cx - span / 2 + span * t1
        h0 = cz + deck_h - 0.35 * (x0 - cx) ** 2 / (span / 2) ** 2 * deck_h
        h1 = cz + deck_h - 0.35 * (x1 - cx) ** 2 / (span / 2) ** 2 * deck_h
        slab = [(x0, cy - width / 2, h0), (x1, cy - width / 2, h1), (x1, cy + width / 2, h1), (x0, cy + width / 2, h0)]
        batch.add(stone, slab, [(0, 1, 2, 3)])
        for sy in (-1, 1):
            yy = cy + sy * (width / 2 - 0.18)
            batch.add(stone, [(x0, yy - 0.18, h0), (x1, yy - 0.18, h1), (x1, yy + 0.18, h1), (x0, yy + 0.18, h0),
                              (x0, yy - 0.18, h0 + 1.0), (x1, yy - 0.18, h1 + 1.0), (x1, yy + 0.18, h1 + 1.0), (x0, yy + 0.18, h0 + 1.0)],
                      [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    for sx in (-1, 1):
        ax = cx + sx * (span / 2 - (span / 2 - ha) / 2)
        _footing(batch, stone, (ax, cy, cz + deck_h * 0.65), (span / 2 - ha, width + 0.3, deck_h * 0.65), height_at)


def arched_footbridge(batch: Any, wood: Any, stone: Any, *, start: Sequence[float], end: Sequence[float], rise: float = 1.5,
                      height_at: Any = None) -> None:
    """A wooden footbridge arching ``rise`` m from ``start`` to ``end`` (x, y, ground z): planks,
    rails on posts, stone abutments standing on the two ground points (their corners follow
    ``height_at`` when given — a bank is never level).  ~1.5 k triangles."""
    a, b = Vector(tuple(float(v) for v in start)), Vector(tuple(float(v) for v in end))
    d = b - a
    L = max(0.5, d.length)
    along = d.normalized()
    side = Vector((-along.y, along.x, 0.0)).normalized()
    n = max(4, int(L / 0.3))
    w = 1.6
    for k in range(n):
        t = (k + 0.5) / n
        p = a + d * t + Vector((0.0, 0.0, rise * math.sin(math.pi * t) + 0.08))
        tangent = (along + Vector((0.0, 0.0, rise * math.pi * math.cos(math.pi * t) / L))).normalized()
        m = Matrix.Translation(p) @ Matrix((side, tangent, side.cross(tangent))).transposed().to_4x4()
        batch.transformed(m).box(wood, (0.0, 0.0, 0.0), (w, L / n * 0.92, 0.05))
    for k in range(5):
        t = k / 4
        p = a + d * t + Vector((0.0, 0.0, rise * math.sin(math.pi * t) + 0.1))
        for sy in (-1, 1):
            foot = p + side * (sy * w / 2)
            batch.beam(wood, foot, foot + Vector((0.0, 0.0, 1.0)), 0.04, sides=6)
    for sy in (-1, 1):
        pts = [a + d * (k / 12) + side * (sy * w / 2) + Vector((0.0, 0.0, rise * math.sin(math.pi * k / 12) + 1.1)) for k in range(13)]
        for k in range(12):
            batch.beam(wood, pts[k], pts[k + 1], 0.035, sides=6)
    for p in (a, b):
        _footing(batch, stone, (p.x, p.y, p.z + 0.3), (w + 0.4, 1.2, 0.3), height_at)


def tower(batch: Any, mat: Any, center: Sequence[float], radius: float, low: float, high: float, windows: Any, *, count: int = 8) -> None:
    """A round tower about ``center`` (x, y) from z = ``low`` to ``high``: a tapering drum with
    a battered base, ``count`` window insets in ``windows`` on two levels, and merlons on top."""
    cx, cy = float(center[0]), float(center[1])
    h = high - low
    batch.lathe(mat, (cx, cy), [(radius * 1.15, low), (radius * 1.05, low + h * 0.12), (radius, low + h * 0.9), (radius * 1.08, high - 0.4),
                                (radius * 1.08, high), (0.0, high)], sides=24)
    for level in (0.42, 0.72):
        for k in range(max(1, int(count))):
            a = math.tau * (k + 0.5 * (level > 0.5)) / count
            m = Matrix.Translation((cx + math.cos(a) * radius * 0.98, cy + math.sin(a) * radius * 0.98, low + h * level)) @ Matrix.Rotation(a + math.pi / 2, 4, "Z")
            batch.transformed(m).box(windows, (0.0, 0.0, 0.0), (0.35, 0.12, 0.9))
    for k in range(max(6, int(count) * 2)):
        a = math.tau * k / max(6, int(count) * 2)
        m = Matrix.Translation((cx + math.cos(a) * radius * 1.0, cy + math.sin(a) * radius * 1.0, high + 0.3)) @ Matrix.Rotation(a, 4, "Z")
        batch.transformed(m).box(mat, (0.0, 0.0, 0.0), (0.3, 0.45, 0.6))


def paving(batch: Any, mats: Sequence[Any], *, center: Sequence[float], inner_r: float, rings: int, seed: int) -> None:
    """Fan-set paving: ``rings`` concentric rings of stones 0.4 m deep from ``inner_r`` outward
    around ``center`` (x, y, ground z), 2 cm thick, cycling ``mats``; ``seed`` varies the
    joints.  A ring of radius r holds ~2πr / 0.35 stones."""
    cx, cy, cz = (float(v) for v in center)
    k = 0
    for ring in range(max(1, int(rings))):
        r0 = inner_r + 0.4 * ring
        rm = r0 + 0.2
        n = max(6, int(math.tau * rm / 0.35))
        for i in range(n):
            a = math.tau * (i + 0.5 * (ring % 2)) / n
            j = 0.012 * hash01(k, seed)
            w = math.tau * rm / n - 0.025 - j
            batch.box(mats[k % len(mats)], (cx + rm * math.cos(a), cy + rm * math.sin(a), cz + 0.01), (0.38 - j, w, 0.02), rotation=a)
            k += 1


def paving_grid(batch: Any, mats: Sequence[Any], center: Sequence[float], size: float | Sequence[float], stone: float | Sequence[float],
                joint: float, rng_: Any) -> None:
    """A rectangular grid of flagstones ``stone`` (m, or (x, y)) with ``joint`` m gaps over
    ``size`` m around ``center`` (x, y, ground z), 2 cm thick, cycling ``mats`` with a jitter."""
    sx, sy = (float(size), float(size)) if isinstance(size, (int, float)) else (float(size[0]), float(size[1]))
    tx, ty = (float(stone), float(stone)) if isinstance(stone, (int, float)) else (float(stone[0]), float(stone[1]))
    cx, cy, cz = (float(v) for v in center)
    nx, ny = max(1, int(sx / (tx + joint))), max(1, int(sy / (ty + joint)))
    k = 0
    for i in range(nx):
        for j in range(ny):
            x = cx - sx / 2 + (tx + joint) * (i + 0.5)
            y = cy - sy / 2 + (ty + joint) * (j + 0.5)
            batch.box(mats[rng_.randrange(len(mats))], (x, y, cz + 0.01), (tx - rng_.uniform(0, joint * 0.5), ty - rng_.uniform(0, joint * 0.5), 0.02))
            k += 1


def _slab(name: str, collection: Any, mat: Any, loc: Sequence[float], size: Sequence[float], parent: Any) -> Any:
    v, f = box_geometry(loc, size)
    ob = mesh_object(name, v, f, mat, collection)
    ob.parent = parent
    return ob


def room_shell(collection: Any, mat: Any, *, center: Sequence[float], extents: Sequence[float], thickness: float = 0.2,
               openings: Sequence[Sequence[Any]] = (), glazed: bool = False) -> list[Any]:
    """An interior's floor, four walls and ceiling OUTSIDE the box ``center`` / ``extents``
    (inner faces = the box faces), under a root Empty ``RoomShell`` with the lid named
    ``Ceiling`` (the overview rig lifts it).  ``openings`` = ``(wall, offset, width, sill, head)``
    rows — ``wall`` front/back/left/right (front = -Y), ``offset`` along the wall from its
    centre, ``sill``/``head`` heights above the floor — cut as panels, no booleans.  ``glazed``
    makes a glass house (a conservatory, a greenhouse, a glass atrium): every wall panel and the
    lid become a ``materials.glass`` pane that casts no shadow on an iron-frame grid of ``mat``
    bars (the sun comes through, the frame's grid shadows the floor); the floor stays an opaque
    slab of ``mat`` and the names stay, a pane being its panel's name + ``Glass``.  Returns
    ``[root, floor, ceiling, walls…]`` (glazed: each panel's frame then its pane); for ``src/env.py``."""
    cx, cy, cz = (float(v) for v in center)
    ex, ey, ez = (float(v) for v in extents)
    t = float(thickness)
    z0, z1 = cz - ez / 2, cz + ez / 2
    pane = (bpy.data.materials.get("RoomShellGlass") or glass("RoomShellGlass", color="E6F2F0")) if glazed else None
    root = bpy.data.objects.new("RoomShell", None)
    root.empty_display_size = 0.5
    collection.objects.link(root)
    out = [root, _slab("RoomShellFloor", collection, mat, (cx, cy, z0 - t / 2), (ex + 2 * t, ey + 2 * t, t), root)]
    out += _slabs("Ceiling", collection, mat, pane, (cx, cy, z1 + t / 2), (ex + 2 * t, ey + 2 * t, t), 2, root)
    # each wall: (name, axis along the wall, centre point, length)
    walls = {"front": ("RoomShellWallFront", "x", (cx, cy - ey / 2 - t / 2), ex + 2 * t),
             "back": ("RoomShellWallBack", "x", (cx, cy + ey / 2 + t / 2), ex + 2 * t),
             "left": ("RoomShellWallLeft", "y", (cx - ex / 2 - t / 2, cy), ey),
             "right": ("RoomShellWallRight", "y", (cx + ex / 2 + t / 2, cy), ey)}
    cuts: dict[str, list[tuple[float, float, float, float]]] = {w: [] for w in _WALLS}
    for row in openings:
        wall, offset, width, sill, head = str(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])
        # clamped into the wall: a head above the lid would invert the lintel into a slab ABOVE it (gate ERROR)
        sill, head = max(0.0, min(sill, ez)), max(0.0, min(head, ez))
        if wall in cuts and width > 0 and head > sill:
            cuts[wall].append((offset, width, sill, head))
    for wall in _WALLS:
        name, axis, (wx, wy), length = walls[wall]
        spec = (collection, mat, root, name, axis, wx, wy, t, pane)
        segs = sorted(cuts[wall], key=lambda c: c[0])
        edge = -length / 2
        k = 0
        for offset, width, sill, head in segs:
            a, b = max(edge, offset - width / 2), min(length / 2, offset + width / 2)
            _panel(out, spec, edge, a, z0, z1, f"_{k}")
            if sill > 0:
                _panel(out, spec, a, b, z0, z0 + sill, f"_{k}Sill")
            if head < ez:
                _panel(out, spec, a, b, z0 + head, z1, f"_{k}Lintel")
            edge = b
            k += 1
        _panel(out, spec, edge, length / 2, z0, z1, "" if k == 0 else f"_{k}")
    return out


def _panel(out: list[Any], spec: tuple[Any, ...], a: float, b: float, zb: float, zt: float, tag: str) -> None:
    """One wall panel between ``a`` and ``b`` along the wall, ``zb``..``zt`` tall (skipped when
    degenerate); ``spec`` = (collection, material, root, name, axis, wx, wy, thickness, pane)."""
    collection, mat, root, name, axis, wx, wy, t, pane = spec
    if b - a <= 1e-3 or zt - zb <= 1e-3:
        return
    mid = (a + b) / 2
    loc = (wx + mid, wy, (zb + zt) / 2) if axis == "x" else (wx, wy + mid, (zb + zt) / 2)
    size = (b - a, t, zt - zb) if axis == "x" else (t, b - a, zt - zb)
    out.extend(_slabs(f"{name}{tag}", collection, mat, pane, loc, size, 1 if axis == "x" else 0, root))


def _slabs(name: str, collection: Any, mat: Any, pane: Any, loc: Sequence[float], size: Sequence[float], thin: int,
           parent: Any) -> list[Any]:
    """One opaque slab — or, with a ``pane`` material, its glazed twin: a ``_PANE`` glass sheet in
    the slab's mid-plane that casts no shadow (the sun comes through), framed by ``mat`` bars on a
    WORLD lattice (``_BAY`` apart: the grid runs on across neighbouring panels and around an
    opening) plus a bar on every edge.  ``thin`` = the thickness axis (0 x, 1 y, 2 z), known from
    the face — never the smallest size: a sill can be narrower than the wall is thick."""
    if pane is None:
        return [_slab(name, collection, mat, loc, size, parent)]
    c0, s0 = [float(v) for v in loc], [float(v) for v in size]
    verts: list[Vec3] = []
    faces: list[tuple[int, ...]] = []
    depth = max(0.04, min(s0[thin], 0.15))
    i, j = [k for k in range(3) if k != thin]
    for along, across in ((i, j), (j, i)):
        lo, hi = c0[across] - s0[across] / 2, c0[across] + s0[across] / 2
        lines, v = [lo, hi], math.ceil((lo + 0.3) / _BAY[across]) * _BAY[across]
        while v < hi - 0.3:
            lines.append(v)
            v += _BAY[across]
        for at in lines:
            c, s = list(c0), [0.0, 0.0, 0.0]
            c[across] = at
            s[thin], s[along], s[across] = depth, s0[along], _BAR
            bv, bf = box_geometry(c, s)
            base = len(verts)
            verts += bv
            faces += [tuple(base + n for n in f) for f in bf]
    frame = mesh_object(name, verts, faces, mat, collection)
    frame.parent = parent
    sheet_ob = _slab(f"{name}Glass", collection, pane, c0, [_PANE if k == thin else s0[k] for k in range(3)], parent)
    sheet_ob.visible_shadow = False
    return [frame, sheet_ob]


def lantern(batch: Any, silk: Any, wood: Any, center: Sequence[float], *, radius: float = 0.35, length: float | None = None) -> None:
    """A paper lantern centred at ``center`` (it hangs: tag the finished root ``free`` or put
    it on a post): a ``silk`` body (make it ``materials.emissive``) with ``wood`` caps and a
    hanging loop; ``length`` defaults to 2.4 x ``radius``."""
    cx, cy, cz = (float(v) for v in center)
    L = float(length) if length is not None else radius * 2.4
    batch.lathe(silk, (cx, cy), [(0.0, cz - L / 2), (radius * 0.55, cz - L / 2 + 0.03), (radius, cz - L * 0.22), (radius, cz + L * 0.22),
                                 (radius * 0.55, cz + L / 2 - 0.03), (0.0, cz + L / 2)], sides=24)
    for zz in (cz - L / 2 - 0.02, cz + L / 2 + 0.02):
        batch.lathe(wood, (cx, cy), [(0.0, zz - 0.02), (radius * 0.4, zz - 0.02), (radius * 0.4, zz + 0.02), (0.0, zz + 0.02)], sides=12)
    batch.ring(wood, (cx, cy, cz + L / 2 + 0.1), (0.0, 1.0, 0.0), 0.06, 0.008, segments=16)


def text_mesh(name: str, collection: Any, body: str, loc: Sequence[float], size: float, mat: Any, *,
              rotation: Sequence[float] = (math.pi / 2, 0.0, 0.0), align: str = "CENTER", extrude: float = 0.005) -> Any:
    """Readable lettering: a text object saying ``body`` at ``loc``, ``size`` m tall letters,
    standing up and facing -Y with the default ``rotation``; ``extrude`` gives it thickness.
    Measured by the census like any mesh; a sign a camera can read."""
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = str(body)
    cu.size = float(size)
    cu.extrude = float(extrude)
    cu.align_x = align
    if mat is not None:
        cu.materials.append(mat)
    ob = bpy.data.objects.new(name, cu)
    ob.location = tuple(float(v) for v in loc)
    ob.rotation_mode = "XYZ"
    ob.rotation_euler = tuple(float(v) for v in rotation)
    collection.objects.link(ob)
    return ob
