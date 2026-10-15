"""lib/figure.py — people as batch geometry: a figure, a dressed visitor, a crowd.

A figure is 1.72 x ``scale`` m tall and stands on ``center`` (x, y, ground z) facing -Y, turned
by ``heading`` (radians about Z; ``place.heading(a, b)`` gives the value that faces ``b``).
``pose`` 0 stands, 1 is a full walking stride (legs and arms swung).  About 560 triangles; a
crowd of forty is one object.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from mathutils import Matrix

_H = 1.72


def figure(batch: Any, cloth: Any, skin: Any, center: Sequence[float], *, scale: float = 1.0, heading: float = 0.0,
           pose: float = 0.0) -> None:
    """One person in ``cloth`` with ``skin`` hands and head, standing on ``center``."""
    _person(batch, cloth, cloth, skin, center, scale=scale, heading=heading, pose=pose)


def visitor(batch: Any, cloth: Any, mats: Sequence[Any], center: Sequence[float], *, scale: float = 1.0, heading: float = 0.0,
            pose: float = 0.0, variant: int = 0) -> None:
    """A dressed figure: ``cloth`` for the coat, ``mats[variant]`` for the legs (a list of
    trouser / skirt materials, cycled), ``mats[-1]`` for the skin when it holds two or more
    — hand ``materials.tint`` copies to vary a crowd."""
    pool = list(mats) or [cloth]
    legs = pool[int(variant) % len(pool)]
    skin = pool[-1] if len(pool) > 1 else cloth
    _person(batch, cloth, legs, skin, center, scale=scale, heading=heading, pose=pose)


def crowd(batch: Any, mats: Sequence[Any], points: Sequence[Sequence[float]], rng_: Any, **kw: Any) -> None:
    """A ``visitor`` at every (x, y, ground z) of ``points`` with a random heading, stride and
    coat drawn from ``mats`` (the last is the skin); ``kw`` = ``scale``."""
    pool = list(mats)
    coats = pool[:-1] if len(pool) > 1 else pool
    for i, p in enumerate(points):
        visitor(batch, coats[rng_.randrange(len(coats))], pool, p, heading=rng_.uniform(0.0, math.tau),
                pose=rng_.uniform(0.0, 1.0), variant=rng_.randrange(len(pool)) if pool else i, **kw)


def _person(batch: Any, coat: Any, legs: Any, skin: Any, center: Sequence[float], *, scale: float, heading: float, pose: float) -> None:
    H = _H * float(scale)
    cx, cy, cz = (float(v) for v in center)
    view = batch.transformed(Matrix.Translation((cx, cy, cz)) @ Matrix.Rotation(float(heading), 4, "Z"))
    swing = 0.45 * max(0.0, min(1.0, float(pose)))
    hip_z, knee_z, ankle_z = 0.5 * H, 0.27 * H, 0.04 * H
    for side, s in ((-1, 1.0), (1, -1.0)):
        hx = side * 0.085 * H
        fwd = -math.sin(swing * s) * (hip_z - ankle_z)       # a stride goes along -Y (the front)
        knee = (hx, fwd * 0.55, knee_z + 0.02 * H * abs(math.sin(swing)))
        ankle = (hx, fwd, ankle_z)
        view.beam(legs, (hx, 0.0, hip_z), knee, 0.065 * H, end=0.05 * H, sides=8)
        view.beam(legs, knee, ankle, 0.05 * H, end=0.04 * H, sides=8)
        view.box(skin, (hx, fwd - 0.05 * H, 0.02 * H), (0.08 * H, 0.15 * H, 0.04 * H))
    # torso (a flattened lathe), neck, head
    torso = view.transformed(Matrix.Diagonal((1.0, 0.68, 1.0, 1.0)))
    torso.lathe(coat, (0.0, 0.0), [(0.13 * H, hip_z - 0.02 * H), (0.15 * H, 0.62 * H), (0.155 * H, 0.74 * H), (0.12 * H, 0.82 * H), (0.0, 0.835 * H)], sides=14)
    view.beam(skin, (0.0, 0.0, 0.8 * H), (0.0, 0.0, 0.87 * H), 0.035 * H, sides=8)
    view.sphere(skin, (0.0, 0.0, 0.925 * H), (0.07 * H, 0.075 * H, 0.085 * H), subdivision=2)
    view.sphere(coat, (0.0, 0.01 * H, 0.95 * H), (0.072 * H, 0.077 * H, 0.065 * H), subdivision=1)   # hair / cap
    # arms, swung opposite to the legs
    for side, s in ((-1, -1.0), (1, 1.0)):
        sx = side * 0.19 * H
        fwd = -math.sin(swing * s * 0.8) * 0.35 * H
        elbow = (sx * 1.1, fwd * 0.5, 0.62 * H)
        hand = (sx * 1.05, fwd, 0.47 * H)
        view.beam(coat, (sx * 0.9, 0.0, 0.8 * H), elbow, 0.045 * H, end=0.04 * H, sides=8)
        view.beam(coat, elbow, hand, 0.04 * H, end=0.032 * H, sides=8)
        view.sphere(skin, hand, 0.035 * H, subdivision=1)
