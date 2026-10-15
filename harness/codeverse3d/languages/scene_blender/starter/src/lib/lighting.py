"""lib/lighting.py — lamps as data objects (never ``bpy.ops.object.light_add``).

Units are Blender's: a Sun's ``energy`` is irradiance in W/m² (3-5 reads as daylight under AgX
at exposure 0, 0.3-1 as dusk), point / spot / area ``energy`` is Watts (a 45 W practical lights a
porch, 500-2000 W a hall).  Compass angles: 0° = +X, 90° = +Y, like the skeleton's
``SUN_AZIMUTH_DEG``.  The harness owns exposure (0) and the view transform (AgX): light the scene,
never the camera.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import bpy
from mathutils import Vector

from lib.materials import rgba


def _rgb(color: str | Sequence[float]) -> tuple[float, float, float]:
    c = rgba(color) if isinstance(color, str) else tuple(float(v) for v in color)
    return (c[0], c[1], c[2])


def aim(obj: Any, target: Sequence[float]) -> None:
    """Point ``obj``'s -Z axis (a lamp's beam, a camera's view) at ``target``."""
    d = Vector(target) - Vector(obj.location)
    if d.length < 1e-6:
        return
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def sun(collection: Any, *, azimuth_deg: float, elevation_deg: float, color: str | Sequence[float] = "FFCF9C",
        energy: float = 3.0, angle_deg: float = 2.0, name: str = "Sun") -> tuple[Any, Vector]:
    """A Sun lamp shining FROM compass ``azimuth_deg`` at ``elevation_deg``; returns
    ``(lamp, towards_sun)`` — the unit vector zones can read as ``ctx.env["sun_direction"]``.
    ``angle_deg`` is the disc's angular size (2° = soft natural shadow edges)."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    towards_sun = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    lamp = bpy.data.lights.new(name, "SUN")
    lamp.energy = float(energy)
    lamp.color = _rgb(color)
    lamp.angle = math.radians(angle_deg)
    ob = bpy.data.objects.new(name, lamp)
    ob.rotation_mode = "XYZ"
    ob.rotation_euler = (-towards_sun).to_track_quat("-Z", "Y").to_euler()
    collection.objects.link(ob)
    return ob, towards_sun


def area_light(collection: Any, name: str, loc: Sequence[float], color: str | Sequence[float], energy: float, *,
               size: float = 5.0, target: Sequence[float] | None = None, shape: str = "DISK") -> Any:
    """An area lamp of ``size`` m (``shape`` SQUARE / RECTANGLE / DISK / ELLIPSE) at ``loc``,
    aimed at ``target`` (straight down when None): a ceiling panel, a sky fill, a window."""
    lamp = bpy.data.lights.new(name, "AREA")
    lamp.shape = shape
    lamp.size = float(size)
    if shape in ("RECTANGLE", "ELLIPSE"):
        lamp.size_y = float(size) * 0.5
    lamp.energy = float(energy)
    lamp.color = _rgb(color)
    ob = bpy.data.objects.new(name, lamp)
    ob.location = tuple(float(v) for v in loc)
    collection.objects.link(ob)
    if target is not None:
        aim(ob, target)
    return ob


def point_light(collection: Any, name: str, loc: Sequence[float], color: str | Sequence[float], energy: float, *,
                radius: float = 0.05) -> Any:
    """A point lamp; ``radius`` (m) softens its shadows (a bulb 0.05, a lantern 0.3)."""
    lamp = bpy.data.lights.new(name, "POINT")
    lamp.energy = float(energy)
    lamp.color = _rgb(color)
    lamp.shadow_soft_size = float(radius)
    ob = bpy.data.objects.new(name, lamp)
    ob.location = tuple(float(v) for v in loc)
    collection.objects.link(ob)
    return ob


def spot(collection: Any, name: str, loc: Sequence[float], color: str | Sequence[float], energy: float, *,
         size_deg: float = 45.0, blend: float = 0.3, target: Sequence[float] | None = None) -> Any:
    """A spot lamp with a ``size_deg`` cone and ``blend`` soft edge, aimed at ``target``."""
    lamp = bpy.data.lights.new(name, "SPOT")
    lamp.energy = float(energy)
    lamp.color = _rgb(color)
    lamp.spot_size = math.radians(size_deg)
    lamp.spot_blend = float(blend)
    lamp.shadow_soft_size = 0.1
    ob = bpy.data.objects.new(name, lamp)
    ob.location = tuple(float(v) for v in loc)
    collection.objects.link(ob)
    if target is not None:
        aim(ob, target)
    return ob


def practical(collection: Any, name: str, loc: Sequence[float], color: str | Sequence[float] = "FFB270", energy: float = 300.0, *,
               size: float = 0.42, target: Sequence[float] | None = None) -> Any:
    """The light a visible fixture throws (a lantern, a window, a brazier): a warm disc area lamp
    of ``size`` m — pair it with an ``emissive`` skin on the fixture so the source reads.
    ``energy`` is what the FRAME needs, not what the fixture looks like: measured on Blender 5.2.2
    (the harness's Cycles/AgX settings, c3d-bpy-scene-lighting), two 45 W practicals leave a night
    frame at mean luminance 0.05 (a dark_frame ERROR); ~300-500 W each at 2.5-3 m lights it."""
    return area_light(collection, name, loc, color, energy, size=size, target=target, shape="DISK")
