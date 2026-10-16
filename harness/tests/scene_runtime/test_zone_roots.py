"""D111: placed-asset rows come from the plan's ZONE roots only — the roots the harness marks itself.

Argon loop iteration 4 (scene_threejs, 2026-10-15): every top-level scene child was a "zone", so an env
wall merged per material (body + piers in one mesh, the flashing strip in another) became rows that
overlap by construction, a two-mesh bollard was a cap "interpenetrating" its post, and in two runs
(r1_02, r1_10) every final placement ERROR sat on such scenery.  The assembler now marks each zone root with
``userData[ZONE_ROOT_KEY]`` (the census GLB's zone-node extras for scene_blender); every other root is
scenery: no rows, never paired with other scenery, not in the free share — still a support, something
to sink into, something a zone asset is mounted on (``attached``, and the boot settle leaves it) and
something a zone asset can clip (a zone-vs-scenery interpenetration names the zone asset).
"""

from __future__ import annotations

import re

import pytest

from codeverse3d.config import get_settings
from codeverse3d.conventions import ZONE_ROOT_KEY
from codeverse3d.languages.scene_threejs import render_scene_js
from codeverse3d.spatial.node import runtime_js_dir
from codeverse3d.spatial.scene_placement import placement_findings
from tests.scene_runtime.conftest import needs_node, run_node_json

SCENE_JS = """
import * as THREE from 'three';
import fs from 'node:fs';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { sceneCensus } from './lib/host_census.mjs';
import { settleScene } from './lib/host_placement.mjs';
const brick = new THREE.MeshStandardMaterial({ name: 'Brick' }), metal = new THREE.MeshStandardMaterial({ name: 'Metal' });
const geo = (sx, sy, sz, x, y, z) => new THREE.BoxGeometry(sx, sy, sz).translate(x, y + sy / 2, z);
const box = (name, sx, sy, sz, x, y, z, m = brick) => { const o = new THREE.Mesh(geo(sx, sy, sz, 0, 0, 0), m); o.name = name; o.position.set(x, y, z); return o; };
function build(mark, withScenery) {
  const scene = new THREE.Scene();
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200, 8, 8), brick); ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  const yard = new THREE.Group(); yard.name = 'Yard';
  if (mark) yard.userData[mark] = 'Yard';
  yard.add(box('Crate', 1, 1, 1, 0, 0, 0));
  yard.add(box('Anvil', 1, 1, 1, -5, -0.5, 0));                 // sunken 0.5 m into Ground: a real finding
  yard.add(box('CrateA', 1, 1, 1, 10, 0, 0)); yard.add(box('CrateB', 1, 1, 1, 10.5, 0, 0));   // a real pair
  // the assembler's probe nests the marked root in a wrapper: still found
  const wrap = new THREE.Group(); wrap.name = '__zone__yard'; wrap.add(yard); scene.add(wrap);
  if (withScenery) {
    // an env parapet built and MERGED BY MATERIAL: wall body + piers in one brick mesh, the flashing
    // strip in a metal one, flush on the wall and pierced by the piers (iteration 4, r1_02)
    const parapet = new THREE.Group(); parapet.name = 'ParapetWall_North';
    const body = [geo(25, 1.1, 0.5, 0, 0, -30)];
    for (let i = -3; i <= 3; i++) body.push(geo(0.6, 1.3, 0.6, i * 4, 0, -30));
    parapet.add(new THREE.Mesh(mergeGeometries(body), brick));
    parapet.add(new THREE.Mesh(geo(25, 0.14, 0.5, 0, 1.0, -30), metal));
    scene.add(parapet);
    // harbour bollards: a cap and a post as two unnamed sibling meshes (iteration 4, r1_10)
    const harbor = new THREE.Group(); harbor.name = 'HarborSurroundings';
    for (const z of [-16, -6, 4]) {
      harbor.add(box('', 0.4, 0.72, 0.4, 20, 0, z, metal));
      harbor.add(box('', 0.36, 0.3, 0.36, 20, 0.6, z, metal));   // its foot inside the post's top
    }
    scene.add(harbor);
    // a room shell (the RoomShell name exemption this replaces): a lintel running into the lid
    const shell = new THREE.Group(); shell.name = 'RoomShell';
    shell.add(box('Ceiling', 8.4, 0.2, 6.4, -30, 3.0, 30)); shell.add(box('RoomShellWallBack_0Lintel', 1.2, 0.5, 0.2, -30, 2.8, 33.1));
    scene.add(shell);
    // a free-tagged scenery root never enters the free share
    const birds = box('Gull', 0.3, 0.2, 0.3, 0, 8, 0); birds.userData.placement = 'free'; scene.add(birds);
  }
  scene.updateMatrixWorld(true);
  return sceneCensus(scene, THREE, { placement: true }).placement;
}
// a marked street zone whose things hang on, and push into, an env facade (the scenery)
function street(mark) {
  const scene = new THREE.Scene();
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200, 8, 8), brick); ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  const facades = new THREE.Group(); facades.name = 'StreetFacades';
  facades.add(box('FacadeWall', 20, 6, 0.5, 0, 0, -5));                  // z in [-5.25, -4.75]
  scene.add(facades);
  const zone = new THREE.Group(); zone.name = 'Street'; if (mark) zone.userData[mark] = 'Street';
  for (const x of [-6, -2, 2, 6]) zone.add(box('WallSconce', 0.2, 0.3, 0.2, x, 2.2, -4.65));   // flush on the facade
  zone.add(box('ShopSign', 2, 0.6, 0.1, 0, 3.0, -4.7));                // flush on the facade, 3 m up
  zone.add(box('Kiosk', 1, 2, 1, 8, 0, -4.6));                         // pushed 0.35 m into the facade
  scene.add(zone);
  scene.updateMatrixWorld(true);
  return scene;
}
const KEY = process.argv[2];
const words = JSON.parse(fs.readFileSync('./lib/placement_words.json', 'utf8'));
const settled = street(KEY);
const settle = settleScene(settled, THREE, { groundY: 0, words });
console.log(JSON.stringify({ zoneOnly: build(KEY, false), marked: build(KEY, true), unmarked: build('', true),
  street: sceneCensus(street(KEY), THREE, { placement: true }).placement, settle }));
"""


