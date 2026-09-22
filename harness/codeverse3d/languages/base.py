"""LanguageRuntime protocol + registry.

A runtime knows how to (1) lay down starter files for a plan — and so which file
owns what (``expected_files`` / ``files_for``: the tracks ask, never restate), (2)
statically lint agent code for known pitfalls, (3) execute it in a sandboxed
subprocess and export the canonical artifact(s).  Its authoring contract is prompt
material (``prompts/<lang>/contract.md``, found by ``prompts.catalog.language_prompt``).
Runtimes never import agent code into the harness process.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Protocol, runtime_checkable

from codeverse3d.contracts.artifacts import BuildResult, GateReport
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import Plan
from codeverse3d.conventions import to_snake
from codeverse3d.workspace import Workspace


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

    def expected_files(self, plan: Plan | None) -> list[str]:
        """The files a whole-artifact session writes for ``plan``, entry first."""
        ...

    def files_for(self, plan: Plan | None, target: str) -> list[str]:
        """The files that own a refine target (a part, zone, asset, camera, env word); ``[]`` = no
        owner, so the task stays whole-artifact."""
        ...


class RuntimeLayout:
    """``expected_files`` / ``files_for`` for a runtime whose entry file (+ ``extra_files``) owns
    the whole artifact — or, when it sets ``part_file``, one file per plan part too.  The scene
    runtime lays out zones / assets / env and overrides both."""

    language: Language
    #: files a whole-artifact session owns besides the entry (urdf: robot.urdf; glsl_shader: common.glsl)
    extra_files: tuple[str, ...] = ()
    #: plan part name → the file that builds it, in a language with one file per part (blender, three.js)
    part_file: Callable[[str], str] | None = None

    def expected_files(self, plan: Plan | None) -> list[str]:
        parts = [self.part_file(p.name) for p in getattr(plan, "parts", None) or ()] if self.part_file else []
        return [ENTRY_FILE[self.language], *self.extra_files, *parts]

    def files_for(self, plan: Plan | None, target: str) -> list[str]:
        if self.part_file is None:
            return []
        key = to_snake(target)
        if key in {to_snake(p.name) for p in getattr(plan, "parts", None) or ()}:
            return [self.part_file(target)]
        return [ENTRY_FILE[self.language]] if key in ("overall", "assembly", "object", "") else []


#: Language → (module, class).  Adding a language is a row, and the import stays lazy
#: (a runtime pulls in bpy / moderngl / node bindings the other six do not need).
_RUNTIMES: dict[Language, tuple[str, str]] = {
    Language.BLENDER: ("codeverse3d.languages.blender", "BlenderRuntime"),
    Language.CADQUERY: ("codeverse3d.languages.cadquery", "CadQueryRuntime"),
    Language.THREEJS: ("codeverse3d.languages.threejs", "ThreeJsRuntime"),
    Language.URDF_BLENDER: ("codeverse3d.languages.urdf", "UrdfBlenderRuntime"),
    Language.SCENE_THREEJS: ("codeverse3d.languages.scene_threejs", "SceneThreeJsRuntime"),
    Language.GLSL_SHADER: ("codeverse3d.languages.glsl_shader", "GlslShaderRuntime"),
    Language.OPENGL_PYTHON: ("codeverse3d.languages.opengl_python", "OpenGLPythonRuntime"),
}


def get_runtime(language: Language | str) -> LanguageRuntime:
    module, cls = _RUNTIMES[Language(language)]
    return getattr(import_module(module), cls)()
