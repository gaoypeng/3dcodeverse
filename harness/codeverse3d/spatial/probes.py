"""Scene probes: the import/census gate and the shader-preflight report reader."""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.conventions import DRAWS_WARN_SCENE, MAX_DRAWS_SCENE, MAX_TRIS_SCENE
from codeverse3d.spatial.render_scene import SceneRenderError, probe_env_args, run_scene_script
from codeverse3d.workspace import Workspace

PROBE_GATE = "scene_probe"
SHADER_GATE = "shader_preflight"

_BOOT_HINTS = {
    "import": "src/scene.js failed to import: fix the syntax/import error quoted above (imports must be 'three', 'three/addons/*' or relative paths that exist)",
    "createScene": "createScene({THREE, renderer, loaders}) threw or returned the wrong shape; it must return {scene: THREE.Scene, cameras: [...], update(t, dt)}",
    "loads": "an asset failed to load; check the '/assets/<name>.glb' path exists under public/assets",
    "first_update": "update(0, 0) threw; guard against undefined handles before animating",
}


class SceneProbeResult(BaseModel):
    """``probe_scene`` result."""

    gate: GateReport
    census: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list, description="harness/driver failures only (tool could not run)")
    findings: list[str] = Field(default_factory=list, description="agent-fixable gate findings, one line each")
    ok: bool = False

    def __iter__(self):  # type: ignore[override]
        yield self.gate
        yield self.census


def probe_result(gate: GateReport, census: dict[str, Any]) -> SceneProbeResult:
    """A ``harness_failure`` finding IS a driver failure, wherever it was raised."""
    errors = [f.message[:800] for f in gate.findings if f.data.get("harness_failure")]
    lines = [f"[{f.severity.value}] {f.target or ''}: {f.message}" for f in gate.findings
             if f.severity != Severity.INFO and not f.data.get("harness_failure")]
    return SceneProbeResult(gate=gate, census=census, ok=not errors, errors=errors, findings=lines)


def _f(gate: str, sev: Severity, msg: str, *, target: str | None = None, hint: str = "", **data: Any) -> GateFinding:
    return GateFinding(gate=gate, severity=sev, target=target, message=msg, fix_hint=hint, data=data)


def run_probe(ws: Workspace, *, compile: bool = False, timeout_s: float | None = None,
              glb: Path | None = None, facts: Mapping[str, Any] | None = None,
              ) -> tuple[GateReport, GateReport, dict[str, Any]]:
    """One ``probe_scene.mjs`` boot → (scene_probe, shader_preflight, census)."""
    t0 = time.time()
    timeout_s = min(float(timeout_s or get_settings().limits.build_timeout_s), 120.0)
    args = ["--ws", str(ws.root), "--out", str(ws.artifacts / "scene_probe.json"), "--timeout-ms", str(int(timeout_s * 1000))]
    if compile:
        args += ["--compile", "--shaders-out", str(ws.artifacts / "shader_preflight.json")]
    if glb is not None:
        args += ["--glb", str(Path(glb).resolve()), "--no-settle"]
    else:
        args += probe_env_args()
    no_preflight = GateReport(gate=SHADER_GATE, passed=False, findings=[])
    try:
        res = run_scene_script("probe_scene.mjs", args, timeout_s=timeout_s + 20)
    except SceneRenderError as e:
        if e.scene_timed_out and glb is None:
            finding = timeout_finding(timeout_s, phase=e.phase)   # after run_scene_script's one retry
        else:
            finding = _f(PROBE_GATE, Severity.ERROR, f"scene probe could not run: {e}"[:1500], target="src/scene.js",
                         hint="this is a harness/driver failure, not your code; retry or report", harness_failure=True)
        return GateReport.of(PROBE_GATE, [finding], duration_ms=int((time.time() - t0) * 1000)), no_preflight, {}
    if glb is not None:
        probe, census = offline_report(res.summary, facts or {}, duration_ms=int((time.time() - t0) * 1000))
        return probe, no_preflight, census
    probe, census = probe_report(res.summary, duration_ms=int((time.time() - t0) * 1000))
    rep = res.summary.get("shader_report") or {}
    if not compile or not rep or rep.get("skipped"):
        return probe, no_preflight, census
    return probe, shader_report(rep, duration_ms=int(rep.get("duration_ms") or 0)), census


