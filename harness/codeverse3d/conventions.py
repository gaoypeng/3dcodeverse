"""Single source of truth for 3D conventions used by the instruments, gates and skills.

Everything that talks about axes, units, frames, view presets or naming imports
from here — never restate a convention inline.

Frames
------
* ``blender`` / ``cadquery`` / ``urdf`` / ``scene_blender``:  **Z-up, -Y is the object's front**,
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
    "scene_blender": Frame.Z_UP_NEG_Y_FRONT,
    "glsl_shader": Frame.Y_UP_POS_Z_FRONT,
    "opengl_python": Frame.Y_UP_POS_Z_FRONT,
}

GLB_FRAME = Frame.Y_UP_POS_Z_FRONT


def authoring_frame(language: str) -> Frame:
    return LANGUAGE_FRAME.get(str(language), GLB_FRAME)


def to_authoring_frame(v: Sequence[float], language: str, *, extents: bool = False) -> tuple[float, float, float]:
    """A GLB-frame vector in ``language``'s authoring frame (a Z-up language reads ``(x, -z, y)``).
    ``extents`` (sizes) are permuted, never sign-flipped; the permutation is its own inverse."""
    x, y, z = (float(c) for c in v)
    if authoring_frame(language) is Frame.Z_UP_NEG_Y_FRONT:
        return (x, z if extents else -z, y)
    return (x, y, z)


def glb_basis(language: str) -> tuple[tuple[float, float, float], ...]:
    """The 3×3 rows taking a ``language``-frame vector into the GLB frame (identity for Y-up).
    Handed to writers that cannot import this module (the census-GLB writer runs in Blender)."""
    if authoring_frame(language) is Frame.Z_UP_NEG_Y_FRONT:
        return ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def from_authoring_frame(v: Sequence[float], language: str) -> tuple[float, float, float]:
    """A point / direction in ``language``'s frame, in the GLB frame (:func:`glb_basis` applied)."""
    x, y, z = (float(c) for c in v)
    r = glb_basis(language)
    return (r[0][0] * x + r[0][1] * y + r[0][2] * z, r[1][0] * x + r[1][1] * y + r[1][2] * z,
            r[2][0] * x + r[2][1] * y + r[2][2] * z)


def frame_doc(frame: Frame) -> str:
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
    name: str
    azimuth_deg: float
    elevation_deg: float


#: Judge view set for objects: 14 views (rings at ±30°, eye-level cardinals, poles).
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

#: Clay cameras (the judge's geometry montage), separate from the rig.  Its ``top`` shares a
#: name with the rig's: never union the two tuples by name.
OBJECT_CLAY_VIEWS: tuple[ViewPreset, ...] = (
    ViewPreset("front_right_34", 35.0, 22.0),
    ViewPreset("back_left_34", 215.0, 22.0),
    ViewPreset("top", 0.0, 88.0),
    ViewPreset("low_front_left", 325.0, -12.0),
)

_OBJECT_VIEW_BY_NAME: dict[str, ViewPreset] = {v.name: v for v in OBJECT_VIEWS}

#: Quick 4-view subset (cheap gates, contact sheets, candidates), picked by name.
OBJECT_VIEWS_QUICK: tuple[ViewPreset, ...] = tuple(
    _OBJECT_VIEW_BY_NAME[n] for n in ("front_right_high", "back_left_high", "front", "top")
)

#: The articulation sheet's three views per pose (``joints_export.render_poses``).
ARTICULATION_VIEWS: tuple[ViewPreset, ...] = tuple(
    _OBJECT_VIEW_BY_NAME[n] for n in ("front_right_high", "back_left_high", "front")
)

#: Clay-camera view names → their twin in the rig; view preference lists use rig names and
#: match through :func:`view_key`.
LEGACY_VIEW_ALIASES: dict[str, str] = {
    "front_right_34": "front_right_high",
    "back_left_34": "back_left_high",
    "low_front_left": "front_left_low",
}


def view_key(name: str) -> str:
    return LEGACY_VIEW_ALIASES.get(name, name)


