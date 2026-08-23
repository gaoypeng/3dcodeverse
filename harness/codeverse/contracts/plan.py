"""Plans: the planner's structured decomposition.  Every plan carries an
*acceptance checklist* — concrete, verifiable items derived from the spec —
so acceptance can be mechanical (evidence), not an opinion.

Plans are written by an LLM under a JSON schema and then VALIDATED IN CODE
(unit axes, single-root joint trees, sane limits, unique names).  Invalid
plans are re-asked with the validation errors, before any generator runs.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from codeverse.contracts.common import Vec3
from codeverse.conventions import to_snake


class BBox(BaseModel):
    """Axis-aligned box in the plan's frame: centre + full extents (meters)."""

    center: Vec3
    extents: Vec3

    @property
    def min(self) -> tuple[float, float, float]:
        return tuple(c - e / 2 for c, e in zip(self.center, self.extents, strict=True))  # type: ignore[return-value]

    @property
    def max(self) -> tuple[float, float, float]:
        return tuple(c + e / 2 for c, e in zip(self.center, self.extents, strict=True))  # type: ignore[return-value]


class AcceptanceItem(BaseModel):
    """One verifiable requirement.  ``how`` says which evidence proves it."""

    id: str
    text: str
    how: Literal["measure", "visual", "probe", "articulation"] = "visual"
    priority: Literal["must", "should"] = "must"


class PartPlan(BaseModel):
    name: str = Field(description="PascalCase unique part name, e.g. SeatCushion")
    role: str = Field(description="what this part is / does, one line")
    description: str = Field(description="shape, construction and visible detail the builder must realise")
    bbox: BBox
    material: str = Field(default="", description="material / finish in plain words")
    attach_to: str | None = Field(default=None, description="parent part name this part touches")
    symmetry: Literal["none", "mirror_x", "mirror_y", "radial"] = "none"
    instances: int = Field(default=1, ge=1, description="identical copies (e.g. 4 legs)")


class StaticPlan(BaseModel):
    object_name: str
    summary: str
    overall_bbox: BBox
    style_notes: str = ""
    parts: list[PartPlan] = Field(min_length=1)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> StaticPlan:
        seen: set[str] = set()
        for p in self.parts:
            key = to_snake(p.name)
            if key in seen:
                raise ValueError(f"duplicate part name (after normalisation): {p.name}")
            seen.add(key)
        names = {to_snake(p.name) for p in self.parts}
        for p in self.parts:
            if p.attach_to and to_snake(p.attach_to) not in names:
                raise ValueError(f"part {p.name} attaches to unknown part {p.attach_to}")
        return self


class JointPlan(BaseModel):
    name: str
    type: Literal["revolute", "prismatic", "continuous", "fixed"]
    parent: str = Field(description="parent link (part) name")
    child: str = Field(description="child link (part) name")
    axis: Vec3 = Field(description="unit axis in the plan frame")
    pivot: Vec3 = Field(description="a point on the joint axis, WORLD coords (rest pose)")
    lower: float = Field(default=0.0, description="rad for revolute, m for prismatic")
    upper: float = Field(default=0.0)
    rest: float = Field(default=0.0, description="joint value in the authored rest pose")
    motion: str = Field(default="", description="what moving this joint does, one line")

    @model_validator(mode="after")
    def _sane(self) -> JointPlan:
        n = math.sqrt(sum(a * a for a in self.axis))
        if n < 1e-6:
            raise ValueError(f"joint {self.name}: zero axis")
        if abs(n - 1.0) > 1e-3:
            self.axis = tuple(a / n for a in self.axis)  # type: ignore[assignment]
        if self.type in ("revolute", "prismatic"):
            if self.upper < self.lower:
                raise ValueError(f"joint {self.name}: upper < lower")
            if not (self.lower - 1e-9 <= self.rest <= self.upper + 1e-9):
                raise ValueError(f"joint {self.name}: rest {self.rest} outside [{self.lower},{self.upper}]")
            if self.type == "revolute" and (self.upper - self.lower) > 2 * math.pi + 1e-6:
                raise ValueError(f"joint {self.name}: revolute range > 2π, use continuous")
            if self.type == "prismatic" and (self.upper - self.lower) > 5.0:
                raise ValueError(f"joint {self.name}: prismatic range > 5 m is implausible")
        if to_snake(self.parent) == to_snake(self.child):
            raise ValueError(f"joint {self.name}: parent == child")
        return self