def timeout_finding(timeout_s: float, *, phase: str) -> GateFinding:
    """A probe whose watchdog fired inside the scene's own code (``render_scene.SCENE_PHASES``) —
    after the driver's one retry, on a browser of its own — is an ERROR against the CODE."""
    entry = ENTRY_FILE[Language.SCENE_THREEJS]
    what = {"scene": f"importing {entry} and createScene()", "census": "the census of what createScene() built",
            "update": "update(t, dt)"}.get(phase, "the scene's boot")
    return _f(PROBE_GATE, Severity.ERROR,
              f"scene construction exceeded the {timeout_s:.0f} s probe budget: {what} did not finish",
              target=entry, kind="probe_timeout", timed_out=True, phase=phase,
              hint=f"the scene must boot inside {timeout_s:.0f} s; the time is the scene's own construction work "
                   "(module-level code, createScene, update) — find what grows with the scene's size and bound it")


def probe_scene(ws: Workspace, *, timeout_s: float | None = None) -> SceneProbeResult:
    """Import-only probe."""
    report, _, census = run_probe(ws, timeout_s=timeout_s)
    if census:
        ws.write_json(ws.artifacts / "census.json", census)
    return probe_result(report, census)


def probe_report(summary: dict[str, Any], *, duration_ms: int = 0) -> tuple[GateReport, dict[str, Any]]:
    """Interpret a ``probe_scene.mjs`` summary into the ``scene_probe`` GateReport (+ census)."""
    gate = PROBE_GATE
    findings: list[GateFinding] = []
    s = summary
    boot = s.get("boot") or {}
    if "boot" not in s:
        findings.append(_f(gate, Severity.ERROR, "scene probe produced no result (driver output lost)",
                           target="src/scene.js",
                           hint="this is a harness/driver failure, not your code; retry or report",
                           harness_failure=True))
        return GateReport.of(gate, findings, duration_ms=duration_ms), {}
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
        findings.append(_f(gate, Severity(p["severity"]), f"cameras: {p['text']}", target="src/scene.js",
                           hint="cameras: [{name:'overview', position:[x,y,z], lookAt:[x,y,z], fov:50}, ...] (1-6 entries)"))
    if boot.get("ok") and s.get("update_ok") is False:
        findings.append(_f(gate, Severity.ERROR, f"update(t, dt) threw: {s.get('update_error', '')[:800]}", target="src/scene.js",
                           hint="update must run every frame without throwing; guard optional handles"))
    for e in s.get("console_errors", []):
        if e.startswith("boot["):
            continue
        if e.startswith("update(t=") and s.get("update_ok") is False:
            continue
        findings.append(_f(gate, Severity.ERROR, f"console: {e}"[:1000], target="src/scene.js",
                           hint="the browser console must stay clean: fix the quoted error"))
    for w in s.get("console_warnings", []):
        if "404 not found" in w or w.startswith("load: "):
            findings.append(_f(gate, Severity.WARN, w[:500], target="public/assets",
                               hint="compile the Blender asset to public/assets/<name>.glb (the scene renders without it until then)"))
    for e in s.get("shader_errors", []):
        findings.append(_f(gate, Severity.ERROR, f"shader {e.get('stage')}: {e.get('message')} at: {e.get('source_line', '')}",
                           target=e.get("material") or "shader", hint="run shader_probe for the file:line mapping"))
    if boot.get("raf_calls"):
        findings.append(_f(gate, Severity.WARN, f"scene called requestAnimationFrame {boot['raf_calls']}x; animation must live in update(t, dt)",
                           target="src/scene.js", hint="delete your own render loop; the host drives update()"))
    census = s.get("census") or {}
    findings.extend(_census_findings(census))
    return GateReport.of(gate, findings, duration_ms=duration_ms), census


FACT_KEYS = ("fog", "background", "environment", "light_types")


