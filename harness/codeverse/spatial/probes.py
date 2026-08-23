"""Scene probes: import/census gate, shader compile preflight, shader presence.

* ``probe_scene(ws)``    → (GateReport 'scene_probe', census)  — the scene build gate
* ``check_shaders(ws)``  → GateReport 'shader_preflight' (file:line + fix hints)
* ``shader_presence(ws)``→ Observation: counterfactual render (custom shaders
  stripped) vs normal; pixel-diff fraction proves the effect is in the frame.

All three drive the node host (``runtime_js/*.mjs``) via ``run_scene_script``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.contracts.plan import CameraPlan
from codeverse.conventions import MAX_TRIS_SCENE
from codeverse.spatial.registry import Observation
from codeverse.spatial.render_scene import (
    SceneRenderError,
    read_metrics,
    render_scene,
    run_scene_script,
)
from codeverse.workspace import Workspace

PROBE_GATE = "scene_probe"
SHADER_GATE = "shader_preflight"

_BOOT_HINTS = {
    "import": "src/scene.js failed to import: fix the syntax/import error quoted above (imports must be 'three', 'three/addons/*' or relative paths that exist)",
    "createScene": "createScene({THREE, renderer, loaders}) threw or returned the wrong shape; it must return {scene: THREE.Scene, cameras: [...], update(t, dt)}",
    "loads": "an asset failed to load; check the '/assets/<name>.glb' path exists under public/assets",
    "first_update": "update(0, 0) threw; guard against undefined handles before animating",
}


class SceneProbeResult(BaseModel):
    """``probe_scene`` result: unpacks as ``gate, census = probe_scene(ws)`` and also
    dumps to a dict generic tool adapters can read.  Tool semantics: ``ok`` /
    ``errors`` describe whether the PROBE TOOL ran (``is_error`` in MCP terms) —
    a gate that fails on agent-fixable findings keeps ``ok=True`` with the
    finding lines under ``findings``; ``errors`` holds only harness/driver
    failures.  Gate truth stays on ``gate.passed``."""

    gate: GateReport
    census: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list, description="harness/driver failures only (tool could not run)")
    findings: list[str] = Field(default_factory=list, description="agent-fixable gate findings, one line each")
    ok: bool = False

    def __iter__(self):  # type: ignore[override]
        yield self.gate
        yield self.census


def _result(gate: GateReport, census: dict[str, Any], *, driver_failure: str = "") -> SceneProbeResult:
    lines = [f"[{f.severity.value}] {f.target or ''}: {f.message}" for f in gate.findings
             if f.severity != Severity.INFO and not f.data.get("harness_failure")]
    return SceneProbeResult(gate=gate, census=census, ok=not driver_failure,
                            errors=[driver_failure] if driver_failure else [], findings=lines)


def _f(gate: str, sev: Severity, msg: str, *, target: str | None = None, hint: str = "", **data: Any) -> GateFinding:
    return GateFinding(gate=gate, severity=sev, target=target, message=msg, fix_hint=hint, data=data)


def probe_scene(ws: Workspace, *, timeout_s: float = 60.0, write_census: bool = True) -> SceneProbeResult:
    """Import-only probe: module loads, shape valid, cameras valid, update runs, census."""
    t0 = time.time()
    gate = PROBE_GATE
    findings: list[GateFinding] = []
    census: dict[str, Any] = {}
    out_json = ws.artifacts / "scene_probe.json"
    try:
        res = run_scene_script(
            "probe_scene.mjs", ["--ws", str(ws.root), "--out", str(out_json), "--timeout-ms", str(int(timeout_s * 1000))],
            timeout_s=timeout_s + 20,
        )
    except SceneRenderError as e:
        findings.append(_f(gate, Severity.ERROR, f"scene probe could not run: {e}", target="src/scene.js",
                           hint="this is a harness/driver failure, not your code; retry or report", harness_failure=True))
        return _result(GateReport(gate=gate, passed=False, findings=findings, duration_ms=int((time.time() - t0) * 1000)), census,
                       driver_failure=f"scene probe could not run: {e}"[:800])
    report, census = probe_report(res.summary, duration_ms=int((time.time() - t0) * 1000))
    if write_census and census:
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        (ws.artifacts / "census.json").write_text(json.dumps(census, indent=1))
    return _result(report, census)


def probe_report(summary: dict[str, Any], *, duration_ms: int = 0) -> tuple[GateReport, dict[str, Any]]:
    """Interpret a ``probe_scene.mjs`` summary into the ``scene_probe`` GateReport
    (+ census).  Pure over the driver JSON, so the combined single-boot build
    (``probe_scene.mjs --compile``, scene_threejs runtime) reuses it."""
    gate = PROBE_GATE
    findings: list[GateFinding] = []
    s = summary
    boot = s.get("boot") or {}
    if not boot.get("ok"):
        stage = boot.get("stage", "?")
        msg = boot.get("error") or "scene did not boot"
        if "Failed to fetch dynamically imported module" in msg:
            missing = [e for e in s.get("console_errors", []) if "404 not found" in e or "request failed" in e]
            if missing:
                msg += " — " + "; ".join(missing[:4])
        findings.append(_f(gate, Severity.ERROR, f"[{stage}] {msg}"[:1500], target="src/scene.js",
                           hint=_BOOT_HINTS.get(stage, "fix the quoted error"), stage=stage))
    for p in boot.get("camera_problems", []):
        sev = Severity.ERROR if ("no valid cameras" in p or "not an array" in p or "must be" in p) else Severity.WARN
        findings.append(_f(gate, sev, f"cameras: {p}", target="src/scene.js",
                           hint="cameras: [{name:'overview', position:[x,y,z], lookAt:[x,y,z], fov:50}, ...] (1-6 entries)"))
    if boot.get("ok") and s.get("update_ok") is False:
        findings.append(_f(gate, Severity.ERROR, f"update(t, dt) threw: {s.get('update_error', '')[:800]}", target="src/scene.js",
                           hint="update must run every frame without throwing; guard optional handles"))
    for e in s.get("console_errors", []):
        if e.startswith("boot["):
            continue
        if e.startswith("update(t=") and s.get("update_ok") is False:
            continue   # already reported as the update(t, dt) finding above
        findings.append(_f(gate, Severity.ERROR, f"console: {e}"[:1000], target="src/scene.js",
                           hint="the browser console must stay clean: fix the quoted error"))
    for w in s.get("console_warnings", []):
        if "404 not found" in w or w.startswith("load: "):
            findings.append(_f(gate, Severity.WARN, w[:500], target="public/assets",
                               hint="compile the Blender asset to public/assets/<name>.glb (the scene renders without it until then)"))
    for e in s.get("shader_errors", []):
        findings.append(_f(gate, Severity.ERROR, f"shader {e.get('stage')}: {e.get('message')} at: {e.get('source_line', '')}",
                           target=e.get("material") or "shader", hint="run check_shaders for file:line mapping"))
    if boot.get("raf_calls"):
        findings.append(_f(gate, Severity.WARN, f"scene called requestAnimationFrame {boot['raf_calls']}x; animation must live in update(t, dt)",
                           target="src/scene.js", hint="delete your own render loop; the host drives update()"))
    census = s.get("census") or {}
    findings.extend(_census_findings(census))
    cam0 = s.get("first_camera") or {}
    if cam0.get("camera_in_geometry"):
        findings.append(_f(gate, Severity.WARN,
                           f"camera '{cam0.get('name')}' is inside/too close to geometry (nearest hit {cam0.get('nearest_hit_m')} m, inside {cam0.get('inside_mesh_bbox')})",
                           target=str(cam0.get("name")), hint="move the eye ≥ 0.5 m away from surfaces and above ground"))
    if cam0 and cam0.get("dark_frac", 0) > 0.85:
        findings.append(_f(gate, Severity.WARN, f"camera '{cam0.get('name')}' frame is {cam0['dark_frac']:.0%} black — no lights or nothing in view?",
                           target=str(cam0.get("name")), hint="add a DirectionalLight + HemisphereLight in env, aim the camera at the content"))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=gate, passed=passed, findings=findings, duration_ms=duration_ms), census


def _census_findings(c: dict[str, Any]) -> list[GateFinding]:
    gate = PROBE_GATE
    out: list[GateFinding] = []
    if not c:
        return out
    tot = c.get("totals", {})
    if tot.get("lights", 0) == 0:
        out.append(_f(gate, Severity.WARN, "scene has no lights (MeshStandardMaterial renders black)", target="src/env.js",
                      hint="add new THREE.DirectionalLight (castShadow) + new THREE.HemisphereLight in buildEnv"))
    if tot.get("meshes", 0) == 0:
        out.append(_f(gate, Severity.ERROR, "scene has no meshes", target="src/scene.js", hint="add zones to the scene in createScene"))
    if tot.get("triangles", 0) > MAX_TRIS_SCENE:
        out.append(_f(gate, Severity.WARN, f"{tot['triangles']:,} triangles exceeds the {MAX_TRIS_SCENE:,} budget", target="scene",
                      hint="lower segment counts, use InstancedMesh for repeats, merge static geometry"))
    unnamed = [g["name"] for g in c.get("groups", []) if not g.get("named") and g.get("kind") == "content"]
    if unnamed:
        out.append(_f(gate, Severity.WARN, f"{len(unnamed)} top-level content object(s) without a name: {unnamed[:5]}", target="src/scene.js",
                      hint="group each zone in a THREE.Group with group.name = 'ZoneName' (PascalCase)"))
    for o in c.get("overlaps", [])[:5]:
        if o.get("footprint_overlap", 0) >= 0.5:
            out.append(_f(gate, Severity.INFO, f"zones {o['a']} and {o['b']} footprints overlap {o['footprint_overlap']:.0%}",
                          target=o["a"], hint="fine if intended (nested zones); otherwise move one"))
    return out


def check_shaders(ws: Workspace, *, module: str | None = None, timeout_s: float = 90.0) -> GateReport:
    """Static GLSL audits + GPU compile preflight with file:line mapped errors."""
    t0 = time.time()
    gate = SHADER_GATE
    out_json = ws.artifacts / "shader_preflight.json"
    args = ["--ws", str(ws.root), "--out", str(out_json), "--timeout-ms", str(int(timeout_s * 1000))]
    if module:
        args += ["--module", module]
    findings: list[GateFinding] = []
    try:
        res = run_scene_script("check_shaders.mjs", args, timeout_s=timeout_s + 20)
    except SceneRenderError as e:
        findings.append(_f(gate, Severity.ERROR, f"shader preflight could not run: {e}", target="src/scene.js",
                           hint="harness/driver failure; retry or report", harness_failure=True))
        return GateReport(gate=gate, passed=False, findings=findings, duration_ms=int((time.time() - t0) * 1000))
    return shader_report(res.summary, duration_ms=int((time.time() - t0) * 1000))


def shader_report(report: dict[str, Any], *, duration_ms: int = 0) -> GateReport:
    """Interpret a shader-preflight report (``check_shaders.mjs`` summary, or the
    ``shader_report`` block of ``probe_scene.mjs --compile``) into the
    ``shader_preflight`` GateReport.  Pure over the driver JSON."""
    gate = SHADER_GATE
    findings: list[GateFinding] = []
    rep = report
    for e in rep.get("errors", []):
        target = f"{e.get('file', '?')}:{e['line']}" if e.get("line") else str(e.get("file", "?"))
        findings.append(_f(gate, Severity.ERROR, e.get("message", "shader error"), target=target,
                           hint=e.get("fix_hint", ""), kind=e.get("kind"), stage=e.get("stage"), material=e.get("material"),
                           source_line=e.get("source_line"), context=e.get("context")))
    for w in rep.get("warnings", []):
        target = f"{w.get('file', '?')}:{w['line']}" if w.get("line") else str(w.get("file", "?"))
        findings.append(_f(gate, Severity.WARN, w.get("message", ""), target=target, hint=w.get("fix_hint", ""), kind=w.get("kind")))
    comp = rep.get("compile") or {}
    if comp:
        findings.append(_f(gate, Severity.INFO, f"compiled {comp.get('programs')} programs ({comp.get('custom_materials')} custom) in {comp.get('ms')} ms",
                           target="scene", **comp))
    passed = bool(rep.get("ok")) and not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=gate, passed=passed, findings=findings, duration_ms=duration_ms)


def shader_presence(
    ws: Workspace,
    *,
    out_dir: Path | None = None,
    camera: CameraPlan | None = None,
    time_s: float = 0.7,
    width: int = 512,
    height: int = 288,
    threshold: float = 0.01,
) -> Observation:
    """Counterfactual probe: render normally and with custom-shader materials
    stripped; the fraction of changed pixels says whether the effect is visible.
    ``present`` when diff ≥ ``threshold`` (calibrate with fixtures; 0.0 = invisible)."""
    import numpy as np
    from PIL import Image

    out = Path(out_dir) if out_dir else ws.artifacts / "tool_scratch" / "shader_presence"
    rs = render_scene(ws, out, cameras=[camera] if camera else None, orbit=False, times=(time_s,),
                      width=width, height=height, sheet=False, fps_seconds=0, counterfactual=True)
    metrics = read_metrics(out)
    custom = (metrics.get("census") or {}).get("custom_materials", [])
    pairs = [(v, nv) for v in rs.views if "_nocustom" not in v.name for nv in rs.views if nv.name == f"{v.name}_nocustom"]
    if not pairs:
        return Observation.error("shader_presence: no render pairs produced; is the scene booting? " + "; ".join(rs.console_errors[:3]))
    results = []
    images: list[str] = []
    for v, nv in pairs:
        a = np.asarray(Image.open(v.path).convert("RGB")).astype(int)
        b = np.asarray(Image.open(nv.path).convert("RGB")).astype(int)
        frac = float((np.abs(a - b).max(axis=2) > 12).mean())
        results.append({"camera": v.name, "diff_frac": round(frac, 4), "present": frac >= threshold})
        images += [v.path, nv.path]
    best = max(r["diff_frac"] for r in results)
    text = (f"custom-shader materials: {len(custom)} ({', '.join(m.get('on', '?') for m in custom[:6])}); "
            f"pixel change when stripped: " + ", ".join(f"{r['camera']}={r['diff_frac']:.1%}" for r in results)
            + (". PRESENT: the shader changes the frame." if best >= threshold else
               ". NOT VISIBLE: stripping the custom shader changes nothing — it is not in any camera's frame (or is hidden/occluded)."))
    if not custom:
        text = "no custom shader materials found (ShaderMaterial / onBeforeCompile). " + text
    return Observation(ok=True, text=text, numbers={"custom_materials": len(custom), "max_diff_frac": best, "per_camera": results, "threshold": threshold}, images=images)
