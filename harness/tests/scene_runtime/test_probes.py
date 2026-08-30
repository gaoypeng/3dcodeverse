"""Browser-backed probes: scene probe, shader preflight."""

from __future__ import annotations

import pytest

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.probes import check_shaders, probe_scene
from tests.scene_runtime.conftest import needs_browser

pytestmark = [pytest.mark.node, needs_browser]


def test_probe_scene_passes_on_example(starter_ws):
    res = probe_scene(starter_ws)
    gate, census = res
    assert gate.gate == "scene_probe" and gate.passed, [(f.target, f.message) for f in gate.findings]
    assert res.ok and res.errors == []
    assert census["totals"]["meshes"] > 10 and census["totals"]["lights"] == 3
    assert {g["name"] for g in census["groups"]} == {"Environment", "Meadow", "Pondside"}
    assert census["content_bbox"]["size"][0] > 50
    assert (starter_ws.artifacts / "census.json").is_file()
    assert gate.duration_ms < 15000


def test_probe_scene_reports_import_error_with_stage(starter_ws):
    (starter_ws.src / "scene.js").write_text("import * as THREE from 'three';\nimport { nope } from './does_not_exist.js';\nexport function createScene() {}\n")
    gate, census = probe_scene(starter_ws)
    assert not gate.passed
    err = gate.errors[0]
    assert err.data.get("stage") == "import" and err.target == "src/scene.js"
    assert "fix the syntax/import error" in err.fix_hint


def test_probe_scene_flags_bad_shape_and_cameras(starter_ws):
    (starter_ws.src / "scene.js").write_text(
        "import * as THREE from 'three';\n"
        "export function createScene() { const scene = new THREE.Scene(); scene.add(new THREE.Mesh(new THREE.BoxGeometry(1,1,1), new THREE.MeshBasicMaterial()));\n"
        "  return { scene, cameras: [{ name: 'x', position: [1, 1], lookAt: [0, 0, 0] }], update(t) { if (t > 0.1) throw new Error('update boom'); } }; }\n"
    )
    gate, census = probe_scene(starter_ws)
    msgs = " | ".join(f.message for f in gate.findings)
    assert not gate.passed
    assert "position must be [x,y,z]" in msgs and "no valid cameras" in msgs
    assert census == {} or census.get("totals", {}).get("meshes") == 1  # census only when booted; boot fails on cameras here


def test_probe_scene_catches_update_throw_and_console_errors(starter_ws):
    (starter_ws.src / "scene.js").write_text(
        "import * as THREE from 'three';\n"
        "export function createScene() { const scene = new THREE.Scene(); scene.add(new THREE.AmbientLight()); scene.add(new THREE.Mesh(new THREE.BoxGeometry(1,1,1), new THREE.MeshBasicMaterial()));\n"
        "  console.error('custom failure 42');\n"
        "  return { scene, cameras: [{ name: 'x', position: [3, 3, 3], lookAt: [0, 0, 0], fov: 50 }], update(t) { if (t > 0.1) throw new Error('update boom'); } }; }\n"
    )
    gate, _ = probe_scene(starter_ws)
    msgs = " | ".join(f.message for f in gate.errors)
    assert "update boom" in msgs and "custom failure 42" in msgs


def test_check_shaders_clean_on_example(starter_ws):
    rep = check_shaders(starter_ws)
    assert rep.gate == "shader_preflight" and rep.passed, [(f.target, f.message) for f in rep.findings]
    info = [f for f in rep.findings if f.severity == Severity.INFO]
    assert info and info[0].data.get("programs", 0) >= 3


def test_check_shaders_maps_compile_error_to_file_line(starter_ws):
    p = starter_ws.src / "shaders" / "water.js"
    text = p.read_text()
    needle = "float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0);"
    assert needle in text
    text = text.replace(needle, "float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0) * undefinedThing;")
    p.write_text(text)
    line = next(i for i, ln in enumerate(text.splitlines(), 1) if "undefinedThing" in ln)
    rep = check_shaders(starter_ws)
    assert not rep.passed
    err = rep.errors[0]
    assert err.target == f"src/shaders/water.js:{line}", err
    assert "undeclared identifier" in err.message and "undefinedThing" in err.message
    assert err.data.get("material") and "PondWater" in err.data["material"]
    assert err.fix_hint