def views_by_preference(views: Sequence[RenderView], names: Sequence[str]) -> list[RenderView]:
    rank = {n: i for i, n in enumerate(names)}
    return sorted((v for v in views if view_key(v.name) in rank), key=lambda v: rank[view_key(v.name)])


#: Scene rig (aerial + eye level).  ``overview_*`` reach the judge; ``overview_low_front``
#: (elevation < 10°) is the ground-level contact camera the contact crops are cut from;
#: ``eye_*`` are diagnostics only.
SCENE_VIEWS: tuple[ViewPreset, ...] = (
    ViewPreset("overview_front_right", 40.0, 32.0),
    ViewPreset("overview_back_left", 220.0, 32.0),
    ViewPreset("overview_top", 0.0, 87.0),
    ViewPreset("overview_low_front", 20.0, 7.0),
    ViewPreset("eye_front", 0.0, 10.0),
    ViewPreset("eye_right", 90.0, 14.0),
    ViewPreset("eye_back_left", 235.0, 12.0),
)


# --------------------------------------------------------------------------- names
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^0-9A-Za-z]+")


def to_snake(name: str) -> str:
    """``SeatCushion`` / ``seat cushion`` / ``Seat-Cushion2`` → ``seat_cushion2`` (THE name normaliser)."""
    s = _NON_WORD.sub("_", name.strip())
    s = _CAMEL_SPLIT.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "part"


#: a part / assembly name: ``SeatCushion``, instances ``SeatCushion_3``; acronyms (``LED``,
#: ``TVStand``, ``CPU_2``) are valid.  ``wrappers/run_cq.py`` keeps a copy; tests pin them.
PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(?:_\d+)?$")


#: a URDF link name (also its Blender object name and ``meshes/<link>.glb`` stem): a plain
#: identifier.  ``run_bpy_links._SAFE_LINK`` keeps a copy; tests pin them.
LINK_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


#: instance name → part: ``Leg_2``, ``Leg.001`` and ``Leg-2`` all read as ``Leg``; at most
#: three digits, so an all-digit asset id (``DonkeyCart_800006``) is no index.
_INSTANCE_RE = re.compile(r"^(?P<base>.+?)[_.-](?P<idx>\d{1,3})$")


def split_instance(name: str) -> tuple[str, str]:
    m = _INSTANCE_RE.match(name)
    return (m.group("base"), m.group("idx")) if m else (name, "")


#: separator of a URDF link's geometry-piece nodes ``<link>__<i>``.
LINK_PIECE_SEP = "__"

#: userData / glTF extras key marking a scene ZONE root; only children of a marked root are
#: placed assets.  ``host_placement.mjs`` keeps a copy; tests pin them.
ZONE_ROOT_KEY = "c3d_zone"


def to_pascal(name: str) -> str:
    """``seat_cushion`` → ``SeatCushion``; acronyms are lost (``tv_stand`` → ``TvStand``), so compare via ``to_snake``."""
    if "_" not in name and PASCAL_RE.match(name):
        return name
    return "".join(w[:1].upper() + w[1:] for w in to_snake(name).split("_") if w) or "Part"


def fmt3(vec: Sequence[float]) -> str:
    return ", ".join(f"{float(x):.3f}" for x in vec)


# --------------------------------------------------------------------------- limits
#: Triangle ceilings; the object one is the depth gate's upper clamp (``gates.budget_gate``).
MAX_TRIS_OBJECT = 600_000
MAX_TRIS_SCENE = 3_000_000
#: Scene draw budget on the probe census's ``totals.draws``: ``scene_probe`` WARNs above the
#: first and fails the build above the second.
DRAWS_WARN_SCENE = 5_000
MAX_DRAWS_SCENE = 15_000
#: Default tolerance for bbox contract checks, in meters.
BBOX_TOLERANCE_M = 0.01
#: "Stands on the ground": the lowest point within this of up = 0 (meters); WARN past it, ERROR past 3x.
GROUND_TOL_M = 0.01
#: Parts are "touching" when the surface gap is below this (meters).
CONTACT_GAP_M = 0.002


#: the scene frame clock (seconds): the judged frames, ``ctx.times`` in scene_blender and the render defaults
SCENE_TIMES: tuple[float, float] = (0.0, 1.5)
