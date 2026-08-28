"""LanguageRuntime protocol + registry.

A runtime knows how to (1) lay down starter files for a plan, (2) statically
lint agent code for known pitfalls, (3) execute it in a sandboxed subprocess
and export the canonical artifact(s), (4) describe its authoring contract to
prompts.  Runtimes never import agent code into the harness process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import Language
from codeverse.contracts.plan import Plan
from codeverse.workspace import Workspace


@runtime_checkable
class LanguageRuntime(Protocol):
    language: Language
    #: files the agent is expected to author (globs relative to workspace root)
    entry_globs: tuple[str, ...]

    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        """Write starter files (contract comments, stubs) into ws.src; return paths."""
        ...

    def lint(self, ws: Workspace) -> GateReport:
        """Static checks (syntax, forbidden APIs, known pitfalls, missing entry points)."""
        ...

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Execute the code in a subprocess; export ``artifacts/object.glb`` (+extras)."""
        ...

    def contract_doc(self) -> str:
        """Prompt text: the authoring contract for this language (from prompts/<lang>/)."""
        ...

    def cookbook_path(self) -> Path: ...


def get_runtime(language: Language | str) -> LanguageRuntime:
    lang = Language(language)
    if lang is Language.BLENDER:
        from codeverse.languages.blender import BlenderRuntime

        return BlenderRuntime()
    if lang is Language.CADQUERY:
        from codeverse.languages.cadquery import CadQueryRuntime

        return CadQueryRuntime()
    if lang is Language.THREEJS:
        from codeverse.languages.threejs import ThreeJsRuntime

        return ThreeJsRuntime()
    if lang is Language.URDF_BLENDER:
        from codeverse.languages.urdf import UrdfBlenderRuntime

        return UrdfBlenderRuntime()
    if lang is Language.GLSL_SHADER:
        from codeverse.languages.glsl_shader import GlslShaderRuntime

        return GlslShaderRuntime()
    if lang is Language.OPENGL_PYTHON:
        from codeverse.languages.opengl_python import OpenGLPythonRuntime

        return OpenGLPythonRuntime()
    from codeverse.languages.scene_threejs import SceneThreeJsRuntime

    return SceneThreeJsRuntime()
