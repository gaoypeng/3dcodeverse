"""``SceneThreeJsRuntime``: the LanguageRuntime for multi-file three.js scenes.

build(ws) = ``probe_scene`` (module loads, shape, cameras, update, census) +
``check_shaders`` (static GLSL audits + GPU compile preflight).  A scene has no
GLB deliverable — ``BuildResult.glb_path`` stays None; the census, probe and
preflight JSONs are in ``extra_paths``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from codeverse.contracts.artifacts import BuildResult, GateReport, Severity
from codeverse.contracts.common import Language
from codeverse.contracts.plan import Plan
from codeverse.languages.scene_threejs.lint import lint as _lint
from codeverse.languages.scene_threejs.skeleton import write_skeleton
from codeverse.workspace import Workspace

_HERE = Path(__file__).resolve().parent
_PROMPTS = _HERE.parent.parent / "prompts" / "scene_threejs"


class SceneThreeJsRuntime:
    language = Language.SCENE_THREEJS
    entry_globs: tuple[str, ...] = ("src/scene.js", "src/zones/*.js", "src/assets/*.js", "src/env.js", "src/shaders/*.js")

    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return _lint(ws)

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Probe + shader preflight; ok iff the module loads and no shader errors."""
        from codeverse.config import get_settings
        from codeverse.spatial.probes import check_shaders, probe_scene

        t0 = time.time()
        tmo = float(timeout_s or get_settings().limits.build_timeout_s)
        probe, census = probe_scene(ws, timeout_s=min(tmo, 120.0))
        shaders = check_shaders(ws, timeout_s=min(tmo, 120.0)) if probe.passed or _module_loaded(probe) else GateReport(
            gate="shader_preflight", passed=False, findings=[])
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        gates_dir = ws.artifacts / "gates"
        gates_dir.mkdir(exist_ok=True)
        (gates_dir / "scene_probe.json").write_text(probe.model_dump_json(indent=1))
        (gates_dir / "shader_preflight.json").write_text(shaders.model_dump_json(indent=1))
        errors = sorted(probe.errors + shaders.errors, key=lambda f: 0 if _target_line(f.target) else 1)
        first = errors[0] if errors else None
        res = BuildResult(
            ok=probe.passed and shaders.passed,
            language=self.language.value,
            glb_path=None,
            extra_paths={
                "census": str(ws.artifacts / "census.json"),
                "scene_probe": str(gates_dir / "scene_probe.json"),
                "shader_preflight": str(gates_dir / "shader_preflight.json"),
            },
            stdout_tail=_summary_text(probe, shaders),
            error_type=(first.data.get("kind") or first.gate) if first else "",
            error_message=first.message if first else "",
            error_file=_target_file(first.target) if first else "",
            error_line=_target_line(first.target) if first else None,
            duration_ms=int((time.time() - t0) * 1000),
            census=census,
        )
        (ws.artifacts / "build.json").write_text(json.dumps(res.model_dump(mode="json"), indent=1))
        return res

    def contract_doc(self) -> str:
        p = _PROMPTS / "contract.md"
        if p.is_file():
            return p.read_text()
        return (_HERE / "CONTRACT.md").read_text()

    def cookbook_path(self) -> Path:
        return _PROMPTS / "cookbook.md"


def _module_loaded(probe: GateReport) -> bool:
    """Run the shader preflight even when the probe failed for non-boot reasons."""
    return not any(f.data.get("stage") for f in probe.findings if f.severity == Severity.ERROR)


def _summary_text(probe: GateReport, shaders: GateReport) -> str:
    lines = [f"scene_probe: {'ok' if probe.passed else 'FAILED'} ({len(probe.errors)} errors)"]
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in probe.findings if f.severity != Severity.INFO][:12]
    lines.append(f"shader_preflight: {'ok' if shaders.passed else 'FAILED'} ({len(shaders.errors)} errors)")
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in shaders.findings if f.severity != Severity.INFO][:12]
    return "\n".join(lines)


def _target_file(target: str | None) -> str:
    if not target:
        return ""
    head, sep, tail = target.rpartition(":")
    return head if sep and tail.isdigit() else target


def _target_line(target: str | None) -> int | None:
    if not target:
        return None
    _, sep, tail = target.rpartition(":")
    return int(tail) if sep and tail.isdigit() else None