def test_check_shaders_on_before_compile_patch_error(starter_ws):
    (starter_ws.src / "shaders" / "glow.js").write_text(
        "import * as THREE from 'three';\n"
        "export function makeGlow(T = THREE) {\n"
        "  const m = new T.MeshStandardMaterial({ color: 0xff8800 });\n"
        "  m.onBeforeCompile = (shader) => {\n"
        "    shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `#include <dithering_fragment>\n"
        "  gl_FragColor.rgb += vec3(0.2) * missingUniform;`);\n"
        "  };\n"
        "  return m;\n"
        "}\n"
    )
    p = starter_ws.src / "zones" / "meadow.js"
    text = p.read_text().replace(
        "import { buildWindmill } from '../assets/windmill.js';",
        "import { buildWindmill } from '../assets/windmill.js';\nimport { makeGlow } from '../shaders/glow.js';",
    ).replace(
        "const rock = new THREE.MeshStandardMaterial({ color: 0x7b7b78, roughness: 0.95 });",
        "const rock = makeGlow(THREE);",
    )
    assert "makeGlow(THREE)" in text
    p.write_text(text)
    rep = check_shaders(starter_ws)
    assert not rep.passed
    err = rep.errors[0]
    assert err.target == "src/shaders/glow.js:6", err
    assert "missingUniform" in err.message


def test_check_shaders_static_audit_without_compile(starter_ws):
    (starter_ws.src / "shaders" / "bad.js").write_text(
        "export const frag = `\n#version 300 es\nvoid main() { gl_FragColor = vec4(uTime); }`;\n"
    )
    rep = check_shaders(starter_ws)
    kinds = {f.data.get("kind") for f in rep.errors}
    assert {"version_directive", "undeclared_uniform", "unbound_uniform"} <= kinds
    assert any(f.target == "src/shaders/bad.js:2" for f in rep.errors)


def test_probe_scene_hanging_create_scene_times_out_as_agent_finding(starter_ws):
    """A hanging createScene times out as an agent finding, not a harness crash."""
    (starter_ws.src / "scene.js").write_text(
        "import * as THREE from 'three';\n"
        "export async function createScene({ THREE: T, renderer, loaders }) {\n"
        "  const tex = await new Promise((resolve) => loaders.texture.load('/assets/missing.png', resolve));\n"
        "  return { scene: new THREE.Scene(), cameras: [], update() {} };\n"
        "}\n"
    )
    res = probe_scene(starter_ws, timeout_s=10)   # createScene timeout = 6 s < watchdog
    gate = res.gate
    assert not gate.passed
    err = next(f for f in gate.errors if f.data.get("stage") == "createScene")
    assert "did not resolve within" in err.message and "never settled" in err.message
    assert "failed to load /assets/missing.png" in err.message
    assert "createScene({THREE, renderer, loaders})" in err.fix_hint
    assert res.ok and res.errors == []          # the probe TOOL ran fine (agent-fixable failure)
    assert not any("harness/driver failure" in f.fix_hint for f in gate.findings)


def test_probe_result_tool_semantics(starter_ws, monkeypatch):
    """Tool success is distinct from agent-fixable gate success."""
    import codeverse.spatial.probes as probes_mod
    from codeverse.spatial.render_scene import NodeResult, SceneRenderError

    def fake_run_ok(script, args, **kw):
        return NodeResult(0, "", "", {
            "ok": False,
            "boot": {"ok": True, "stage": "ready", "cameras": [{"name": "a"}], "camera_problems": []},
            "update_ok": True, "census": {"totals": {"meshes": 3, "lights": 1}},
            "console_errors": ["custom failure 42"], "console_warnings": [], "shader_errors": [],
        }, 5)

    monkeypatch.setattr(probes_mod, "run_scene_script", fake_run_ok)
    res = probes_mod.probe_scene(starter_ws)
    assert not res.gate.passed                       # gate truth: the scene has an error
    assert res.ok and res.errors == []               # tool truth: the probe ran
    assert any("custom failure 42" in line for line in res.findings)

    def fake_run_crash(script, args, **kw):
        raise SceneRenderError("probe_scene.mjs failed (exit 3): timeout")

    monkeypatch.setattr(probes_mod, "run_scene_script", fake_run_crash)
    res2 = probes_mod.probe_scene(starter_ws)
    assert not res2.gate.passed and not res2.ok
    assert res2.errors and "could not run" in res2.errors[0]
    assert res2.gate.errors[0].data.get("harness_failure") is True
    rep = probes_mod.check_shaders(starter_ws)
    assert not rep.passed and rep.errors[0].data.get("harness_failure") is True
