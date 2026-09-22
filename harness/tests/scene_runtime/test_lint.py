"""Static lint for scene_threejs workspaces."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.scene_threejs import lint
from tests.scene_runtime.conftest import needs_node

pytestmark = [pytest.mark.node, needs_node]


def _msgs(report, sev=None):
    return [(f.target, f.message) for f in report.findings if sev is None or f.severity == sev]


def test_example_scene_lints_clean(starter_ws):
    rep = lint(starter_ws)
    assert rep.gate == "lint:scene_threejs"
    assert rep.passed, _msgs(rep)
    assert not [f for f in rep.findings if f.severity == Severity.ERROR]


def test_required_scene_entry_and_export(ws):
    rep = lint(ws)
    assert not rep.passed
    assert any("src/scene.js is missing" in m for _, m in _msgs(rep, Severity.ERROR))
    (ws.src / "scene.js").write_text("import * as THREE from 'three';\nexport function makeScene() {}\n")
    rep = lint(ws)
    assert any("does not export createScene" in m for _, m in _msgs(rep, Severity.ERROR))


def test_forbidden_imports_and_globals(ws):
    (ws.src / "scene.js").write_text(
        "import * as THREE from 'three';\n"
        "import gsap from 'gsap';\n"
        "import { x } from 'https://unpkg.com/foo@1/foo.js';\n"
        "import { y } from './missing.js';\n"
        "export function createScene() {\n"
        "  requestAnimationFrame(() => {});\n"
        "  const r = new THREE.WebGLRenderer();\n"
        "  document.body.appendChild(r.domElement);\n"
        "  const g = new THREE.BoxBufferGeometry(1, 1, 1);\n"
        "  return { scene: new THREE.Scene(), cameras: [], update() {} };\n"
        "}\n"
    )
    rep = lint(ws)
    errs = _msgs(rep, Severity.ERROR)
    text = " | ".join(m for _, m in errs)
    assert "import 'gsap' is not allowed" in text
    assert "https://unpkg.com" in text or "not allowed" in text
    assert "missing file './missing.js'" in text
    assert "requestAnimationFrame" in text
    assert "creating a renderer" in text
    assert "DOM/page access" in text
    assert "BufferGeometry aliases" in text
    # targets carry file:line
    assert any(t and t.startswith("src/scene.js:") for t, _ in errs)


def test_syntax_error_maps_to_line(ws):
    (ws.src / "scene.js").write_text("import * as THREE from 'three';\nexport function createScene() {\n  const = 3;\n}\n")
    rep = lint(ws)
    f = next(f for f in rep.findings if f.message.startswith("syntax"))
    assert f.target == "src/scene.js:3"


def test_zone_without_build_and_asset_without_factory(ws):
    (ws.src / "scene.js").write_text("export function createScene() { return {}; }\n")
    (ws.src / "zones").mkdir()
    (ws.src / "zones" / "dock.js").write_text("export function make() {}\n")
    (ws.src / "assets").mkdir()
    (ws.src / "assets" / "crate.js").write_text("export function crate() {}\n")
    rep = lint(ws)
    assert any("zone module does not export build" in m for _, m in _msgs(rep, Severity.ERROR))
    assert any("asset module exports no build" in m for _, m in _msgs(rep, Severity.WARN))


def test_large_file_warning(ws):
    (ws.src / "scene.js").write_text("export function createScene() { return {}; }\n" + "// pad\n" * 900)
    rep = lint(ws)
    assert any("large file" in m for _, m in _msgs(rep, Severity.WARN))


def test_an_untouched_env_skeleton_is_an_error(ws):
    """The env stage shipping the skeleton back is invisible to every other gate: the file
    parses, exports what it must, and renders — as a sunny day, whatever the brief asked for."""
    (ws.src / "scene.js").write_text(
        "import * as THREE from 'three';\n"
        "export function createScene({ THREE, renderer, loaders }) { return { scene: new THREE.Scene(), cameras: [], update() {} }; }\n")
    skeleton = (
        "// ENV PLAN: night, moonlit, sky zenith 0x070b18\n"
        "// Rewrite the ground/sky/fog/sun below to match the plan; keep buildEnv/heightAt exports.\n"
        "import * as THREE from 'three';\n"
        "export const SUN_AZIMUTH_DEG = 60;\n"
        "export function heightAt(x, z) { return 0; }\n"
        "export function buildEnv(ctx) {\n"
        "  ctx.scene.background = new THREE.Color(0xcfdcec);\n"
        "  ctx.scene.add(new THREE.HemisphereLight(0xbcd7ff, 0x4a5a2a, 0.9));\n"
        "  return { update(t, dt) {} };\n"
        "}\n")
    (ws.src / "env.js").write_text(skeleton)
    rep = lint(ws)
    assert not rep.passed
    assert any("untouched skeleton" in m for _, m in _msgs(rep, Severity.ERROR))

    # the same file with the plan's own colours written in is clean again
    (ws.src / "env.js").write_text(
        skeleton.replace("// Rewrite the ground/sky/fog/sun below to match the plan; keep buildEnv/heightAt exports.\n", "")
                .replace("0xcfdcec", "0x141a2e").replace("0xbcd7ff", "0x1c2b4a"))
    rep = lint(ws)
    assert not any("untouched skeleton" in m for _, m in _msgs(rep, Severity.ERROR)), _msgs(rep)

