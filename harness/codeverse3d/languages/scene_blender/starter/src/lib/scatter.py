"""lib/scatter.py — placing copies: collection instances and geometry-nodes clouds.

Two kinds of copy, and the gate treats them differently.  ``instance`` / ``scatter`` make a
collection-instance Empty per copy, seated on ``height_at``: ONE placement row each (named,
measured for floating / sunk / interpenetration — what a bench, a cart or a tree deserves).
``gn_scatter`` makes an instance CLOUD on a surface (blades, pebbles, tufts: hundreds to tens
of thousands): one object, counted as instances in the census and EXEMPT from per-asset
placement because its copies ride the surface by construction.  Blender 5.2 API: node inputs
are set with ``getattr(mod.properties.inputs, identifier).value``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import bpy

from lib.place import seat_instance, unique_name, world_matrix

HeightFn = Callable[[float, float], float]


def instance(collection: Any, source: Any, name: str, x: float, y: float, *, height_at: HeightFn, rot_z: float = 0.0,
             scale: float = 1.0, sink: float = 0.0) -> Any:
    """One placed copy of the Collection ``source`` (``ctx.assets["<snake>"]``): an instance
    Empty named ``name`` at (x, y), yawed ``rot_z`` radians, seated on ``height_at`` (``sink``
    m deeper).  Returns the Empty — the placement row."""
    ob = bpy.data.objects.new(name, None)
    ob.instance_type = "COLLECTION"
    ob.instance_collection = source
    ob.empty_display_size = 0.25
    ob.location = (float(x), float(y), 0.0)
    ob.rotation_mode = "XYZ"
    ob.rotation_euler = (0.0, 0.0, float(rot_z))
    ob.scale = (float(scale), float(scale), float(scale))
    collection.objects.link(ob)
    seat_instance(ob, source, height_at, sink=sink)
    return ob


def scatter(collection: Any, source: Any, prefix: str, points: Sequence[Sequence[float]], *, height_at: HeightFn, rng: Any,
            rot: str | float = "random", scale: Sequence[float] = (1.0, 1.0)) -> list[Any]:
    """``instance`` at every (x, y) of ``points``, named ``<prefix>_<i>``; ``rot`` ``"random"``
    or a fixed yaw, ``scale`` a (min, max) range drawn per copy."""
    out = []
    for i, p in enumerate(points):
        yaw = rng.uniform(0.0, math.tau) if rot == "random" else float(rot)
        s = rng.uniform(float(scale[0]), float(scale[1])) if scale[0] != scale[1] else float(scale[0])
        out.append(instance(collection, source, unique_name(prefix, i), float(p[0]), float(p[1]), height_at=height_at,
                            rot_z=yaw, scale=s))
    return out


def poisson(rng: Any, *, area: Sequence[float], min_dist: float, max_count: int,
            mask: Callable[[float, float], float] | None = None) -> list[tuple[float, float]]:
    """Up to ``max_count`` points in ``area`` = (x0, y0, x1, y1) at least ``min_dist`` apart
    (dart throwing); ``mask(x, y)`` 0..1 is the chance a candidate is kept (a shoreline, a
    clearing).  Deterministic for a seeded ``rng``."""
    x0, y0, x1, y1 = (float(v) for v in area)
    cell = max(1e-3, float(min_dist)) / math.sqrt(2.0)
    grid: dict[tuple[int, int], tuple[float, float]] = {}
    out: list[tuple[float, float]] = []
    tries = max(1, int(max_count)) * 30
    for _ in range(tries):
        if len(out) >= max_count:
            break
        x, y = rng.uniform(x0, x1), rng.uniform(y0, y1)
        if mask is not None and rng.uniform(0.0, 1.0) > float(mask(x, y)):
            continue
        gx, gy = int(math.floor((x - x0) / cell)), int(math.floor((y - y0) / cell))
        ok = True
        for i in range(gx - 2, gx + 3):
            for j in range(gy - 2, gy + 3):
                q = grid.get((i, j))
                if q is not None and math.hypot(q[0] - x, q[1] - y) < min_dist:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            grid[(gx, gy)] = (x, y)
            out.append((x, y))
    return out


def along_path(points: Sequence[Sequence[float]], n: int, *, jitter: float = 0.0, rng: Any = None) -> list[tuple[float, ...]]:
    """``n`` points evenly spaced by arc length along the polyline ``points`` (2-D or 3-D,
    returned the same way), each pushed up to ``jitter`` m sideways when ``rng`` is given."""
    pts = [tuple(float(v) for v in p) for p in points]
    if len(pts) == 1 or n <= 0:
        return pts[:1] * max(0, n)
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg)
    out: list[tuple[float, ...]] = []
    for k in range(n):
        d = total * (k / (n - 1) if n > 1 else 0.5)
        i = 0
        while i < len(seg) - 1 and d > seg[i]:
            d -= seg[i]
            i += 1
        t = d / seg[i] if seg[i] > 0 else 0.0
        a, b = pts[i], pts[i + 1]
        p = [a[j] + (b[j] - a[j]) * t for j in range(len(a))]
        if jitter > 0 and rng is not None:
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy) or 1.0
            s = rng.uniform(-jitter, jitter)
            p[0] += -dy / L * s
            p[1] += dx / L * s
        out.append(tuple(p))
    return out


def grid(center: Sequence[float], size: float | Sequence[float], spacing: float, rng: Any, *, jitter: float = 0.3) -> list[tuple[float, float]]:
    """A grid of (x, y) points ``spacing`` m apart over ``size`` m around ``center``, each moved
    up to ``jitter`` x spacing (an orchard, a car park, a crowd that is not a lattice)."""
    sx, sy = (float(size), float(size)) if isinstance(size, (int, float)) else (float(size[0]), float(size[1]))
    cx, cy = float(center[0]), float(center[1])
    nx, ny = max(1, int(sx / spacing)), max(1, int(sy / spacing))
    out = []
    for i in range(nx):
        for j in range(ny):
            x = cx - sx / 2 + spacing * (i + 0.5) + rng.uniform(-jitter, jitter) * spacing
            y = cy - sy / 2 + spacing * (j + 0.5) + rng.uniform(-jitter, jitter) * spacing
            out.append((x, y))
    return out


def gn_scatter(name: str, collection: Any, surface: Any, instance_obj: Any, *, density: float, seed: int = 0,
               align_normal: bool = True, scale_range: Sequence[float] = (0.8, 1.2)) -> Any:
    """An instance cloud of ``instance_obj`` on the mesh of ``surface`` (a hidden patch that
    follows the terrain, or any mesh): ``density`` copies per m², random yaw, ``scale_range``
    per copy, aligned to the surface normal when ``align_normal``.  Returns the host object
    (named ``name``; it renders ONLY the instances — the surface stays the distribution
    domain).  ``instance_obj`` may be hidden: its geometry is copied, not its visibility."""
    ng = bpy.data.node_groups.new(f"{name}Scatter", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    dens = ng.interface.new_socket("Density", in_out="INPUT", socket_type="NodeSocketFloat")
    sd = ng.interface.new_socket("Seed", in_out="INPUT", socket_type="NodeSocketInt")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n, links = ng.nodes, ng.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    dist = n.new("GeometryNodeDistributePointsOnFaces")
    dist.distribute_method = "RANDOM"
    links.new(gi.outputs["Geometry"], dist.inputs["Mesh"])
    links.new(gi.outputs["Density"], dist.inputs["Density"])
    links.new(gi.outputs["Seed"], dist.inputs["Seed"])
    info = n.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = instance_obj
    inst = n.new("GeometryNodeInstanceOnPoints")
    links.new(dist.outputs["Points"], inst.inputs["Points"])
    links.new(info.outputs["Geometry"], inst.inputs["Instance"])
    yaw = n.new("FunctionNodeRandomValue")
    yaw.data_type = "FLOAT_VECTOR"
    yaw.inputs["Min"].default_value = (0.0, 0.0, 0.0)
    yaw.inputs["Max"].default_value = (0.0, 0.0, math.tau)
    links.new(gi.outputs["Seed"], yaw.inputs["Seed"])
    euler = n.new("FunctionNodeEulerToRotation")
    links.new(yaw.outputs["Value"], euler.inputs["Euler"])
    if align_normal:
        rot = n.new("FunctionNodeRotateRotation")
        links.new(dist.outputs["Rotation"], rot.inputs["Rotation"])
        links.new(euler.outputs["Rotation"], rot.inputs["Rotate By"])
        links.new(rot.outputs["Rotation"], inst.inputs["Rotation"])
    else:
        links.new(euler.outputs["Rotation"], inst.inputs["Rotation"])
    size = n.new("FunctionNodeRandomValue")
    size.data_type = "FLOAT"
    size.inputs["Min"].default_value = float(scale_range[0])
    size.inputs["Max"].default_value = float(scale_range[1])
    links.new(gi.outputs["Seed"], size.inputs["Seed"])
    links.new(size.outputs["Value"], inst.inputs["Scale"])
    links.new(inst.outputs["Instances"], go.inputs["Geometry"])
    host = bpy.data.objects.new(name, surface.data)
    host.matrix_world = world_matrix(surface)
    collection.objects.link(host)
    mod = host.modifiers.new("Scatter", "NODES")
    mod.node_group = ng
    getattr(mod.properties.inputs, dens.identifier).value = float(density)
    getattr(mod.properties.inputs, sd.identifier).value = int(seed)
    return host
