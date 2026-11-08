"""Enums and small value types used everywhere."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Vec3 = Annotated[tuple[float, float, float], Field(description="x, y, z")]


class Track(StrEnum):
    """What kind of 3D thing we are producing."""

    STATIC_OBJECT = "static_object"
    ARTICULATED_OBJECT = "articulated_object"
    SCENE = "scene"
    GRAPHICS = "graphics"  # shader / OpenGL effects (2D/2.5D/3D procedural graphics)


class Language(StrEnum):
    """Raw authoring language of the deliverable (no SDKs on top)."""

    BLENDER = "blender"  # pure bpy script → GLB/STL
    CADQUERY = "cadquery"  # pure cadquery script → STEP/STL/GLB
    THREEJS = "threejs"  # procedural three.js (ESM) → GLB via GLTFExporter
    URDF_BLENDER = "urdf_blender"  # bpy link meshes + hand-written URDF
    SCENE_THREEJS = "scene_threejs"  # multi-file three.js scene (+GLSL, +GLB assets)
    SCENE_BLENDER = "scene_blender"  # multi-file bpy scene (+GLB heroes), rendered by Blender
    GLSL_SHADER = "glsl_shader"  # Shadertoy-style fragment shader (GLSL 330/ES 3.0), rendered by moderngl
    OPENGL_PYTHON = "opengl_python"  # raw OpenGL via moderngl + GLSL (multi-pass, geometry, FBOs)


#: languages allowed per track
TRACK_LANGUAGES: dict[Track, tuple[Language, ...]] = {
    Track.STATIC_OBJECT: (Language.BLENDER, Language.CADQUERY, Language.THREEJS),
    Track.ARTICULATED_OBJECT: (Language.URDF_BLENDER,),
    Track.SCENE: (Language.SCENE_THREEJS, Language.SCENE_BLENDER),
    Track.GRAPHICS: (Language.GLSL_SHADER, Language.OPENGL_PYTHON),
}

#: the rubric (``judges/rubrics/<name>.yaml``) each track is judged on
TRACK_RUBRIC: dict[Track, str] = {
    Track.STATIC_OBJECT: "static_object",
    Track.ARTICULATED_OBJECT: "articulated",
    Track.SCENE: "scene",
    Track.GRAPHICS: "shader",
}

#: entry file inside the workspace per language — the file a build starts from.
ENTRY_FILE: dict[Language, str] = {
    Language.BLENDER: "src/model.py",
    Language.CADQUERY: "src/model.py",
    Language.URDF_BLENDER: "src/model.py",
    Language.THREEJS: "src/object.js",
    Language.SCENE_THREEJS: "src/scene.js",
    Language.SCENE_BLENDER: "src/scene.py",
    Language.GLSL_SHADER: "src/shader.frag",
    Language.OPENGL_PYTHON: "src/program.py",
}


# ------------------------------------------------------------------ joint coupling
#: |multiplier| below this is a coupling that transmits no motion (shared by plan, lint and loader).
MIMIC_MIN_MULTIPLIER = 1e-9


class MimicSpec(BaseModel):
    """One joint as the coupling rules see it, in whatever vocabulary the caller has.

    ``key`` is the caller's canonical name (the plan folds case and underscores, URDF
    does not); ``name`` is what its messages should print.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    movable: bool
    target: str | None = None  # key of the joint this one follows; None = drives itself
    multiplier: float = 1.0
    offset: float = 0.0
    lower: float | None = None  # this joint's own limits, when it declares them
    upper: float | None = None


class MimicIssue(BaseModel):
    """One broken coupling rule.  ``kind`` is what is wrong, ``joint``/``target`` are
    names as written, ``detail`` carries the joint a cycle closes through."""

    model_config = ConfigDict(frozen=True)

    joint: str
    kind: Literal["immobile", "zero_multiplier", "self", "unknown_target", "immobile_target",
                  "cycle", "out_of_range"]
    target: str
    detail: str = ""


def mimic_issues(specs: Collection[MimicSpec]) -> list[MimicIssue]:
    """Every rule a ``<mimic>`` has to satisfy, checked once over the whole joint set.

    Callers render the issues in their own vocabulary and decide what is fatal
    (``out_of_range`` is only a warning: the coupling still poses the mechanism).
    """
    order = list(specs)
    by_key = {s.key: s for s in order}
    out: list[MimicIssue] = []
    for s in order:
        if s.target is None:
            continue
        if not s.movable:
            out.append(MimicIssue(joint=s.name, kind="immobile", target=s.target))
        if abs(s.multiplier) < MIMIC_MIN_MULTIPLIER:
            out.append(MimicIssue(joint=s.name, kind="zero_multiplier", target=s.target))
        if s.target == s.key:
            out.append(MimicIssue(joint=s.name, kind="self", target=s.name))
            continue
        src = by_key.get(s.target)
        if src is None:
            out.append(MimicIssue(joint=s.name, kind="unknown_target", target=s.target))
            continue
        if not src.movable:
            out.append(MimicIssue(joint=s.name, kind="immobile_target", target=src.name))
        reach = _driven_range(s, src)
        if reach is not None:
            out.append(MimicIssue(joint=s.name, kind="out_of_range", target=src.name,
                                  detail=f"[{reach[0]:.4g}, {reach[1]:.4g}]"))
        seen, cur = {s.key}, src
        while cur.target is not None:
            if cur.key in seen:
                out.append(MimicIssue(joint=s.name, kind="cycle", target=s.target, detail=cur.name))
                break
            seen.add(cur.key)
            nxt = by_key.get(cur.target)
            if nxt is None:
                break  # that joint reports its own unknown target on its own turn
            cur = nxt
    return out


def _driven_range(follower: MimicSpec, driver: MimicSpec) -> tuple[float, float] | None:
    """Where the coupling takes ``follower`` if outside its own limits, else ``None``.

    Slack is 1 % of the follower's span, absorbing multiplier/limit rounding.
    """
    if None in (follower.lower, follower.upper, driver.lower, driver.upper):
        return None
    ends = (follower.multiplier * driver.lower + follower.offset,
            follower.multiplier * driver.upper + follower.offset)
    lo, hi = min(ends), max(ends)
    slack = max(1e-6, 0.01 * (follower.upper - follower.lower))
    if lo < follower.lower - slack or hi > follower.upper + slack:
        return lo, hi
    return None
