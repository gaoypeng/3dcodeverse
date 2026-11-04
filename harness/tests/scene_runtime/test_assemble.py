"""Deterministic assembler: pure parts + browser-probed assembly."""

from __future__ import annotations

import math

import pytest

from codeverse3d.languages.scene_threejs import (
    assemble,
    lint,
    sun_azimuth,
)
from tests.scene_runtime.conftest import needs_browser


@pytest.mark.node
@needs_browser
def test_assemble_probes_each_zone_excludes_the_broken_and_boots(starter_ws):
    """One probed assembly."""
    zones = starter_ws.src / "zones"
    (zones / "broken.js").write_text("export function build(ctx) { throw new Error('kaboom'); }\n")
    (zones / "nobuild.js").write_text("export const x = 1;\n")
    (zones / "orchard.js").write_text(
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
    assert set(res.zones_included) == {"meadow", "pondside", "orchard"}
    assert "kaboom" in res.zones_failed["broken"]
    assert "no export build" in res.zones_failed["nobuild"]
    assert res.census.get("ground_y") is not None
    assert not (starter_ws.root / "src" / "_c3v_assemble_probe.js").exists()
    # driver-fitted cameras: an overview plus one eye-level camera per measured zone
    assert res.cameras[0].name == "overview" and res.cameras[0].position[1] > 0.5
    assert {"meadow_view", "pondside_view", "orchard_view"} <= {c.name for c in res.cameras}
    src = (starter_ws.src / "scene.js").read_text()
    assert "broken" not in src and "nobuild" not in src
    (zones / "broken.js").unlink()
    (zones / "nobuild.js").unlink()
    assert lint(starter_ws).passed
    from codeverse3d.spatial.probes import probe_scene

    probe = probe_scene(starter_ws)
    assert probe.gate.passed, [(f.target, f.message) for f in probe.gate.findings]
    assert "Orchard" in {g["name"] for g in probe.census.get("groups", [])}


@pytest.mark.node
@needs_browser
def test_fitted_overview_faces_the_lit_side_inside_the_plan_bounds(tmp_ws):
    """Regressions (audit 2026-09-24 N8, N9)."""
    import json

    from codeverse3d.languages.scene_threejs import probe_zone_modules

    (tmp_ws.src / "env.js").write_text("export const SUN_AZIMUTH_DEG = 135;\nexport function buildEnv() { return {}; }\n")
    zones = tmp_ws.src / "zones"
    zones.mkdir()
    (zones / "cottage.js").write_text(
        "import * as THREE from 'three';\n"
        "export function build() {\n"
        "  const m = new THREE.Mesh(new THREE.BoxGeometry(16, 7, 12), new THREE.MeshStandardMaterial());\n"
        "  m.position.y = 3.5; m.name = 'Cottage'; const g = new THREE.Group(); g.add(m); return g;\n"
        "}\n")
    (zones / "meadow.js").write_text(   # an instanced scatter 400 m wide
        "import * as THREE from 'three';\n"
        "export function build() {\n"
        "  const im = new THREE.InstancedMesh(new THREE.BoxGeometry(0.3, 1.2, 0.3), new THREE.MeshStandardMaterial(), 64);\n"
        "  const m = new THREE.Matrix4();\n"
        "  for (let i = 0; i < 64; i++) im.setMatrixAt(i, m.makeTranslation(-200 + (i % 8) * 400 / 7, 0.6, -200 + Math.floor(i / 8) * 400 / 7));\n"
        "  im.name = 'Grass'; const g = new THREE.Group(); g.add(im); return g;\n"
        "}\n")
    tmp_ws.plan_path.write_text(json.dumps({"bounds": {"center": [0, 7.5, 0], "extents": [60, 15, 60]}}))
    report = probe_zone_modules(tmp_ws, sun_azimuth_deg=sun_azimuth(tmp_ws))
    ov = report.camera_specs["overview"]
    view = [p - q for p, q in zip(ov["position"], ov["lookAt"], strict=True)]
    sun = (math.cos(math.radians(135)), math.sin(math.radians(135)))    # sunRig's (x, z)
    cos = (view[0] * sun[0] + view[2] * sun[1]) / math.hypot(view[0], view[2])
    assert cos > 0.99, (ov, cos)
    assert math.dist(ov["position"], ov["lookAt"]) < 100, ov
