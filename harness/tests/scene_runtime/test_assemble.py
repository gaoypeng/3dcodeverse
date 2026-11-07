"""Deterministic assembler: pure parts + browser-probed assembly."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.conventions import to_snake
from codeverse3d.languages.scene_threejs import (
    assemble,
    lint,
)
from codeverse3d.tracks.planner import plan_example
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
    plan = ScenePlan.model_validate(plan_example(Track.SCENE))
    res = assemble(starter_ws, plan)
    assert set(res.zones_included) == {"meadow", "pondside", "orchard"}
    assert "kaboom" in res.zones_failed["broken"]
    assert "no export build" in res.zones_failed["nobuild"]
    assert res.census.get("ground_y") is not None
    assert not (starter_ws.root / "src" / "_c3v_assemble_probe.js").exists()
    src = (starter_ws.src / "scene.js").read_text()
    assert all(f"name: '{to_snake(c.name)}'" in src for c in plan.cameras), "the plan's cameras reach scene.js"
    assert "broken" not in src and "nobuild" not in src
    (zones / "broken.js").unlink()
    (zones / "nobuild.js").unlink()
    assert lint(starter_ws).passed
    from codeverse3d.spatial.probes import probe_scene

    probe = probe_scene(starter_ws)
    assert probe.gate.passed, [(f.target, f.message) for f in probe.gate.findings]
    assert "Orchard" in {g["name"] for g in probe.census.get("groups", [])}
