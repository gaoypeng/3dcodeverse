"""BlenderRuntime: lint → build (headless Blender subprocess) → BuildResult.

Layout (see ``layout.py``): ``src/model.py`` is the entry; ``src/parts/<snake>.py`` hold
one ``build_<snake>()`` per plan part (``file_for_part`` maps a part to its file so the
tracks can refine parts in parallel); a single-file ``model.py`` stays valid.  The
agent's code is never imported here; ``wrappers/run_bpy.py`` runs it inside Blender
(with ``src/`` on ``sys.path``), writes ``artifacts/build.json`` + ``census.json`` and
exports ``artifacts/object.glb`` (Y-up, +Z front) and ``object.stl`` (Z-up).
"""

from __future__ import annotations

import os
from pathlib import Path

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import Language
from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.conventions import MAX_TRIS_OBJECT
from codeverse.languages._common import (
    compose_build_result,
    remove_stale,
    run_subprocess,
    strip_blender_noise,
)
from codeverse.languages.blender.layout import ENTRY_REL, lint_workspace, part_file_rel
from codeverse.languages.blender.skeleton import write_blender_skeleton
from codeverse.proc import scrub_secrets
from codeverse.prompts import PROMPTS_DIR
from codeverse.workspace import Workspace

_PKG_DIR = Path(__file__).resolve().parent
WRAPPER = _PKG_DIR / "wrappers" / "run_bpy.py"


class BlenderNotFoundError(RuntimeError):
    """No usable Blender binary (configure ``CV3D_BINARIES__BLENDER`` or put blender on PATH)."""


def blender_env() -> dict[str, str]:
    """Environment for a headless Blender child: keep the user's env but make sure the
    host python (conda) cannot leak into Blender's bundled interpreter, and strip
    credential-shaped vars (:func:`codeverse.proc.scrub_secrets`) — the model-authored
    ``model.py`` executes inside this process and must never see API keys."""
    env = scrub_secrets(dict(os.environ))
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(k, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


class BlenderRuntime:
    """LanguageRuntime for ``Language.BLENDER``."""

    language = Language.BLENDER
    entry_globs: tuple[str, ...] = (ENTRY_REL, "src/parts/*.py")

    def __init__(self, *, blender: str | None = None, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._blender = blender

    # ------------------------------------------------------------------ helpers
    def blender_binary(self) -> str:
        b = self._blender or self._settings.resolve_blender()
        if not b or not Path(b).exists():
            raise BlenderNotFoundError("Blender binary not found; set CV3D_BINARIES__BLENDER=/path/to/blender")
        return b

    def entry_file(self, ws: Workspace) -> Path:
        return ws.root / ENTRY_REL

    @staticmethod
    def file_for_part(part_name: str) -> str:
        """Workspace-relative file that owns a plan part: ``src/parts/<snake>.py``
        (the tracks call this via ``getattr`` to fan out per-part refinement)."""
        return part_file_rel(part_name)

    def part_file(self, ws: Workspace, part_name: str) -> Path:
        return ws.root / self.file_for_part(part_name)

    @staticmethod
    def file_for_target(target: str) -> list[str]:
        """Refine target → files: whole-object targets (``overall``/``assembly``/``object``/'')
        map to the entry ``src/model.py``; anything else is treated as a part name."""
        if target.strip().lower() in ("", "overall", "assembly", "object", "model"):
            return [ENTRY_REL]
        return [part_file_rel(target)]

    def build_command(
        self, ws: Workspace, *, stl: bool = True, blend: bool = False, seed: int = 0,
        tri_limit: int = MAX_TRIS_OBJECT, rlimit_gb: float | None = None,
    ) -> list[str]:
        cmd = [
            self.blender_binary(), "-b", "--factory-startup", "--python", str(WRAPPER), "--",
            "--script", str(self.entry_file(ws)), "--out", str(ws.artifacts),
            "--rlimit-gb", str(rlimit_gb if rlimit_gb is not None else self._settings.limits.bpy_rlimit_gb),
            "--tri-limit", str(tri_limit), "--seed", str(seed),
        ]
        if stl:
            cmd.append("--stl")
        if blend:
            cmd.append("--blend")
        return cmd

    # ------------------------------------------------------------------ protocol
    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        if not isinstance(plan, StaticPlan):  # ArticulatedPlan is a StaticPlan subclass → allowed
            raise TypeError(f"BlenderRuntime.skeleton needs a StaticPlan/ArticulatedPlan, got {type(plan).__name__}")
        return write_blender_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        """Lint every python file under ``src/`` + the multi-file layout rules."""
        return lint_workspace(ws)

    def build(
        self, ws: Workspace, *, timeout_s: int | None = None, stl: bool = True, blend: bool = False,
        seed: int = 0, tri_limit: int = MAX_TRIS_OBJECT,
    ) -> BuildResult:
        """Run the wrapper; never raises for agent-code failures (typed BuildResult instead)."""
        entry = self.entry_file(ws)
        if not entry.is_file():
            return BuildResult(ok=False, language=self.language.value, error_type="MissingEntryFile",
                               error_message=f"{ENTRY_REL} does not exist", error_file=ENTRY_REL)
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        build_json = ws.artifacts / "build.json"
        census_json = ws.artifacts / "census.json"
        glb = ws.artifacts / "object.glb"
        stl_path = ws.artifacts / "object.stl"
        blend_path = ws.artifacts / "object.blend"
        remove_stale(build_json, census_json, glb, stl_path, blend_path)
        cmd = self.build_command(ws, stl=stl, blend=blend, seed=seed, tri_limit=tri_limit)
        proc = run_subprocess(
            cmd, cwd=ws.root, env=blender_env(),
            timeout_s=timeout_s or self._settings.limits.build_timeout_s,
        )
        return compose_build_result(
            language=self.language.value, proc=proc, build_json=build_json, census_json=census_json,
            glb_path=glb, extra_paths={"stl": stl_path, "blend": blend_path}, output_filter=strip_blender_noise,
        )

    def contract_doc(self) -> str:
        p = PROMPTS_DIR / "blender" / "contract.md"
        if p.is_file():
            return p.read_text()
        return (_PKG_DIR / "CONTRACT.md").read_text()

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / "blender" / "cookbook.md"
