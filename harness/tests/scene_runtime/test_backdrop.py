"""One backdrop classifier serves census and frame coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

RUNTIME_JS = get_settings().runtime_js_dir()
LIB_DIR = Path(__file__).resolve().parents[2] / "codeverse3d/languages/scene_threejs/starter/src/lib"

CASES = [
    # (name, box size [sx, sy, sz], instanced, content span, expected)
    ("SkyDome", [400, 200, 400], False, 0, "sky"),
    ("Anything", [3000, 10, 3000], False, 0, "sky"),
    ("GroundPlane", [60, 0.2, 60], False, 0, "ground"),
    ("Terrain", [30, 1.0, 30], False, 0, "ground"),          # named ground, flat enough
    ("Meadow", [100, 0.5, 100], False, 0, "ground"),         # unnamed but very wide + flat
    ("Grass", [80, 1.0, 80], True, 0, "content"),            # scatter: instanced wins
    ("Cabin", [6, 4, 5], False, 0, "content"),
    ("Cabin", [6, 4, 5], False, 3, "content"),
    ("Path", [30, 0.1, 30], False, 5, "ground"),             # far larger than the content bbox
    ("Path", [30, 0.1, 30], False, 0, "content"),            # same box, no content bbox known
    ("Rug", [3, 0.05, 3], False, 40, "content"),             # small relative to content: not backdrop
    ("PlanetSkyBackdrop", [205, 205, 205], False, 0, "sky"),
    ("planet_sky_backdrop", [205, 205, 205], False, 0, "sky"),
    ("SkyAtmosphereBand0", [90, 4, 90], False, 0, "sky"),
    ("MoonLamp", [1, 1, 1], False, 0, "content"),            # a solid prop remains content
    ("SkyScraper", [25, 200, 25], False, 0, "content"),       # the size guard still matters
    ("HDRISky", [400, 200, 400], False, 0, "sky"),             # acronym names split like to_snake (N5)
    ("GPUTerrain", [30, 1.0, 30], False, 0, "ground"),
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


def test_background_planet_and_small_moon_do_not_move_content_framing():
    body = f"""
import * as THREE from 'three';
import {{ sceneCensus }} from '{RUNTIME_JS}/lib/host_census.mjs';
import {{ framingBox }} from '{RUNTIME_JS}/lib/orbit.mjs';
const scene = new THREE.Scene(), environment = new THREE.Group();
environment.name = 'Environment'; scene.add(environment);
const subject = new THREE.Mesh(new THREE.BoxGeometry(30, 20, 40), new THREE.MeshStandardMaterial());
subject.name = 'Hangar'; subject.position.y = 10; environment.add(subject);
const planet = new THREE.Mesh(new THREE.SphereGeometry(100), new THREE.MeshBasicMaterial());
planet.name = 'PlanetSkyBackdrop'; planet.position.set(25, -55, -200); environment.add(planet);
const moon = new THREE.Mesh(new THREE.CircleGeometry(2.5), new THREE.MeshBasicMaterial({{ depthWrite: false }}));
moon.name = 'MoonDisc'; moon.position.set(-277, 137, -103); environment.add(moon);
const census = sceneCensus(scene, THREE);
console.log(JSON.stringify({{ content: census.content_bbox, all: census.bbox, framed: framingBox(census) }}));
"""
    got = run_node_json(body)
    assert got["content"]["min"] == [-15, 0, -20]
    assert got["content"]["max"] == [15, 20, 20]
    assert got["framed"] == got["content"]
    assert got["all"]["min"][0] < -270  # background geometry is retained and still counted


def test_explicit_sky_metadata_requires_non_depth_writing_materials():
    got = run_node_json(f"""
import * as THREE from 'three';
import {{ classifyBackdrop, drawableBox }} from '{RUNTIME_JS}/lib/backdrop.mjs';
const clear = new THREE.MeshBasicMaterial({{ depthWrite: false }});
const solid = new THREE.MeshBasicMaterial();
const classify = (material, marker) => {{
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10), material);
  mesh.name = 'WeatherPart'; mesh.userData.sceneBackdrop = marker;
  return classifyBackdrop(mesh, drawableBox(mesh, THREE));
}};
console.log(JSON.stringify([
  classify(clear, 'sky'), classify(solid, 'sky'), classify([clear, solid], 'sky'),
  classify(clear, undefined), classify(clear, 'content'),
]));
""")
    assert got == ["sky", "content", "content", "content", "content"]


def test_compact_clouds_preserve_content_framing_with_custom_names_and_scale():
    got = run_node_json(f"""
import * as THREE from 'three';
import {{ makeClouds, makeCirrus }} from '{LIB_DIR}/clouds.js';
import {{ sceneCensus }} from '{RUNTIME_JS}/lib/host_census.mjs';
import {{ framingBox }} from '{RUNTIME_JS}/lib/orbit.mjs';
const results = [];
for (const factory of [makeClouds, makeCirrus]) for (const custom of [false, true]) {{
  const scene = new THREE.Scene();
  const subject = new THREE.Mesh(new THREE.BoxGeometry(10, 10, 10), new THREE.MeshStandardMaterial());
  subject.name = 'House'; scene.add(subject);
  const cloud = factory({{ count: 1, area: 600, quality: 'low', ...(custom ? {{ name: 'WeatherDeck' }} : {{}}) }});
  if (custom) cloud.scale.setScalar(.01);
  scene.add(cloud);
  const census = sceneCensus(scene, THREE);
  results.push({{ kind: census.groups[1].kind, framed: framingBox(census) }});
  cloud.userData.dispose(); subject.geometry.dispose(); subject.material.dispose();
}}
console.log(JSON.stringify(results));
""")
    for result in got:
        assert result["kind"] == "sky", result
        assert result["framed"] == {"min": [-5, -5, -5], "max": [5, 5, 5], "size": [10, 10, 10]}, result


#: names on both sides of every JS name rule: acronyms, instance separators, long numeric ids
NAMES = ["Leg_2", "Rock.003", "Fence-1", "Leg2", "DonkeyCart_800006", "Leg_", "Planter_12", "LEDStairs",
         "HDRISky", "UVGround", "GPUTerrain", "PondWater", "pond_water", "SkyAtmosphereBand0", "TV-Stand.2", ""]


def test_js_name_rules_mirror_conventions():
    """Audit 2026-09-24 N3/N5: `splitInstance` IS `conventions.split_instance` and `nameWords` IS
    `to_snake`'s words — the census/placement copies split `Fence-1` and `LEDStairs` otherwise."""
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
    """Audit 2026-09-24 N28: 40 cloud puffs over 3 km were `sky` to the census (its own
    per-instance box) and `content` to coverage (`drawableBox` = one 10 m puff)."""
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
