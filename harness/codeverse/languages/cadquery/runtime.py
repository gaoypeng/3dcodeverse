"""CadQueryRuntime: lint → build (python subprocess running ``wrappers/run_cq.py``) → BuildResult.

The wrapper needs ``cadquery`` + ``trimesh`` importable by the interpreter it runs
under (``sys.executable`` by default; override with ``python`` for a dedicated env).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import Language
from codeverse.contracts.plan import Plan, StaticPlan
from codeverse.conventions import MAX_TRIS_OBJECT
from codeverse.languages._common import compose_build_result, remove_stale, run_subprocess
from codeverse.languages.cadquery.lint import lint_cadquery_file
from codeverse.languages.cadquery.skeleton import write_cadquery_skeleton
from codeverse.prompts import PROMPTS_DIR
from codeverse.workspace import Workspace

_PKG_DIR = Path(__file__).resolve().parent
WRAPPER = _PKG_DIR / "wrappers" / "run_cq.py"


def cadquery_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONSTARTUP", None)
    env.setdefault("OMP_NUM_THREADS", "4")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


class CadQueryRuntime:
    """LanguageRuntime for ``Language.CADQUERY``."""

    language = Language.CADQUERY
    entry_globs: tuple[str, ...] = ("src/model.py",)

    def __init__(self, *, python: str | None = None, settings: Settings | None = None, rlimit_gb: float = 8.0):
        self._settings = settings or get_settings()
        self._python = python or sys.executable
        self._rlimit_gb = rlimit_gb

    def entry_file(self, ws: Workspace) -> Path:
        return ws.src / "model.py"

    def build_command(self, ws: Workspace, *, seed: int = 0, tri_limit: int = MAX_TRIS_OBJECT,
                      tolerance: float = 0.001, angular_tolerance: float = 0.15) -> list[str]:
        return [
            self._python, str(WRAPPER), "--script", str(self.entry_file(ws)), "--out", str(ws.artifacts),
            "--rlimit-gb", str(self._rlimit_gb), "--tri-limit", str(tri_limit), "--seed", str(seed),
            "--tolerance", str(tolerance), "--angular-tolerance", str(angular_tolerance),
        ]

    # ------------------------------------------------------------------ protocol
    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        if not isinstance(plan, StaticPlan):
            raise TypeError(f"CadQueryRuntime.skeleton needs a StaticPlan, got {type(plan).__name__}")
        return write_cadquery_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_cadquery_file(self.entry_file(ws))

    def build(self, ws: Workspace, *, timeout_s: int | None = None, seed: int = 0,
              tri_limit: int = MAX_TRIS_OBJECT) -> BuildResult:
        entry = self.entry_file(ws)
        if not entry.is_file():
            return BuildResult(ok=False, language=self.language.value, error_type="MissingEntryFile",
                               error_message="src/model.py does not exist", error_file="src/model.py")
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        build_json, census_json = ws.artifacts / "build.json", ws.artifacts / "census.json"
        glb, step, stl = ws.artifacts / "object.glb", ws.artifacts / "object.step", ws.artifacts / "object.stl"
        remove_stale(build_json, census_json, glb, step, stl)
        proc = run_subprocess(self.build_command(ws, seed=seed, tri_limit=tri_limit), cwd=ws.root, env=cadquery_env(),
                              timeout_s=timeout_s or self._settings.limits.build_timeout_s)
        return compose_build_result(language=self.language.value, proc=proc, build_json=build_json, census_json=census_json,
                                    glb_path=glb, extra_paths={"step": step, "stl": stl})

    def contract_doc(self) -> str:
        p = PROMPTS_DIR / "cadquery" / "contract.md"
        if p.is_file():
            return p.read_text()
        return (_PKG_DIR / "CONTRACT.md").read_text()

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / "cadquery" / "cookbook.md"
