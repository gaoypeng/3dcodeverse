"""Scene probes: the import/census gate and the shader-preflight report reader.

* ``run_probe(ws, compile=)`` → (scene_probe, shader_preflight, census) — THE
  ``probe_scene.mjs`` driver call; ``compile=True`` (the scene build and the
  ``shader_probe`` tool) adds the shader preflight to the same boot
* ``probe_scene(ws)``  → (GateReport 'scene_probe', census) — the standalone probe
  (the ``scene_probe`` tool, ``check_placement(rebuild=true)``)
* ``probe_report`` / ``shader_report`` — the two gates, pure over the driver JSON.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.conventions import MAX_TRIS_SCENE
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
    """``probe_scene`` result: unpacks as ``gate, census = probe_scene(ws)`` and also
    dumps to a dict generic tool adapters can read.  Tool semantics: ``ok`` /
    ``errors`` describe whether the PROBE TOOL ran — a gate that fails on
    agent-fixable findings keeps ``ok=True`` with the finding lines under
    ``findings``; ``errors`` holds only harness/driver failures and is what the
    tool maps to ``Observation.failed`` (MCP ``is_error``).  Gate truth stays on
    ``gate.passed``, which is the observation's verdict."""

    gate: GateReport
    census: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list, description="harness/driver failures only (tool could not run)")
    findings: list[str] = Field(default_factory=list, description="agent-fixable gate findings, one line each")
    ok: bool = False

    def __iter__(self):  # type: ignore[override]
        yield self.gate
        yield self.census


def _result(gate: GateReport, census: dict[str, Any]) -> SceneProbeResult:
    """A ``harness_failure`` finding IS a driver failure, wherever it was raised.

    ``ok`` is "the tool could run" (``registry.Observation.failed = not ok``) and the
    agent-facing ``findings`` deliberately exclude harness failures — so a report that
    carries one and nothing else used to come back ok, with no errors and no findings: a
    probe that reads healthy and measured nothing."""
    errors = [f.message[:800] for f in gate.findings if f.data.get("harness_failure")]
    lines = [f"[{f.severity.value}] {f.target or ''}: {f.message}" for f in gate.findings
             if f.severity != Severity.INFO and not f.data.get("harness_failure")]
    return SceneProbeResult(gate=gate, census=census, ok=not errors, errors=errors, findings=lines)


def _f(gate: str, sev: Severity, msg: str, *, target: str | None = None, hint: str = "", **data: Any) -> GateFinding:
    return GateFinding(gate=gate, severity=sev, target=target, message=msg, fix_hint=hint, data=data)


def run_probe(ws: Workspace, *, compile: bool = False, timeout_s: float | None = None,
              ) -> tuple[GateReport, GateReport, dict[str, Any]]:
    """One ``probe_scene.mjs`` boot → (scene_probe, shader_preflight, census).

    ``compile`` also runs the shader preflight on the same page; without it — or when the
    scene never boots — the preflight is a failed-empty gate.  ``timeout_s`` defaults to
    the build timeout, capped at 120 s.  A driver that cannot run is a ``harness_failure``
    finding, never a raise."""
    t0 = time.time()
    timeout_s = min(float(timeout_s or get_settings().limits.build_timeout_s), 120.0)
    args = ["--ws", str(ws.root), "--out", str(ws.artifacts / "scene_probe.json"), "--timeout-ms", str(int(timeout_s * 1000))]
    if compile:
        args += ["--compile", "--shaders-out", str(ws.artifacts / "shader_preflight.json")]
    # every probe runs under the SAME settle / camera-repair / auto-exposure policy every
    # render uses — ONE parser, render_scene (review-3 S4: the build used to carry none of
    # the flags, so its gate measured a census the renders then contradicted)
    args += probe_env_args()
    no_preflight = GateReport(gate=SHADER_GATE, passed=False, findings=[])
    try:
        res = run_scene_script("probe_scene.mjs", args, timeout_s=timeout_s + 20)
    except SceneRenderError as e:
        finding = _f(PROBE_GATE, Severity.ERROR, f"scene probe could not run: {e}"[:1500], target="src/scene.js",
                     hint="this is a harness/driver failure, not your code; retry or report", harness_failure=True)
        return GateReport(gate=PROBE_GATE, passed=False, findings=[finding], duration_ms=int((time.time() - t0) * 1000)), no_preflight, {}
    probe, census = probe_report(res.summary, duration_ms=int((time.time() - t0) * 1000))
    rep = res.summary.get("shader_report") or {}
    if not compile or not rep or rep.get("skipped"):
        return probe, no_preflight, census
    return probe, shader_report(rep, duration_ms=int(rep.get("duration_ms") or 0)), census


def probe_scene(ws: Workspace, *, timeout_s: float = 60.0) -> SceneProbeResult:
    """Import-only probe: module loads, shape valid, cameras valid, update runs, census."""
    report, _, census = run_probe(ws, timeout_s=timeout_s)
    if census:
        ws.write_json(ws.artifacts / "census.json", census)
    return _result(report, census)


def probe_report(summary: dict[str, Any], *, duration_ms: int = 0) -> tuple[GateReport, dict[str, Any]]:
    """Interpret a ``probe_scene.mjs`` summary into the ``scene_probe`` GateReport
    (+ census).  Pure over the driver JSON, so the combined single-boot build
    (``probe_scene.mjs --compile``, scene_threejs runtime) reuses it."""
    gate = PROBE_GATE
    findings: list[GateFinding] = []
    s = summary
    boot = s.get("boot") or {}
    if "boot" not in s:
        # NO boot record at all is not a verdict about the scene: `probe_scene.mjs` always
        # carries `boot` in its summary, so an absent one means the driver produced no
        # parsable summary line — its stdout tail was dropped (`proc._ABANDONED`), or it
        # died in a way `run_scene_script` let through with exit 0.  Saying "scene did not
        # boot" here hands the agent a defect that does not exist and names nothing it can
        # fix: on desert_canyon (bench/out/scene_baseline, 2026-09-05) the run spent three
        # repair attempts on `[?] scene did not boot` with an identical signature, while the
        # driver's own artifacts/scene_probe.json recorded `ok: true, boot.ok: true,
        # stage: ready` — and the workspace boots in 600 ms today, unchanged.
        findings.append(_f(gate, Severity.ERROR, "scene probe produced no result (driver output lost)",
                           target="src/scene.js",
                           hint="this is a harness/driver failure, not your code; retry or report",
                           harness_failure=True))
        return GateReport(gate=gate, passed=False, findings=findings, duration_ms=duration_ms), {}
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
                           target=e.get("material") or "shader", hint="run shader_probe for the file:line mapping"))
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
    return GateReport.of(gate, findings, duration_ms=duration_ms), census


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


def shader_report(report: dict[str, Any], *, duration_ms: int = 0) -> GateReport:
    """Interpret a shader-preflight report (the ``shader_report`` block of
    ``probe_scene.mjs --compile``) into the ``shader_preflight`` GateReport.  Pure over
    the driver JSON."""
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
