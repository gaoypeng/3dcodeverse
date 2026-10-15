"""lib/motion.py — animation as DATA: keyframes on ``ctx.frame(t)`` and simple drivers.

The harness samples frame ``ctx.frame(t)`` for every ``t`` in ``ctx.times`` (0 and 1.5 s at 30
fps = frames 1 and 46) and reads the result off the saved .blend: nothing here runs Python at
render time.  A driver expression must be in Blender's simple-expression subset (``frame``,
``sin`` / ``cos`` / ``min`` / ``max`` / ``clamp``, + - * /) — a Python driver evaluates to 0
under ``--factory-startup`` and fails the build census.  Actions are slotted in 5.2: the
f-curves live in ``action.layers[0].strips[0].channelbag(slot)``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

_AXES = {"X": 0, "Y": 1, "Z": 2}


def _frames(ctx: Any, t0: float | None = None, t1: float | None = None) -> tuple[int, int]:
    times = tuple(ctx.times) if ctx is not None else (0.0, 1.5)
    a = float(times[0] if t0 is None else t0)
    b = float(times[-1] if t1 is None else t1)
    return ctx.frame(a), ctx.frame(b)


def fcurves(id_data: Any) -> list[Any]:
    """Every f-curve animating ``id_data`` (an object, a node tree, a shape key block)."""
    ad = getattr(id_data, "animation_data", None)
    act = ad.action if ad is not None else None
    if act is None:
        return []
    out: list[Any] = []
    for layer in act.layers:
        for strip in layer.strips:
            bag = strip.channelbag(ad.action_slot)
            if bag is not None:
                out.extend(bag.fcurves)
    return out


def set_interpolation(id_data: Any, interpolation: str = "LINEAR") -> None:
    """``LINEAR`` / ``BEZIER`` / ``CONSTANT`` on every keyframe of ``id_data``."""
    for fc in fcurves(id_data):
        for kp in fc.keyframe_points:
            kp.interpolation = interpolation


def keyframe_path(obj: Any, points: Sequence[Sequence[float]], ctx: Any, *, t0: float = 0.0, t1: float | None = None,
                  interpolation: str = "LINEAR") -> None:
    """Move ``obj`` through ``points`` (world positions of its origin) evenly between ``t0`` and
    ``t1`` (default: the last of ``ctx.times``)."""
    f0, f1 = _frames(ctx, t0, t1)
    n = len(points)
    for i, p in enumerate(points):
        frame = f0 if n == 1 else round(f0 + (f1 - f0) * i / (n - 1))
        obj.location = (float(p[0]), float(p[1]), float(p[2]))
        obj.keyframe_insert("location", frame=frame)
    obj.location = (float(points[0][0]), float(points[0][1]), float(points[0][2]))
    set_interpolation(obj, interpolation)


def drift(obj: Any, delta: Sequence[float], ctx: Any) -> None:
    """Translate ``obj`` by ``delta`` (m) over ``ctx.times``: a drifting boat, a rising balloon,
    a falling leaf — linear."""
    start = (float(obj.location[0]), float(obj.location[1]), float(obj.location[2]))
    end = (start[0] + float(delta[0]), start[1] + float(delta[1]), start[2] + float(delta[2]))
    keyframe_path(obj, [start, end], ctx)


def spin(obj: Any, axis: str = "Z", turns_per_s: float = 0.25, ctx: Any = None) -> None:
    """Rotate ``obj`` about its ``axis`` forever: a simple driver ``frame * k`` (a windmill, a
    waterwheel, a weather vane).  Positive = counter-clockwise seen from +axis."""
    fps = float(ctx.fps) if ctx is not None else 30.0
    k = math.tau * float(turns_per_s) / fps
    obj.rotation_mode = "XYZ"
    fc = obj.driver_add("rotation_euler", _AXES[axis.upper()])
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = f"frame*{k:.6f}"


def sway(obj: Any, axis: str, amplitude: float, period_s: float, ctx: Any) -> None:
    """Rock ``obj`` ± ``amplitude`` radians about ``axis`` with period ``period_s``: a simple
    driver ``sin(frame * w) * a`` around the current rotation (a hanging lantern, a mast)."""
    fps = float(ctx.fps) if ctx is not None else 30.0
    w = math.tau / (max(0.05, float(period_s)) * fps)
    i = _AXES[axis.upper()]
    obj.rotation_mode = "XYZ"
    base = float(obj.rotation_euler[i])
    fc = obj.driver_add("rotation_euler", i)
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = f"{base:.6f}+sin(frame*{w:.6f})*{float(amplitude):.6f}"


def scroll(mapping_node: Any, velocity: Sequence[float], ctx: Any) -> None:
    """Slide a Mapping node's Location by ``velocity`` (texture units per second) over
    ``ctx.times``: moving water, drifting clouds, a flowing aurora — keyframes on the node
    tree, linear."""
    f0, f1 = _frames(ctx)
    t0, t1 = float(ctx.times[0]), float(ctx.times[-1])
    sock = mapping_node.inputs["Location"]
    start = tuple(float(v) for v in sock.default_value)
    sock.keyframe_insert("default_value", frame=f0)
    sock.default_value = tuple(start[k] + float(velocity[k]) * (t1 - t0) for k in range(3))
    sock.keyframe_insert("default_value", frame=f1)
    sock.default_value = start
    set_interpolation(mapping_node.id_data, "LINEAR")


def shape_key_sweep(obj: Any, key_name: str, ctx: Any, *, from_value: float = 0.0, to_value: float = 1.0) -> None:
    """Drive shape key ``key_name`` of ``obj`` from ``from_value`` to ``to_value`` over
    ``ctx.times`` (a sail filling, a flower opening) — linear keyframes on the key block."""
    f0, f1 = _frames(ctx)
    keys = obj.data.shape_keys
    key = keys.key_blocks[key_name]
    key.value = float(from_value)
    key.keyframe_insert("value", frame=f0)
    key.value = float(to_value)
    key.keyframe_insert("value", frame=f1)
    key.value = float(from_value)
    set_interpolation(keys, "LINEAR")
