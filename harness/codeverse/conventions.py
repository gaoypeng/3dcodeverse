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
from dataclasses import dataclass
from enum import StrEnum

UNITS = "meters"


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


#: Judge view set for objects: 3/4 views + orthographic-ish sides + top + underside.
OBJECT_VIEWS: tuple[ViewPreset, ...] = (
    ViewPreset("front_right_34", 35.0, 22.0),
    ViewPreset("back_left_34", 215.0, 22.0),
    ViewPreset("front", 0.0, 8.0),
    ViewPreset("right", 90.0, 8.0),
    ViewPreset("back", 180.0, 8.0),
    ViewPreset("left", 270.0, 8.0),
    ViewPreset("top", 0.0, 88.0),
    ViewPreset("low_front_left", 325.0, -12.0),
)

#: Quick 4-view subset (cheap gates, contact sheets for agents).
OBJECT_VIEWS_QUICK: tuple[ViewPreset, ...] = (
    OBJECT_VIEWS[0],
    OBJECT_VIEWS[1],
    OBJECT_VIEWS[2],
    OBJECT_VIEWS[6],
)

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


def to_pascal(name: str) -> str:
    """``seat_cushion`` / ``seat cushion`` → ``SeatCushion``."""
    return "".join(w[:1].upper() + w[1:] for w in to_snake(name).split("_") if w) or "Part"


def slugify(text: str, max_len: int = 48) -> str:
    """Filesystem/URL-safe slug for run directories."""
    s = to_snake(text)[:max_len].strip("_")
    return s or "run"


# --------------------------------------------------------------------------- limits
#: Triangle budgets used by gates/prompts (soft caps; judge sees the count).
MAX_TRIS_OBJECT = 600_000
MAX_TRIS_SCENE = 3_000_000
#: Default tolerance for bbox contract checks, in meters.
BBOX_TOLERANCE_M = 0.01
#: Parts are "touching" when the surface gap is below this (meters).
CONTACT_GAP_M = 0.002
