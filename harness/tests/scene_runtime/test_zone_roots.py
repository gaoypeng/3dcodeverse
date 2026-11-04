"""D111: placement rows come from the zone roots the assembler marks; other roots are scenery (no rows)."""

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
import { sceneCensus } from './lib/host_census.mjs';
import { settleScene } from './lib/host_placement.mjs';
const brick = new THREE.MeshStandardMaterial({ name: 'Brick' }), metal = new THREE.MeshStandardMaterial({ name: 'Metal' });
const geo = (sx, sy, sz, x, y, z) => new THREE.BoxGeometry(sx, sy, sz).translate(x, y + sy / 2, z);
const box = (name, sx, sy, sz, x, y, z, m = brick) => { const o = new THREE.Mesh(geo(sx, sy, sz, 0, 0, 0), m); o.name = name; o.position.set(x, y, z); return o; };
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
console.log(JSON.stringify({
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
def test_a_zone_asset_mounted_on_scenery_is_attached_and_one_pushed_into_it_clips(tables):
    """A sconce flush on an env facade is mounted on it — the gate leaves it, and so does the boot settle (it used to drop a sign hung on scenery to the g..."""
    t = tables["street"]
    rows = {r["name"]: r for r in t["assets"]}
    assert rows["WallSconce"]["attached"] == ["FacadeWall"] and rows["ShopSign"]["attached"] == ["FacadeWall"]
    assert tables["settle"]["count"] == 0, tables["settle"]
    found = _findings(t)
    assert not [f for f in found if f[1] in ("floating", "unsupported")], found
    assert [t for _, k, t in found if k == "interpenetration"] == ["Street/Kiosk"], found
    (pair,) = t["interpenetrations"]
    assert (pair["a"], pair["zone_a"], pair["b"], pair["zone_b"]) == ("Kiosk", "Street", "FacadeWall", "StreetFacades")