class ArticulatedPlan(StaticPlan):
    root_link: str
    joints: list[JointPlan] = Field(min_length=1)

    @model_validator(mode="after")
    def _tree(self) -> ArticulatedPlan:
        links = {to_snake(p.name) for p in self.parts}
        root = to_snake(self.root_link)
        if root not in links:
            raise ValueError(f"root_link {self.root_link} is not a part")
        parent_of: dict[str, str] = {}
        for j in self.joints:
            p, c = to_snake(j.parent), to_snake(j.child)
            if p not in links or c not in links:
                raise ValueError(f"joint {j.name} references unknown link(s) {j.parent}/{j.child}")
            if c in parent_of:
                raise ValueError(f"link {j.child} has two parent joints")
            if c == root:
                raise ValueError(f"root link {self.root_link} cannot be a joint child")
            parent_of[c] = p
        # every non-root link must reach root
        for link in links - {root}:
            seen, cur = set(), link
            while cur != root:
                if cur in seen or cur not in parent_of:
                    raise ValueError(f"link {link} is not connected to root {self.root_link} (single-root tree required)")
                seen.add(cur)
                cur = parent_of[cur]
        return self


class ZonePlan(BaseModel):
    name: str = Field(description="PascalCase zone name, e.g. Harbour")
    description: str
    bbox: BBox
    contents: list[str] = Field(default_factory=list, description="asset names placed in this zone")


class AssetPlan(BaseModel):
    name: str = Field(description="PascalCase asset name")
    kind: Literal["threejs", "blender_glb"] = Field(
        description="threejs = procedural module; blender_glb = built with bpy and compiled to a GLB"
    )
    description: str
    approx_size_m: Vec3
    instances_hint: int = 1


class EffectPlan(BaseModel):
    name: str
    kind: Literal["glsl_material", "postprocess", "particles", "animated_geometry"]
    description: str
    target: str = Field(default="", description="zone / asset / scene it applies to")


class CameraPlan(BaseModel):
    name: str
    position: Vec3
    look_at: Vec3
    fov: float = 50.0
    purpose: str = ""


class ScenePlan(BaseModel):
    title: str
    summary: str
    setting: str = Field(description="place, era, weather, time of day")
    mood: str = ""
    bounds: BBox
    environment: str = Field(description="sky, sun/moon, fog, ground, water — one paragraph")
    zones: list[ZonePlan] = Field(min_length=1)
    assets: list[AssetPlan] = Field(default_factory=list)
    effects: list[EffectPlan] = Field(default_factory=list)
    animation: list[str] = Field(default_factory=list, description="what moves and how")
    cameras: list[CameraPlan] = Field(min_length=1, max_length=6)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names(self) -> ScenePlan:
        assets = {to_snake(a.name) for a in self.assets}
        zones = set()
        for z in self.zones:
            k = to_snake(z.name)
            if k in zones:
                raise ValueError(f"duplicate zone {z.name}")
            zones.add(k)
            for c in z.contents:
                if to_snake(c) not in assets:
                    raise ValueError(f"zone {z.name} lists unknown asset {c}")
        return self


class PassPlan(BaseModel):
    name: str = Field(description="PascalCase pass name, e.g. Clouds, Bloom")
    kind: Literal["fullscreen", "geometry", "postprocess", "feedback"] = "fullscreen"
    description: str = Field(description="what this pass draws / computes, concrete")


class GraphicsPlan(BaseModel):
    """Plan for the graphics track (GLSL shader / raw OpenGL program)."""

    title: str
    summary: str
    style: str = Field(description="visual style: palette, mood, references in words")
    resolution: tuple[int, int] = (1280, 720)
    duration_s: float = Field(default=8.0, description="loop length for judging / video")
    passes: list[PassPlan] = Field(min_length=1)
    uniforms: list[str] = Field(default_factory=list, description="uniform names the program exposes (u_time, u_resolution, ...)")
    motion: str = Field(default="", description="what animates over time and how")
    key_visuals: list[str] = Field(default_factory=list, description="3-8 visible elements a viewer must recognise")
    acceptance: list[AcceptanceItem] = Field(default_factory=list)


Plan = StaticPlan | ArticulatedPlan | ScenePlan | GraphicsPlan
