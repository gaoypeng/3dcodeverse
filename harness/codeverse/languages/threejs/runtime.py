"""ThreeJsRuntime: skeleton → lint → build (node GLTFExporter) for static objects."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from codeverse.config import get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.plan import Plan
from codeverse.conventions import to_snake
from codeverse.languages.threejs.contract import CONTRACT_FALLBACK
from codeverse.languages.threejs.lint import lint_workspace
from codeverse.languages.threejs.skeleton import write_skeleton
from codeverse.languages.threejs.templates import PACKAGE_JSON
from codeverse.prompts import PROMPTS_DIR, load_text
from codeverse.spatial.node import NodeError, NodeResult, run_node, runtime_js_dir
from codeverse.workspace import Workspace

ENTRY = "src/object.js"
GLB_NAME = "object.glb"
CENSUS_NAME = "census.json"
BUILD_JSON = "build.json"
NODE_MEM_LIMIT_GB = 8.0  # RLIMIT_AS for the export process (geometry-bomb protection)


class ThreeJsRuntime:
    """LanguageRuntime for ``Language.THREEJS`` (raw ESM three.js, exported via node)."""

    language = Language.THREEJS
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.THREEJS], "src/parts/*.js")

    # ------------------------------------------------------------------ contract
    def contract_doc(self) -> str:
        try:
            return load_text("threejs/contract.md")
        except FileNotFoundError:
            return CONTRACT_FALLBACK

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / "threejs" / "cookbook.md"

    # ------------------------------------------------------------------ skeleton / lint
    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    # ------------------------------------------------------------------ build
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Run ``runtime_js/export_glb.mjs`` on ``src/object.js`` → ``artifacts/object.glb``.

        Never raises for agent-code failures: they come back as ``ok=False`` with
        ``error_type/message/file/line`` and the stdout/stderr tails.  Harness
        misconfiguration (missing node, missing script) still raises.
        """
        settings = get_settings()
        timeout_s = timeout_s or settings.limits.build_timeout_s
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        glb = ws.artifacts / GLB_NAME
        census = ws.artifacts / CENSUS_NAME
        for stale in (glb, census, ws.artifacts / "export_error.json"):
            stale.unlink(missing_ok=True)
        self._ensure_module_type(ws)

        t0 = time.time()
        args = ["--ws", str(ws.root), "--entry", ENTRY, "--out", str(glb), "--census", str(census)]
        try:
            res = run_node(
                runtime_js_dir() / "export_glb.mjs", args, cwd=ws.root, timeout_s=timeout_s,
                three_hook=True, node_args=["--max-old-space-size=4096"], check=False,
                mem_limit_gb=NODE_MEM_LIMIT_GB,
            )
        except NodeError as e:
            if e.result is None:
                raise  # harness problem (no node binary / script)
            result = self._failure(e.result, "Timeout" if e.result.timed_out else "NodeError", str(e))
            self._write_build_json(ws, result)
            return result

        if res.rc != 0 or not (res.last_json or {}).get("ok") or not glb.is_file():
            result = self._from_error_record(res, ws)
        else:
            census_data: dict[str, Any] = {}
            if census.is_file():
                census_data = json.loads(census.read_text())
            result = BuildResult(
                ok=True, language=self.language.value, glb_path=str(glb),
                stdout_tail=res.stdout_tail, stderr_tail=res.stderr_tail,
                duration_ms=int((time.time() - t0) * 1000), census=census_data,
                extra_paths={"census": str(census)},
            )
        self._write_build_json(ws, result)
        return result

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _ensure_module_type(ws: Workspace) -> None:
        """``src/package.json`` {type: module} so ``.js`` files are parsed as ESM."""
        ws.src.mkdir(parents=True, exist_ok=True)
        pj = ws.src / "package.json"
        if not pj.is_file():
            pj.write_text(PACKAGE_JSON)

    def _failure(self, res: NodeResult, etype: str, message: str) -> BuildResult:
        return BuildResult(
            ok=False, language=self.language.value, stdout_tail=res.stdout_tail, stderr_tail=res.stderr_tail,
            error_type=etype, error_message=message[:2000], duration_ms=res.duration_ms,
        )

    def _from_error_record(self, res: NodeResult, ws: Workspace | None = None) -> BuildResult:
        rec = res.last_json or {}
        err = rec.get("error") if isinstance(rec.get("error"), dict) else None
        if err is None:
            tail = res.stderr_tail.strip().splitlines()
            return self._failure(res, "ExportError", tail[-1] if tail else f"export_glb exited {res.rc} without an error record")
        result = self._failure(res, str(err.get("type", "Error")), str(err.get("message", "")))
        result.error_file = str(err.get("file", "") or self._part_file(ws, err.get("part")) or ("" if err.get("frames") else ENTRY))
        result.error_line = err.get("line") if isinstance(err.get("line"), int) else None
        result.census = {"frames": err.get("frames", []), "stack": err.get("stack", ""), "part": str(err.get("part", "") or "")}
        return result

    @staticmethod
    def _part_file(ws: Workspace | None, part: object) -> str:
        """``src/parts/<snake>.js`` for the plan part named in a validation error (when that file exists).

        Contract errors raised by the exporter (NaN geometry, empty bbox) carry no src
        frame — the throw site is export_glb.mjs — but name the offending part; the
        naming convention (``conventions.to_snake``) turns that into the file to repair.
        """
        if ws is None or not isinstance(part, str) or not part.strip():
            return ""
        rel = Path("src") / "parts" / f"{to_snake(part)}.js"
        return rel.as_posix() if (ws.root / rel).is_file() else ""

    @staticmethod
    def _write_build_json(ws: Workspace, result: BuildResult) -> None:
        ws.write_json(ws.artifacts / BUILD_JSON, result)
