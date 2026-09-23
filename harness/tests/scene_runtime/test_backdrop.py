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