def merge_facts(census: dict[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """The GLB census with the bpy facts in the keys the gates already read (``fog``,
    ``background``, ``totals.lights`` …) and the rest under ``bpy``."""
    if not census:
        return {}
    out = {**census, **{k: facts[k] for k in FACT_KEYS if k in facts}}
    out["totals"] = {**(census.get("totals") or {}), "lights": int(facts.get("lights") or 0)}
    out["bpy"] = {k: v for k, v in facts.items() if k not in FACT_KEYS and k != "lights"}
    return out


def offline_report(summary: dict[str, Any], facts: Mapping[str, Any], *, duration_ms: int = 0) -> tuple[GateReport, dict[str, Any]]:
    """The ``scene_probe`` gate of an offline scene over its census-GLB probe."""
    boot = summary.get("boot") or {}
    fail = [e for e in summary.get("console_errors", []) if not e.startswith("boot[")]
    if not boot.get("ok"):
        fail.insert(0, f"[{boot.get('stage', '?')}] {boot.get('error') or 'no probe result (driver output lost)'}")
    if fail:
        finding = _f(PROBE_GATE, Severity.ERROR, f"the census GLB did not load cleanly in the probe host: {fail[0]}"[:1500],
                     target="artifacts/census.glb", hint="this is a harness failure, not your code; retry or report",
                     harness_failure=True)
        return GateReport.of(PROBE_GATE, [finding], duration_ms=duration_ms), {}
    findings = [_f(PROBE_GATE, Severity(p["severity"]), f"cameras: {p['text']}", target="src/scene.py",
                   hint="the plan's cameras become src/scene.py CAMERAS at assembly")
                for p in boot.get("camera_problems", [])]
    census = merge_facts(summary.get("census") or {}, facts)
    findings.extend(_census_findings(census, offline=True))
    return GateReport.of(PROBE_GATE, findings, duration_ms=duration_ms), census


def _offline_findings(c: dict[str, Any]) -> list[GateFinding]:
    """What only an offline scene can get wrong."""
    bpy = c.get("bpy") or {}
    out: list[GateFinding] = []
    unique = int(bpy.get("unique_tris") or 0)
    if unique > MAX_TRIS_SCENE:
        out.append(_f(PROBE_GATE, Severity.ERROR,
                      f"{unique:,} unique triangles (instanced copies not counted) exceed the {MAX_TRIS_SCENE:,} budget",
                      target="scene", kind="unique_tris", unique_tris=unique, limit=MAX_TRIS_SCENE,
                      hint="place repeats as collection instances or geometry-nodes instances instead of copies, and lower "
                           "subdivision levels / segment counts on large meshes"))
    for d in (bpy.get("drivers_invalid") or [])[:8]:
        why = "runs Python, which the harness never executes (it evaluates to 0)" if d.get("python") else "is invalid"
        out.append(_f(PROBE_GATE, Severity.ERROR,
                      f"driver {d.get('id')} {d.get('path')}[{d.get('index')}] = {str(d.get('expression'))[:120]!r} {why}",
                      target=str(d.get("id") or "scene"), kind="python_driver",
                      hint="use keyframes, or a simple expression of `frame` (sin/cos/min/max/clamp/lerp, + - * / and "
                           "comparisons, no attribute access, no ** or %)"))
    for h in bpy.get("handlers") or []:
        out.append(_f(PROBE_GATE, Severity.ERROR, f"bpy.app.handlers.{h} is set: the deliverable .blend would depend on "
                      "code that does not travel with it", target="scene", kind="app_handler",
                      hint="animate with keyframes, simple drivers or the geometry-nodes Scene Time node"))
    return out


def _census_findings(c: dict[str, Any], *, offline: bool = False) -> list[GateFinding]:
    gate = PROBE_GATE
    out: list[GateFinding] = []
    if not c:
        return out
    hook_audit = c.get("animation_hooks") or {}
    for hook in hook_audit.get("unobserved", [])[:12]:
        out.append(_f(
            gate, Severity.WARN,
            f"animation hook {hook.get('path', '?')} was not observed during "
            f"update sampling ({hook_audit.get('from_time', 0):g}–{hook_audit.get('to_time', 0):g} s)",
            target=hook.get("zone") or "src/scene.js",
            hint="verify the parent zone forwards update(t, dt) to this asset; captured callback references "
                 "or delayed animation can evade this observation, so inspect before changing code",
            kind="unobserved_animation_hook", hook_path=hook.get("path"),
        ))
    tot = c.get("totals", {})
    if (dark := unlit(c, offline=offline)) is not None:
        out.append(dark)
    if offline:
        if tot.get("meshes", 0) == 0:
            out.append(_f(gate, Severity.ERROR, "scene has no meshes", target="src/scene.py",
                          hint="every zone's build(ctx) links its objects into ctx.collection"))
        out.extend(_offline_findings(c))
    else:
        if tot.get("lights", 0) == 0 and dark is None:
            out.append(_f(gate, Severity.WARN, "scene has no lights (MeshStandardMaterial renders black)", target="src/env.js",
                          hint="add new THREE.DirectionalLight (castShadow) + new THREE.HemisphereLight in buildEnv"))
        if tot.get("meshes", 0) == 0:
            out.append(_f(gate, Severity.ERROR, "scene has no meshes", target="src/scene.js", hint="add zones to the scene in createScene"))
        if tot.get("triangles", 0) > MAX_TRIS_SCENE:
            out.append(_f(gate, Severity.WARN, f"{tot['triangles']:,} triangles exceeds the {MAX_TRIS_SCENE:,} budget", target="scene",
                          hint="lower segment counts, use InstancedMesh for repeats, merge static geometry"))
        out.extend(_draw_findings(c))
    unnamed = [g["name"] for g in c.get("groups", []) if not g.get("named") and g.get("kind") == "content"]
    if unnamed:
        out.append(_f(gate, Severity.WARN, f"{len(unnamed)} top-level content object(s) without a name: {unnamed[:5]}", target="src/scene.js",
                      hint="group each zone in a THREE.Group with group.name = 'ZoneName' (PascalCase)"))
    for o in c.get("overlaps", [])[:5]:
        if o.get("footprint_overlap", 0) >= 0.5:
            out.append(_f(gate, Severity.INFO, f"zones {o['a']} and {o['b']} footprints overlap {o['footprint_overlap']:.0%}",
                          target=o["a"], hint="fine if intended (nested zones); otherwise move one"))
    return out


def unlit(census: Mapping[str, Any], *, offline: bool) -> GateFinding | None:
    """The ERROR for a scene that nothing lights."""
    lights = int((census.get("totals") or {}).get("lights", 0) or 0)
    if lights or census.get("background"):
        return None
    if offline:
        return _f(PROBE_GATE, Severity.ERROR, "scene has no lamps and no world: it renders black", target="src/env.py",
                  kind="unlit", hint="build_env must light the scene: a world (sky or background colour) and/or lamps")
    if census.get("environment"):
        return None
    return _f(PROBE_GATE, Severity.ERROR, "scene has no lights, no scene.background and no scene.environment: it renders black",
              target="src/env.js", kind="unlit",
              hint="buildEnv must light the scene: lights, and the sky as scene.background / scene.environment")


def _draw_findings(c: dict[str, Any]) -> list[GateFinding]:
    """The scene draw budget."""
    draws = int(c.get("totals", {}).get("draws", 0))
    if draws <= DRAWS_WARN_SCENE:
        return []
    sev, limit = (Severity.ERROR, MAX_DRAWS_SCENE) if draws > MAX_DRAWS_SCENE else (Severity.WARN, DRAWS_WARN_SCENE)
    heavy = sorted((g for g in c.get("groups", []) if g.get("draws")), key=lambda g: -g["draws"])[:3]
    where = ", ".join(f"{g['name']} {g['draws']:,}" for g in heavy)
    return [_f(PROBE_GATE, sev,
               f"{draws:,} draw calls per frame (each visible mesh is one) exceed the {limit:,} budget"
               + (f" — most in {where}" if where else "")
               + "; instance repeated geometry with InstancedMesh or merge "
                 "static clutter with BufferGeometryUtils.mergeGeometries",
               target="scene", kind="draw_calls", draws=draws, limit=limit)]


def shader_report(report: dict[str, Any], *, duration_ms: int = 0) -> GateReport:
    """Interpret a shader-preflight report (the ``shader_report`` block of ``probe_scene.mjs
    --compile``) into the ``shader_preflight`` GateReport."""
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
