"""Deterministic assembler: pure parts + browser-probed assembly."""

from __future__ import annotations

import math

import pytest

from codeverse.contracts.plan import CameraPlan
from codeverse.languages.scene_threejs.assemble import (
    ZoneProbe,
    assemble,
    derive_cameras,
    fit_zone_camera,
    probe_zone_modules,
    render_scene_js,
    sun_azimuth,
)
from codeverse.languages.scene_threejs.lint import lint
from tests.scene_runtime.conftest import needs_browser, needs_node

BOX_A = {"min": [-10, 0, -10], "max": [10, 6, 10], "size": [20, 6, 20]}
BOX_B = {"min": [20, 0, -5], "max": [30, 3, 5], "size": [10, 3, 10]}


def test_derive_cameras_sun_side_and_order():
    probes = {
        "harbour": ZoneProbe(name="harbour", file="src/zones/harbour.js", ok=True, bbox=BOX_A),
        "market": ZoneProbe(name="market", file="src/zones/market.js", ok=True, bbox=BOX_B),
        "broken": ZoneProbe(name="broken", file="src/zones/broken.js", ok=False, error="boom"),
    }
    cams = derive_cameras(probes, sun_azimuth_deg=90, ground_y=0.0)
    assert [c.name for c in cams] == ["overview", "harbour_view", "market_view"]
    ov = cams[0]
    # sun at azimuth 90 → eye on +X side of the union centre, above ground, looking at the centre
    assert ov.position[0] > ov.look_at[0] and ov.position[1] > 0.5
    hv = cams[1]
    assert hv.position[0] > 10 and 1.5 < hv.position[1] < 4.5 and abs(hv.position[2]) < 1e-6
    assert math.dist(hv.position, hv.look_at) > 10


def test_derive_cameras_fallback_without_zones():
    cams = derive_cameras({}, sun_azimuth_deg=45)
    assert len(cams) == 1 and cams[0].name == "overview"


def test_fit_zone_camera_respects_floor():
    c = fit_zone_camera("pier", BOX_B, azimuth=0, floor=2.0)
    assert c.position[1] >= 3.7
    assert c.name == "pier_view"


def test_render_scene_js_contents():
    cams = [CameraPlan(name="overview", position=(1, 2, 3), look_at=(0, 0, 0), fov=50)]
    src = render_scene_js(["harbour", "fish_market"], cams, ["crane"], env_ok=True)
    assert "import { build as buildHarbour } from './zones/harbour.js';" in src
    assert "addZone(buildFishMarket, 'FishMarket');" in src
    assert "crane: '/assets/crane.glb'" in src
    assert "name: 'overview', position: [1, 2, 3]" in src
    assert "import { buildEnv, heightAt } from './env.js';" in src
    src2 = render_scene_js([], cams, [], env_ok=False)
    assert "env.js" not in src2 and "heightAt: () => 0" in src2


def test_sun_azimuth_reads_env(starter_ws):
    assert sun_azimuth(starter_ws) == 60.0


@needs_node
def test_assemble_without_probe_writes_all_zones(starter_ws):
    res = assemble(starter_ws, probe=False)
    assert set(res.zones_included) == {"meadow", "pondside"}
    assert (starter_ws.src / "scene.js").read_text().startswith("// src/scene.js — ASSEMBLED")
    assert lint(starter_ws).passed
    assert (starter_ws.artifacts / "assemble.json").is_file()


@pytest.mark.node
@needs_browser
def test_probe_zone_modules_reports_failures_and_bboxes(starter_ws):
    (starter_ws.src / "zones" / "broken.js").write_text("export function build(ctx) { throw new Error('kaboom'); }\n")
    (starter_ws.src / "zones" / "nobuild.js").write_text("export const x = 1;\n")
    probes, census, other = probe_zone_modules(starter_ws)
    assert probes["meadow"].ok and probes["meadow"].bbox and probes["meadow"].meshes > 5
    assert probes["pondside"].ok
    assert not probes["broken"].ok and "kaboom" in probes["broken"].error
    assert not probes["nobuild"].ok and "no export build" in probes["nobuild"].error
    assert census.get("ground_y") is not None
    assert not (starter_ws.root / "src" / "_c3v_assemble_probe.js").exists()


@pytest.mark.node
@needs_browser
def test_assemble_end_to_end_excludes_broken_zone(starter_ws):
    (starter_ws.src / "zones" / "broken.js").write_text("export function build(ctx) { throw new Error('kaboom'); }\n")
    res = assemble(starter_ws)
    assert set(res.zones_included) == {"meadow", "pondside"}
    assert "broken" in res.zones_failed
    assert res.cameras[0].name == "overview" and len(res.cameras) == 3
    src = (starter_ws.src / "scene.js").read_text()
    assert "broken" not in src
    assert lint(starter_ws).passed


def test_render_scene_js_awaits_async_builds():
    """Finding: lint + the assembler probe accept `export async function build(ctx)`,
    so the generated scene.js must await build/buildEnv instead of throwing on the Promise."""
    cams = [CameraPlan(name="overview", position=(1, 2, 3), look_at=(0, 0, 0), fov=50)]
    src = render_scene_js(["orchard"], cams, [], env_ok=True)
    assert "const addZone = async (build, name) => {" in src
    assert "const g = await build(ctx);" in src
    assert "await addZone(buildOrchard, 'Orchard');" in src
    assert "const env = (await buildEnv(ctx)) || {};" in src


@pytest.mark.node
@needs_browser
def test_assemble_with_async_zone_boots(starter_ws):
    (starter_ws.src / "zones" / "orchard.js").write_text(
        "import * as THREE from 'three';\n"
        "export async function build(ctx) {\n"
        "  await new Promise((r) => setTimeout(r, 10));\n"
        "  const g = new THREE.Group(); g.name = 'Orchard';\n"
        "  const m = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), new THREE.MeshStandardMaterial({ color: 0x884400 }));\n"
        "  m.position.y = 1; g.add(m);\n"
        "  return g;\n"
        "}\n"
    )
    res = assemble(starter_ws)
    assert "orchard" in res.zones_included and res.zones_failed == {}
    from codeverse.spatial.probes import probe_scene

    probe = probe_scene(starter_ws)
    assert probe.gate.passed, [(f.target, f.message) for f in probe.gate.findings]
    assert "Orchard" in {g["name"] for g in probe.census.get("groups", [])}
