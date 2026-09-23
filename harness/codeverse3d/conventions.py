"""Single source of truth for 3D conventions used by prompts, tools and gates.

Everything that talks about axes, units, frames, view presets or naming must
import from here — never restate a convention inline (the reference harnesses
drifted into 3-4 incompatible ``snake_case`` helpers and two up-axes).

Frames
------
* ``blender`` / ``cadquery`` / ``urdf``:  **Z-up, -Y is the object's front**,
  meters.  (Blender's "Front" view looks along +Y, so a front face points -Y.)
* ``threejs`` / ``scene_threejs``:        **Y-up, +Z is the front**, meters.
* ``glsl_shader`` / ``opengl_python``:    **Y-up, +Z front** (GL clip space is
  Y-up; harness cameras/gates treat graphics output like the three.js frame).
* Canonical exchange format is **GLB (glTF 2.0): Y-up, +Z front, meters**.
  Blender's glTF exporter maps (x, y, z) → (x, z, -y), so a Blender -Y front
  becomes glTF +Z front — the two native frames agree once exported.
* Objects stand on the ground: the lowest point is at up=0 and the footprint
  is centred on the up-axis.  Scenes may use any origin but bounds are declared.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeverse3d.contracts.artifacts import RenderView


class Frame(StrEnum):
    """Named coordinate conventions."""

    Z_UP_NEG_Y_FRONT = "z_up_neg_y_front"  # blender / cadquery / urdf
    Y_UP_POS_Z_FRONT = "y_up_pos_z_front"  # three.js / glTF


#: up / front axes per frame as unit vectors (x, y, z)
FRAME_AXES: dict[Frame, dict[str, tuple[float, float, float]]] = {
    Frame.Z_UP_NEG_Y_FRONT: {"up": (0, 0, 1), "front": (0, -1, 0), "right": (1, 0, 0)},
    Frame.Y_UP_POS_Z_FRONT: {"up": (0, 1, 0), "front": (0, 0, 1), "right": (1, 0, 0)},
}

#: language id → native authoring frame
LANGUAGE_FRAME: dict[str, Frame] = {
    "blender": Frame.Z_UP_NEG_Y_FRONT,
    "cadquery": Frame.Z_UP_NEG_Y_FRONT,
    "urdf_blender": Frame.Z_UP_NEG_Y_FRONT,
    "threejs": Frame.Y_UP_POS_Z_FRONT,
    "scene_threejs": Frame.Y_UP_POS_Z_FRONT,
    # GL clip space is Y-up: graphics output is framed like the three.js/glTF frame.
    "glsl_shader": Frame.Y_UP_POS_Z_FRONT,
    "opengl_python": Frame.Y_UP_POS_Z_FRONT,
}

GLB_FRAME = Frame.Y_UP_POS_Z_FRONT


def authoring_frame(language: str) -> Frame:
    """``language``'s authoring frame; an unknown / empty id is the GLB frame itself."""
    return LANGUAGE_FRAME.get(str(language), GLB_FRAME)


def to_authoring_frame(v: Sequence[float], language: str, *, extents: bool = False) -> tuple[float, float, float]:
    """A GLB-frame vector (Y-up, +Z front) in ``language``'s authoring frame: the inverse of the
    glTF export mapping ``(x, y, z) → (x, z, -y)``, so a Z-up language reads ``(x, -z, y)``.
    ``extents`` (sizes, size deltas) are permuted, never sign-flipped — and that permutation is
    its own inverse: it also takes a Z-up plan's (W, D, H) into the GLB frame's (W, H, D)."""
    x, y, z = (float(c) for c in v)
    if authoring_frame(language) is Frame.Z_UP_NEG_Y_FRONT:
        return (x, z if extents else -z, y)
    return (x, y, z)


def frame_doc(frame: Frame) -> str:
    """Return the one-paragraph prompt text describing ``frame``."""
    if frame is Frame.Z_UP_NEG_Y_FRONT:
        return (
            "Coordinate frame: Z is UP, -Y is the FRONT of the object (the face a "
            "viewer sees from the default front view), +X is the object's right. "
            "Units are meters. The object stands on the ground plane z=0 and its "
            "footprint is centred on the Z axis."
        )
    return (
        "Coordinate frame: Y is UP, +Z is the FRONT of the object (towards the "
        "default camera), +X is the object's right. Units are meters. The object "
        "stands on the ground plane y=0 and its footprint is centred on the Y axis."
    )


# --------------------------------------------------------------------------- views
@dataclass(frozen=True)
class ViewPreset:
    """A canonical camera direction: azimuth (deg, 0 = front, CCW from above) and
    elevation (deg above the horizon).  Distance is fitted to the asset bbox."""

    name: str
    azimuth_deg: float
    elevation_deg: float


#: Judge view set for objects — the 14-view rig (brilliana camera numbers):
#: three rings ±30° + eye-level cardinals + poles; adopted 2026-08-31, D47.
OBJECT_VIEWS: tuple[ViewPreset, ...] = (
    ViewPreset("front_right_high", 45.0, 30.0),
    ViewPreset("back_right_high", 135.0, 30.0),
    ViewPreset("back_left_high", 225.0, 30.0),
    ViewPreset("front_left_high", 315.0, 30.0),
    ViewPreset("front", 0.0, 0.0),
    ViewPreset("right", 90.0, 0.0),
    ViewPreset("back", 180.0, 0.0),
    ViewPreset("left", 270.0, 0.0),
    ViewPreset("front_right_low", 45.0, -30.0),
    ViewPreset("back_right_low", 135.0, -30.0),
    ViewPreset("back_left_low", 225.0, -30.0),
    ViewPreset("front_left_low", 315.0, -30.0),
    ViewPreset("top", 0.0, 90.0),
    ViewPreset("bottom", 0.0, -90.0),
)

