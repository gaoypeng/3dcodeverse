"""Enums and small value types used everywhere."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

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
    GLSL_SHADER = "glsl_shader"  # Shadertoy-style fragment shader (GLSL 330/ES 3.0), rendered by moderngl
    OPENGL_PYTHON = "opengl_python"  # raw OpenGL via moderngl + GLSL (multi-pass, geometry, FBOs)


#: languages allowed per track
TRACK_LANGUAGES: dict[Track, tuple[Language, ...]] = {
    Track.STATIC_OBJECT: (Language.BLENDER, Language.CADQUERY, Language.THREEJS),
    Track.ARTICULATED_OBJECT: (Language.URDF_BLENDER,),
    Track.SCENE: (Language.SCENE_THREEJS,),
    Track.GRAPHICS: (Language.GLSL_SHADER, Language.OPENGL_PYTHON),
}


class TrackInfo(BaseModel):
    """Per-track registry row: default judge rubric + human label.

    THE single source for what used to be restated as ``TRACK_RUBRIC`` /
    ``RUBRIC_BY_TRACK`` / ``TYPE_LABEL`` in judges, texturing, flywheel and cli.
    """

    model_config = ConfigDict(frozen=True)

    rubric: str = Field(description="rubric name under judges/rubrics/, e.g. 'static_object_v1'")
    label: str = Field(description="human label for galleries / captions / reports")


#: one registry row per track
TRACK_INFO: dict[Track, TrackInfo] = {
    Track.STATIC_OBJECT: TrackInfo(rubric="static_object_v1", label="3D Objects"),
    Track.ARTICULATED_OBJECT: TrackInfo(rubric="articulated_v1", label="Articulated Objects"),
    Track.SCENE: TrackInfo(rubric="scene_v1", label="3D Scenes"),
    Track.GRAPHICS: TrackInfo(rubric="shader_v1", label="Procedural Graphics"),
}

#: entry file inside the workspace per language — the file a build starts from.
#: THE single source for entry paths (runtimes' first entry_glob, flywheel export).
ENTRY_FILE: dict[Language, str] = {
    Language.BLENDER: "src/model.py",
    Language.CADQUERY: "src/model.py",
    Language.URDF_BLENDER: "src/model.py",
    Language.THREEJS: "src/object.js",
    Language.SCENE_THREEJS: "src/scene.js",
    Language.GLSL_SHADER: "src/shader.frag",
    Language.OPENGL_PYTHON: "src/program.py",
}

#: human label per language (galleries, captions, reports)
LANGUAGE_LABEL: dict[Language, str] = {
    Language.BLENDER: "Blender Python",
    Language.CADQUERY: "CadQuery (Python)",
    Language.URDF_BLENDER: "URDF + Blender Python",
    Language.THREEJS: "Three.js (ESM)",
    Language.SCENE_THREEJS: "Three.js scene (multi-file ESM + GLSL)",
    Language.GLSL_SHADER: "GLSL fragment shader",
    Language.OPENGL_PYTHON: "OpenGL (moderngl Python + GLSL)",
}


def code_file(language: Language) -> str:
    """Top-level flywheel copy of the entry file: ``code`` + the entry suffix
    (``code.py`` / ``code.js`` / ``code.frag``)."""
    entry = ENTRY_FILE[language]
    return "code" + entry[entry.rfind(".") :]


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
    """Hard ceilings for a run.  The orchestrator stops (cleanly) when any is hit.

    The ceilings are non-negative by construction.  A negative one is not a small
    budget, it is an unrunnable one: ``BudgetGuard.ok()`` is False before a single
    token is spent, the first charge raises ``BudgetExceeded: cost $0.001 exceeds
    max_usd $-2.50``, and ``grant_grace`` cannot lift a hard ceiling back above zero —
    so the workspace and the git-committed spec are created for a run that can only die.
    """

    max_rounds: int = Field(default=4, ge=0)
    max_usd: float = Field(default=5.0, ge=0)
    max_minutes: float = Field(default=60.0, ge=0)
    max_repair_attempts: int = Field(default=3, ge=0)  # per build failure before escalating


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
    # The judge drives the refine loop: the pro tier has ~3x lower sample noise than
    # flash (calibration 2026-08-23: std 0.03 vs 0.08-0.12) for ~$0.07 per verdict.
    judge: str = "gemini:gemini-3.1-pro-preview"
    captioner: str = "gemini:gemini-3.7-flash"
