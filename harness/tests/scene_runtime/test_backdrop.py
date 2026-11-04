"""One backdrop classifier serves census and frame coverage."""

from __future__ import annotations

import json

import pytest

from codeverse3d.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

RUNTIME_JS = get_settings().runtime_js_dir()

CASES = [
    # (name, box size [sx, sy, sz], instanced, content span, expected)
    ("SkyDome", [400, 200, 400], False, 0, "sky"),
    ("GroundPlane", [60, 0.2, 60], False, 0, "ground"),
    ("Meadow", [100, 0.5, 100], False, 0, "ground"),         # unnamed but very wide + flat
    ("Grass", [80, 1.0, 80], True, 0, "content"),            # scatter: instanced wins
    ("Cabin", [6, 4, 5], False, 0, "content"),
    ("Path", [30, 0.1, 30], False, 5, "ground"),             # far larger than the content bbox
    ("Path", [30, 0.1, 30], False, 0, "content"),            # same box, no content bbox known
    ("Rug", [3, 0.05, 3], False, 40, "content"),             # small relative to content: not backdrop
    ("PlanetSkyBackdrop", [205, 205, 205], False, 0, "sky"),
    ("SkyAtmosphereBand0", [90, 4, 90], False, 0, "sky"),
    ("MoonLamp", [1, 1, 1], False, 0, "content"),            # a solid prop remains content
    ("SkyScraper", [25, 200, 25], False, 0, "content"),       # the size guard still matters
]


def _classify(cases):
    body = f"""
import {{ classifyBackdrop }} from '{RUNTIME_JS}/lib/backdrop.mjs';
const cases = {json.dumps(cases)};
const out = cases.map(([name, size, instanced, span, lo]) => {{
  const o = lo || [-size[0] / 2, 0, -size[2] / 2];   // the box's min corner: centred on the origin by default
  return classifyBackdrop(
    {{ name, isInstancedMesh: instanced }},
    {{ min: {{ x: o[0], y: o[1], z: o[2] }}, max: {{ x: o[0] + size[0], y: o[1] + size[1], z: o[2] + size[2] }} }},
    span,
  );
}});
console.log(JSON.stringify(out));
"""
    return run_node_json(body)


def test_classifier_rule_table():
    got = _classify([[c[0], c[1], c[2], c[3]] for c in CASES] + [
        # `sunRig` parks its 'SunDisc' (no word boundary for SKY_NAME_RE) at 3 600 m: content by
        # name, and it blew the content bbox to 2.5 km — the overview rig then framed the sun.
        # A small thing kilometres away is sky whatever its name; the same disc near is content.
        ["SunDisc", [48, 48, 48], False, 40, [2100, 2600, 1800]],
        ["SunDisc", [48, 48, 48], False, 40, [10, 20, 10]],
    ])
    assert got == [c[4] for c in CASES] + ["sky", "content"], list(zip([c[0] for c in CASES], got, strict=False))


#: names on both sides of every JS name rule: acronyms, instance separators, long numeric ids
NAMES = ["Leg_2", "Rock.003", "Fence-1", "Leg2", "DonkeyCart_800006", "Leg_", "Planter_12", "LEDStairs",
         "HDRISky", "UVGround", "GPUTerrain", "PondWater", "pond_water", "SkyAtmosphereBand0", "TV-Stand.2", ""]


def test_js_name_rules_mirror_conventions():
    """Audit 2026-09-24 N3/N5."""
    from codeverse3d.conventions import split_instance, to_snake

    got = run_node_json(f"""
import {{ splitInstance, nameWords }} from '{RUNTIME_JS}/lib/backdrop.mjs';
const names = {json.dumps(NAMES)};
console.log(JSON.stringify({{ split: names.map(splitInstance), words: names.map(nameWords) }}));
""")
    assert got["split"] == [list(split_instance(n)) for n in NAMES]
    snake_words = [[w for w in to_snake(n).split("_") if not w.isdigit()] if n else [] for n in NAMES]
    assert got["words"] == snake_words


def test_instanced_scatter_is_one_box_for_census_and_coverage():
    """Audit 2026-09-24 N28."""
    got = run_node_json(f"""
import * as THREE from 'three';
import {{ classifyBackdrop, drawableBox }} from '{RUNTIME_JS}/lib/backdrop.mjs';
import {{ sceneCensus }} from '{RUNTIME_JS}/lib/host_census.mjs';
const scene = new THREE.Scene(), sky = new THREE.Group(), village = new THREE.Group();
sky.name = 'Sky'; village.name = 'Village'; scene.add(sky, village);
const inst = new THREE.InstancedMesh(new THREE.SphereGeometry(5), new THREE.MeshStandardMaterial(), 40);
inst.name = 'Clouds'; sky.add(inst);
const m = new THREE.Matrix4();
for (let i = 0; i < 40; i++) {{ m.makeTranslation(-1500 + i * 75, 200, (i % 5) * 300 - 600); inst.setMatrixAt(i, m); }}
const house = new THREE.Mesh(new THREE.BoxGeometry(10, 6, 8), new THREE.MeshStandardMaterial()); village.add(house);
scene.updateMatrixWorld(true);
const census = sceneCensus(scene, THREE);
const box = drawableBox(inst, THREE);
console.log(JSON.stringify({{ census: census.groups.find((g) => g.name === 'Sky').kind,
  coverage: classifyBackdrop(inst, box, 10), span: box.max.x - box.min.x }}));
""")
    assert got == {"census": "sky", "coverage": "sky", "span": 2935}, got
