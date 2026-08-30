"""Deterministic assembler: pure parts + browser-probed assembly."""

from __future__ import annotations

import math

import pytest

from codeverse.contracts.plan import CameraPlan
from codeverse.languages.scene_threejs import (
    ZoneProbe,
    assemble,
    cameras_from_specs,
    lint,
    probe_zone_modules,
    render_scene_js,
    sun_azimuth,
)
from tests.scene_runtime.conftest import needs_browser, needs_node

BOX_A = {"min": [-10, 0, -10], "max": [10, 6, 10], "size": [20, 6, 20]}
BOX_B = {"min": [20, 0, -5], "max": [30, 3, 5], "size": [10, 3, 10]}

# what probe_scene.mjs --sun-azimuth 90 emits for the two-zone layout above
# (camera-fit math is owned by runtime_js/lib/orbit.mjs; tested in test_orbit.py)
SPECS = {
    "azimuth": 90,
    "overview": {"position": [55.0, 22.0, 0.0], "lookAt": [10.0, 3.0, 0.0], "fov": 50},
    "zones": [
        {"group": "__zone__harbour", "position": [14.4, 2.2, 0.0], "lookAt": [0.0, 2.6, 0.0], "fov": 50},
        {"group": "__zone__market", "position": [32.2, 1.9, 0.0], "lookAt": [25.0, 1.55, 0.0], "fov": 50},
    ],
}


def test_cameras_from_specs_sun_side_and_order():
    probes = {
        "harbour": ZoneProbe(name="harbour", file="src/zones/harbour.js", ok=True, bbox=BOX_A),
        "market": ZoneProbe(name="market", file="src/zones/market.js", ok=True, bbox=BOX_B),
        "broken": ZoneProbe(name="broken", file="src/zones/broken.js", ok=False, error="boom"),
    }
    cams = cameras_from_specs(SPECS, probes)
    assert [c.name for c in cams] == ["overview", "harbour_view", "market_view"]
    ov = cams[0]
    assert ov.position == (55.0, 22.0, 0.0) and ov.look_at == (10.0, 3.0, 0.0)
    assert "sun side" in ov.purpose
    hv = cams[1]
    assert hv.position[0] > 10 and math.dist(hv.position, hv.look_at) > 10
    assert cams[2].purpose == "zone Market from the sun side"


def test_cameras_from_specs_fallback_without_zones_or_specs():
    cams = cameras_from_specs({}, {})
    assert len(cams) == 1 and cams[0].name == "overview" and "fallback" in cams[0].purpose
    # measurable zones but no driver specs (e.g. probe ran without --sun-azimuth)
    probes = {"harbour": ZoneProbe(name="harbour", file="src/zones/harbour.js", ok=True, bbox=BOX_A)}
    cams2 = cameras_from_specs({}, probes)
    assert len(cams2) == 1 and "fallback" in cams2[0].purpose


def test_render_scene_js_contents():
    cams = [CameraPlan(name="overview", position=(1, 2, 3), look_at=(0, 0, 0), fov=50)]
    src = render_scene_js(["harbour", "fish_market"], cams, ["crane"], env_ok=True)
    assert "import { build as buildHarbour } from './zones/harbour.js';" in src
    assert "addZone(buildFishMarket, 'FishMarket');" in src
    assert "crane: '/assets/crane.glb'" in src
    assert "name: 'overview', position: [1, 2, 3]" in src
    assert "import { buildEnv, heightAt } from './env.js';" in src
    assert "const addZone = async (build, name) => {" in src
    assert "const g = await build(ctx);" in src
    assert "await addZone(buildHarbour, 'Harbour');" in src
    assert "const env = (await buildEnv(ctx)) || {};" in src
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
    report = probe_zone_modules(starter_ws, sun_azimuth_deg=60.0)
    probes, census, other = report   # historical 3-tuple unpacking still works
    assert probes["meadow"].ok and probes["meadow"].bbox and probes["meadow"].meshes > 5
    assert probes["pondside"].ok
    assert not probes["broken"].ok and "kaboom" in probes["broken"].error
    assert not probes["nobuild"].ok and "no export build" in probes["nobuild"].error
    assert census.get("ground_y") is not None
    assert not (starter_ws.root / "src" / "_c3v_assemble_probe.js").exists()
    # driver-fitted cameras: an overview plus one eye-level spec per measured zone group
    specs = report.camera_specs
    assert specs["azimuth"] == 60.0 and specs["overview"]["position"][1] > 0.5
    zone_groups = {z["group"] for z in specs["zones"]}
    assert {"__zone__meadow", "__zone__pondside"} <= zone_groups


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
