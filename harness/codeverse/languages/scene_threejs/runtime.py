"""``SceneThreeJsRuntime``: the LanguageRuntime for multi-file three.js scenes.

build(ws) = ONE ``probe_scene.mjs --compile`` run (single browser boot) that
yields both gates: ``scene_probe`` (module loads, shape, cameras, update,
census) and ``shader_preflight`` (static GLSL audits + GPU compile preflight).
A scene has no GLB deliverable — ``BuildResult.glb_path`` stays None; the
census, probe and preflight JSONs are in ``extra_paths``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from codeverse.contracts.artifacts import BuildResult, GateReport, Severity
from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.plan import Plan
from codeverse.languages.scene_threejs.lint import lint as _lint
from codeverse.languages.scene_threejs.skeleton import write_skeleton
from codeverse.workspace import Workspace

_HERE = Path(__file__).resolve().parent
_PROMPTS = _HERE.parent.parent / "prompts" / "scene_threejs"


class SceneThreeJsRuntime:
    language = Language.SCENE_THREEJS
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.SCENE_THREEJS], "src/zones/*.js", "src/assets/*.js", "src/env.js", "src/shaders/*.js")

    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return _lint(ws)

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Probe + shader preflight (one browser boot); ok iff the module loads
        and no shader errors.  External contract unchanged: the same two
        GateReports land under ``artifacts/gates/``."""
        from codeverse.config import get_settings

        t0 = time.time()
        tmo = min(float(timeout_s or get_settings().limits.build_timeout_s), 120.0)
        probe, shaders, census = _probe_and_preflight(ws, timeout_s=tmo)
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        if census:
            (ws.artifacts / "census.json").write_text(json.dumps(census, indent=1))
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


def _probe_and_preflight(ws: Workspace, *, timeout_s: float) -> tuple[GateReport, GateReport, dict]:
    """One ``probe_scene.mjs --compile`` run → (scene_probe, shader_preflight, census).

    The driver boots the scene once and runs both stages on the same page;
    when the scene never boots the preflight is skipped, matching the old
    two-call behaviour (a failed-empty shader gate).
    """
    from codeverse.contracts.artifacts import GateFinding
    from codeverse.spatial.probes import PROBE_GATE, SHADER_GATE, probe_report, shader_report
    from codeverse.spatial.render_scene import SceneRenderError, run_scene_script

    t0 = time.time()
    args = [
        "--ws", str(ws.root), "--compile",
        "--out", str(ws.artifacts / "scene_probe.json"),
        "--shaders-out", str(ws.artifacts / "shader_preflight.json"),
        "--timeout-ms", str(int(timeout_s * 1000)),
    ]
    try:
        res = run_scene_script("probe_scene.mjs", args, timeout_s=timeout_s + 20)
    except SceneRenderError as e:
        dur = int((time.time() - t0) * 1000)
        finding = GateFinding(gate=PROBE_GATE, severity=Severity.ERROR, target="src/scene.js",
                              message=f"scene probe could not run: {e}"[:1500],
                              fix_hint="this is a harness/driver failure, not your code; retry or report",
                              data={"harness_failure": True})
        return (GateReport(gate=PROBE_GATE, passed=False, findings=[finding], duration_ms=dur),
                GateReport(gate=SHADER_GATE, passed=False, findings=[]), {})
    dur = int((time.time() - t0) * 1000)
    probe, census = probe_report(res.summary, duration_ms=dur)
    rep = res.summary.get("shader_report") or {}
    if not rep or rep.get("skipped"):
        shaders = GateReport(gate=SHADER_GATE, passed=False, findings=[])
    else:
        shaders = shader_report(rep, duration_ms=int(rep.get("duration_ms") or 0))
    return probe, shaders, census


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
