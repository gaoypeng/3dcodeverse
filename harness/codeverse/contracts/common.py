"""Enums and small value types used everywhere."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

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
    GLSL_SHADER = "glsl_shader"  # Shadertoy-style fragment shader (GLSL 330/ES 3.0), rendered by moderngl
    OPENGL_PYTHON = "opengl_python"  # raw OpenGL via moderngl + GLSL (multi-pass, geometry, FBOs)


#: languages allowed per track
TRACK_LANGUAGES: dict[Track, tuple[Language, ...]] = {
    Track.STATIC_OBJECT: (Language.BLENDER, Language.CADQUERY, Language.THREEJS),
    Track.ARTICULATED_OBJECT: (Language.URDF_BLENDER,),
    Track.SCENE: (Language.SCENE_THREEJS,),
    Track.GRAPHICS: (Language.GLSL_SHADER, Language.OPENGL_PYTHON),
}


class Usage(BaseModel):
    """Normalised token/cost accounting for ONE model or agent call."""

    backend: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            backend=self.backend or other.backend,
            model=self.model if self.model == other.model else (self.model or other.model),
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            thoughts_tokens=self.thoughts_tokens + other.thoughts_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_ms=self.latency_ms + other.latency_ms,
        )


class Budget(BaseModel):
    """Hard ceilings for a run.  The orchestrator stops (cleanly) when any is hit."""

    max_rounds: int = 4
    max_usd: float = 5.0
    max_minutes: float = 60.0
    max_repair_attempts: int = 3  # per build failure before escalating


class Backends(BaseModel):
    """Which model/agent does which job.  Ids are ``<kind>:<model>``:

    * API chat models:  ``gemini:gemini-3.7-flash`` · ``anthropic:claude-sonnet-5``
      · ``openai:gpt-5.6-sol``
    * Coding agents:    ``gemini-cli:gemini-3.7-flash`` · ``claude-code:sonnet``
      · ``codex:gpt-5.6-sol`` · ``agy:gemini-3.6-flash-high``
      · ``api-agent:gemini:gemini-3.7-flash`` (in-process tool loop on a ChatModel)
    """

    planner: str = "gemini:gemini-3.7-flash"
    generator: str = "api-agent:gemini:gemini-3.7-flash"
    judge: str = "gemini:gemini-3.1-pro-preview"
    captioner: str = "gemini:gemini-3.7-flash"
