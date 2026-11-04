"""LanguageRuntime protocol + registry: starter files, static lint, sandboxed build, file ownership."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel

from codeverse3d.contracts.artifacts import BuildResult, GateReport, RenderSet
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import AssetPlan, CameraPlan, Plan, ScenePlan
from codeverse3d.conventions import to_snake
from codeverse3d.workspace import Workspace

if TYPE_CHECKING:
    from codeverse3d.spatial.probes import SceneProbeResult


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
        """The files that own a refine target (part, zone, asset, camera, env word); ``[]`` leaves the task whole-artifact."""
        ...


@runtime_checkable
class SceneRuntime(LanguageRuntime, Protocol):
    """What the scene track asks of its language: zone / asset locations, entry assembly, probe and render."""

#: the environment module the env stage writes (``src/env.js`` / ``src/env.py``)
    env_file: str

    def zone_file(self, name: str) -> str:
        """The module that builds zone ``name`` (``src/zones/<snake>.<ext>``)."""
        ...

    def asset_file(self, asset: AssetPlan) -> str:
        """The file an asset ships as: its factory module, or a hero's GLB."""
        ...

    def assemble(self, ws: Workspace, plan: ScenePlan) -> BaseModel:
        """Write the entry file from the zones that load (the plan's cameras, else derived ones);
        the result lands in ``artifacts/assemble.json``."""
        ...

    def lib_interface(self, ws: Workspace) -> str:
        """The harness-owned ``src/lib/`` as one fact line per module (exports and parameters); ``""`` if none."""
        ...

    def check_env(self, ws: Workspace) -> str:
        """The env module alone: ``""`` when it lints, builds and lights clean; else the failure, quoted for a repair prompt."""
        ...

    def probe(self, ws: Workspace, *, timeout_s: float | None = None) -> SceneProbeResult:
        """Load the built scene headlessly: the ``scene_probe`` gate + the census."""
        ...

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None = None, orbit: bool = True,
                     times: Sequence[float] = (0.0, 1.5), width: int = 1024, height: int = 576,
                     sheet: bool = True) -> RenderSet:
        """Authored cameras (+ the orbit rig) × ``times`` → views, metrics.json, the contact sheet."""
        ...


class RuntimeLayout:
    """``expected_files`` / ``files_for`` for a runtime whose entry file (+ ``extra_files``) owns the whole artifact."""

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


#: Language → (module, class); the import is lazy.
_RUNTIMES: dict[Language, tuple[str, str]] = {
    Language.BLENDER: ("codeverse3d.languages.blender", "BlenderRuntime"),
    Language.CADQUERY: ("codeverse3d.languages.cadquery", "CadQueryRuntime"),
    Language.THREEJS: ("codeverse3d.languages.threejs", "ThreeJsRuntime"),
    Language.URDF_BLENDER: ("codeverse3d.languages.urdf", "UrdfBlenderRuntime"),
    Language.SCENE_THREEJS: ("codeverse3d.languages.scene_threejs", "SceneThreeJsRuntime"),
    Language.SCENE_BLENDER: ("codeverse3d.languages.scene_blender", "SceneBlenderRuntime"),
    Language.GLSL_SHADER: ("codeverse3d.languages.glsl_shader", "GlslShaderRuntime"),
    Language.OPENGL_PYTHON: ("codeverse3d.languages.opengl_python", "OpenGLPythonRuntime"),
}


def get_runtime(language: Language | str) -> LanguageRuntime:
    module, cls = _RUNTIMES[Language(language)]
    return getattr(import_module(module), cls)()