@pytest.fixture(scope="module")
def tables() -> dict:
    return run_node_json(SCENE_JS.replace("process.argv[2]", repr(ZONE_ROOT_KEY)))


def _findings(table: dict) -> list[tuple[str, str, str]]:
    report = placement_findings(table, thresholds=get_settings().placement)
    return sorted((f.severity.value, f.data.get("kind", ""), f.target or "") for f in report.findings
                  if f.data.get("kind") != "summary")


def test_the_key_is_one_word_on_both_sides():
    src = (runtime_js_dir() / "lib" / "host_placement.mjs").read_text()
    m = re.search(r"export const ZONE_ROOT_KEY = '([^']+)';", src)
    assert m and m.group(1) == ZONE_ROOT_KEY


def test_the_assembled_scene_marks_every_zone_root():
    text = render_scene_js(["yard", "quay"], [], [])
    assert f"g.userData['{ZONE_ROOT_KEY}'] = name;" in text


@needs_node
@pytest.mark.node
def test_env_scenery_yields_no_rows_while_the_zone_findings_are_unchanged(tables):
    marked, zone_only = tables["marked"], tables["zoneOnly"]
    assert {r["zone"] for r in marked["assets"]} == {"Yard"}
    assert marked["scenery"]["n"] == 5 and {"ParapetWall_North", "HarborSurroundings", "RoomShell", "Ground"} <= set(marked["scenery"]["names"])
    assert not marked["exempt"].get("free"), "a free-tagged scenery root is not in the free share"
    # the room shell (walls, lid, lintels abutting by construction): no row, no pair — the 2026-10-15
    # `RoomShell…` name exemption this replaces (24 of 41 Blender ERRORs in loop iteration 0)
    assert not [r for r in marked["assets"] if r["name"] in ("Ceiling", "RoomShellWallBack_0Lintel")]
    found = _findings(marked)
    assert found == _findings(zone_only), "scenery changes nothing about the zone's own findings"
    kinds = {(k, t) for _, k, t in found}
    assert ("sunken", "Yard/Anvil") in kinds and any(k == "interpenetration" for k, _ in kinds), found
    assert all(t.startswith("Yard/") or t == "scene" for _, _, t in found), found


@needs_node
@pytest.mark.node
def test_a_zone_asset_mounted_on_scenery_is_attached_and_one_pushed_into_it_clips(tables):
    """A sconce flush on an env facade is mounted on it — the gate leaves it, and so does the boot
    settle (it used to drop a sign hung on scenery to the ground: the "touching a neighbour" test
    looked at zone assets only) — while a kiosk pushed into the facade is still an interpenetration,
    reported on the zone asset whichever box is smaller."""
    t = tables["street"]
    rows = {r["name"]: r for r in t["assets"]}
    assert rows["WallSconce"]["attached"] == ["FacadeWall"] and rows["ShopSign"]["attached"] == ["FacadeWall"]
    assert tables["settle"]["count"] == 0, tables["settle"]
    found = _findings(t)
    assert not [f for f in found if f[1] in ("floating", "unsupported")], found
    assert [t for _, k, t in found if k == "interpenetration"] == ["Street/Kiosk"], found
    (pair,) = t["interpenetrations"]
    assert (pair["a"], pair["zone_a"], pair["b"], pair["zone_b"]) == ("Kiosk", "Street", "FacadeWall", "StreetFacades")


@needs_node
@pytest.mark.node
def test_without_the_mark_the_scenery_is_measured_as_before(tables):
    """The control: the same scene unmarked (a scene the harness did not assemble) still measures
    every top-level child — which is exactly the artefact the mark removes."""
    pairs = tables["unmarked"]["interpenetrations"]
    zones = {p["zone_a"] for p in pairs} | {p["zone_b"] for p in pairs}
    assert {"ParapetWall_North", "HarborSurroundings"} <= zones, pairs
    assert tables["unmarked"]["scenery"]["n"] == 0