#: The measured legacy clay cameras (the judge's geometry montage) — its OWN tuple,
#: not a subset of the rig.  Its ``top`` (el 88) shares a name with the rig's ``top``
#: (el 90): that is fine ONLY because nothing unions the two tuples by name — clay
#: tiles carry ``mode != "shaded"`` and prompt_builder resolves their labels here first.
OBJECT_CLAY_VIEWS: tuple[ViewPreset, ...] = (
    ViewPreset("front_right_34", 35.0, 22.0),
    ViewPreset("back_left_34", 215.0, 22.0),
    ViewPreset("top", 0.0, 88.0),
    ViewPreset("low_front_left", 325.0, -12.0),
)

_OBJECT_VIEW_BY_NAME: dict[str, ViewPreset] = {v.name: v for v in OBJECT_VIEWS}

#: Quick 4-view subset (cheap gates, agent contact sheets, texture pass, candidates,
#: scene assets) — by NAME so a rig reorder cannot silently change it.
OBJECT_VIEWS_QUICK: tuple[ViewPreset, ...] = tuple(
    _OBJECT_VIEW_BY_NAME[n] for n in ("front_right_high", "back_left_high", "front", "top")
)

#: the view a one-camera comparison (the reference silhouette) takes, in preference order;
#: ``*_34`` are pre-D47 stored runs
FRONT_VIEW_NAMES: tuple[str, ...] = ("front", "front_right_high", "front_left_high",
                                     "front_right_34", "front_left_34")


def front_view(views: Sequence[RenderView]) -> RenderView | None:
    """The first of ``FRONT_VIEW_NAMES`` among ``views``, else the first view."""
    return next((v for n in FRONT_VIEW_NAMES for v in views if v.name == n), views[0] if views else None)


#: Scene overview rig (scenes sit on ground: aerial + eye level, no underside).
SCENE_VIEWS: tuple[ViewPreset, ...] = (
    ViewPreset("overview_front_right", 40.0, 32.0),
    ViewPreset("overview_back_left", 220.0, 32.0),
    ViewPreset("overview_top", 0.0, 87.0),
    ViewPreset("eye_front", 0.0, 10.0),
    ViewPreset("eye_right", 90.0, 14.0),
    ViewPreset("eye_back_left", 235.0, 12.0),
)


# --------------------------------------------------------------------------- names
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^0-9A-Za-z]+")


def to_snake(name: str) -> str:
    """``SeatCushion`` / ``seat cushion`` / ``Seat-Cushion2`` → ``seat_cushion2``.

    This is THE name normaliser; every wrapper and prompt must use it.
    """
    s = _NON_WORD.sub("_", name.strip())
    s = _CAMEL_SPLIT.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "part"


#: a part / assembly name as the contracts require it: ``SeatCushion``, instances ``SeatCushion_3``.
#: All-caps acronyms are valid PascalCase (owner, 2026-09-22): ``LED``, ``TV``, ``A``,
#: ``TVStand``, ``LEDStrip``, ``HDMIPort1``, ``CPU_2``.  The stdlib-only wrapper
#: ``languages/wrappers/run_cq.py`` keeps a copy (it cannot import this package);
#: ``tests/core/test_names.py`` pins the two together.
PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:_\d+)?$")


def to_pascal(name: str) -> str:
    """``seat_cushion`` / ``seat cushion`` → ``SeatCushion``; a name that is already
    PascalCase (no underscore) comes back unchanged, so ``TVStand`` stays ``TVStand``.

    For a name without an instance suffix ``to_snake(to_pascal(n)) == to_snake(n)`` — the
    snake name is the key every gate matches parts by (``CPU_2`` / ``Leg_0`` fold to
    ``Cpu2`` / ``Leg0``: an instance name is not a plan name).  The reverse loses acronyms: ``to_pascal("tv_stand")``
    is ``TvStand``, so a name rebuilt from a snake key (a file stem, a GLB key, a zone
    module) is ``TvStand`` — code that compares such a name must compare ``to_snake``.
    """
    if "_" not in name and PASCAL_RE.match(name):
        return name
    return "".join(w[:1].upper() + w[1:] for w in to_snake(name).split("_") if w) or "Part"


def slugify(text: str, max_len: int = 48) -> str:
    """Filesystem/URL-safe slug for run directories."""
    s = to_snake(text)[:max_len].strip("_")
    return s or "run"


def fmt3(vec: Sequence[float]) -> str:
    """A vector as the prompts print it: ``0.100, 0.250, -0.030`` (metres, 3 decimals)."""
    return ", ".join(f"{float(x):.3f}" for x in vec)


# --------------------------------------------------------------------------- limits
#: Triangle budgets used by gates/prompts (soft caps; judge sees the count).
MAX_TRIS_OBJECT = 600_000
MAX_TRIS_SCENE = 3_000_000
#: Default tolerance for bbox contract checks, in meters.
BBOX_TOLERANCE_M = 0.01
#: Parts are "touching" when the surface gap is below this (meters).
CONTACT_GAP_M = 0.002
