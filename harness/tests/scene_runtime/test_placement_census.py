"""Synthetic census coverage for placement, support, overlap, and exemptions."""

from __future__ import annotations

import pytest

from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]


SCENE_JS = """
import * as THREE from 'three';
import { sceneCensus } from './lib/host_census.mjs';
const mat = new THREE.MeshStandardMaterial();
const box = (name, sx, sy, sz, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat); m.name = name; m.position.set(x, y + sy / 2, z); return m; };
const scene = new THREE.Scene();
const env = new THREE.Group(); env.name = 'Environment'; scene.add(env);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 64, 64), mat); ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; env.add(ground);
const sky = new THREE.Mesh(new THREE.SphereGeometry(600, 16, 8), mat); sky.name = 'SkyDome'; env.add(sky);
const pond = new THREE.Mesh(new THREE.PlaneGeometry(10, 10), mat); pond.rotation.x = -Math.PI / 2; pond.position.set(-20, 0.2, -20);
const pondGroup = new THREE.Group(); pondGroup.name = 'PondWater'; pondGroup.add(pond); env.add(pondGroup);
env.add(new THREE.DirectionalLight());
const zone = new THREE.Group(); zone.name = 'Yard'; scene.add(zone);
zone.add(box('Crate', 1, 1, 1, 0, 0, 0));
zone.add(box('Lantern', 0.4, 0.6, 0.4, 5, 0.3, 0));
zone.add(box('Anvil', 1, 1, 1, -5, -0.5, 0));
zone.add(box('CrateA', 1, 1, 1, 10, 0, 0));
zone.add(box('CrateB', 1, 1, 1, 10.5, 0, 0));
const bird = box('Bird', 0.3, 0.2, 0.3, 0, 4, 5); bird.userData.placement = 'free'; zone.add(bird);
const table = new THREE.Group(); table.name = 'Table'; table.position.set(-10, 0, 5);
for (const [lx, lz] of [[-0.6, -0.3], [0.6, -0.3], [-0.6, 0.3], [0.6, 0.3]]) table.add(box('Leg', 0.05, 0.72, 0.05, lx, 0, lz));
table.add(box('Top', 1.4, 0.03, 0.8, 0, 0.72, 0)); zone.add(table);
zone.add(box('Cup', 0.08, 0.1, 0.08, -10, 0.75, 5));
zone.add(box('Chair', 0.4, 0.45, 0.4, -10, 0, 5.2));
const tree = new THREE.Group(); tree.name = 'Tree'; tree.position.set(15, 0, 15);
tree.add(box('Trunk', 0.3, 2, 0.3, 0, 0, 0)); tree.add(box('Canopy', 4, 3, 4, 0, 2, 0)); zone.add(tree);
zone.add(box('Bench', 1.6, 0.9, 0.5, 16, 0, 15));
zone.add(box('Boat', 2, 0.6, 0.8, -20, 0.0, -20));
const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(0.6, 1), mat); rock.name = 'Rock'; rock.position.set(8, 0.2, -8); zone.add(rock);
const inst = new THREE.InstancedMesh(new THREE.BoxGeometry(0.2, 0.2, 0.2), mat, 50); inst.name = 'Pebbles'; zone.add(inst);
scene.updateMatrixWorld(true);
const c = sceneCensus(scene, THREE, { placement: true });
console.log(JSON.stringify({ ground_y: c.ground_y, placement: c.placement, plain_has_placement: 'placement' in sceneCensus(scene, THREE) }));
"""


@pytest.fixture(scope="module")
def table() -> dict:
    out = run_node_json(SCENE_JS)
    assert out["ground_y"] == 0 and out["plain_has_placement"] is False   # the render-time census stays cheap
    return out["placement"]


def _row(table, name):
    rows = [a for a in table["assets"] if a["name"] == name]
    assert len(rows) == 1, (name, [a["name"] for a in table["assets"]])
    return rows[0]


def test_counts_exemptions_and_budget(table):
    assert table["total"] == 17 and table["checked"] == 12 and table["truncated"] is False
    assert table["exempt"] == {"backdrop": 3, "free": 1, "instanced": 1}
    assert {a["name"] for a in table["assets"] if a["exempt"] == "backdrop"} == {"Ground", "SkyDome", "PondWater"}
    assert _row(table, "Bird")["exempt"] == "free" and _row(table, "Pebbles")["exempt"] == "instanced"
    assert table["duration_ms"] < 2000 and table["notes"] == []
    assert table["thresholds"] == {"contact_m": 0.02, "floating_m": 0.05, "sunk_m": 0.1, "overlap_min": 0.2}


def test_supported_floating_and_sunk_boxes(table):
    crate = _row(table, "Crate")
    assert crate["zone"] == "Yard" and crate["ground_gap_m"] == 0 and crate["support"] == "Ground" and crate["supported"]
    assert crate["bbox"] == {"min": [-0.5, 0, -0.5], "max": [0.5, 1, 0.5], "size": [1, 1, 1]}
    lantern = _row(table, "Lantern")
    assert lantern["ground_gap_m"] == pytest.approx(0.3, abs=1e-3) and lantern["floating"] and lantern["touches_nothing"]
    assert lantern["support"] == "Ground" and lantern["sunk_m"] == 0 and not lantern["supported"]
    anvil = _row(table, "Anvil")
    assert anvil["sunk_m"] == pytest.approx(0.5, abs=1e-3) and anvil["sunk_into"] == "Ground" and anvil["ground_gap_m"] == pytest.approx(-0.5, abs=1e-3)
    assert anvil["supported"] and not anvil["floating"]


def test_interpenetration_is_a_pair_not_a_burial(table):
    pairs = table["interpenetrations"]
    assert [(p["a"], p["b"], p["aabb_overlap"]) for p in pairs] == [("CrateA", "CrateB", 0.5)]
    assert pairs[0]["inside_frac"] > 0.2 and pairs[0]["zone_a"] == "Yard"
    for name in ("CrateA", "CrateB"):
        r = _row(table, name)
        assert r["sunk_m"] == 0 and r["ground_gap_m"] == 0 and r["attached"] == ["CrateB" if name == "CrateA" else "CrateA"]


def test_no_false_positives_on_legitimate_placements(table):
    """Nested furniture, canopy, water, and terrain contacts classify correctly."""
    for name, support in (("Table", "Ground"), ("Cup", "Table"), ("Chair", "Ground"), ("Tree", "Ground"), ("Bench", "Ground")):
        r = _row(table, name)
        assert r["ground_gap_m"] == pytest.approx(0, abs=1e-3) and r["support"] == support and r["sunk_m"] == 0, (name, r)
        assert not r["floating"] and not r["touches_nothing"]
    assert _row(table, "Chair")["attached"] == ["Table"] and _row(table, "Bench")["attached"] == ["Tree"]
    boat = _row(table, "Boat")
    assert boat["on_water"] and boat["supported"] and not boat["floating"] and boat["sunk_into"] == "PondWater"
    rock = _row(table, "Rock")
    assert rock["sunk_into"] == "Ground" and 0.3 < rock["sunk_m"] < 0.5 and not rock["floating"]



SETTLE_JS = """
import * as THREE from 'three';
import fs from 'node:fs';
import { settleScene, placementTable } from './lib/host_placement.mjs';
const words = JSON.parse(fs.readFileSync('./lib/placement_words.json', 'utf8'));
const mat = new THREE.MeshStandardMaterial();
const box = (name, sx, sy, sz, x, y, z) => { const m = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), mat); m.name = name; m.position.set(x, y + sy / 2, z); return m; };
const scene = new THREE.Scene();
const env = new THREE.Group(); env.name = 'Environment'; scene.add(env);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60, 64, 64), mat); ground.name = 'Ground'; ground.rotation.x = -Math.PI / 2; env.add(ground);
const pond = new THREE.Mesh(new THREE.PlaneGeometry(10, 10), mat); pond.rotation.x = -Math.PI / 2; pond.position.set(-20, 0.2, -20);
const pondGroup = new THREE.Group(); pondGroup.name = 'PondWater'; pondGroup.add(pond); env.add(pondGroup);
const zone = new THREE.Group(); zone.name = 'Yard'; scene.add(zone);
zone.add(box('Lantern', 0.4, 0.6, 0.4, 5, 0.3, 0));            // floating 0.3, touches nothing
zone.add(box('Anvil', 1, 1, 1, -5, -0.5, 0));                   // sunken 0.5 into Ground
const bird = box('Bird', 0.3, 0.2, 0.3, 0, 4, 5); bird.userData.placement = 'free'; zone.add(bird);
zone.add(box('Boat', 2, 0.6, 0.8, -20, 0.0, -20));              // foot at pond level: on water
const rock = box('Rock', 1, 1.2, 1, 8, -0.6, -8); zone.add(rock);  // half-buried rock: partial-ok, frac 0.5 <= 0.75
const upper = box('UpperGround', 8, 1, 8, 9, 0, 20); env.add(upper);    // an uphill terrace: slab y 0..1, top face at y=1
const aqueduct = box('Aqueduct', 6, 0.5, 1, 5, 0.4, 20); zone.add(aqueduct); // spans flat ground -> terrace: downhill col floats, uphill col sunk 0.6
const shelf = box('Shelf', 1, 0.1, 0.4, 12, 1.2, 0);            // floating but TOUCHING the wall
const wall = box('Wall', 0.2, 2.5, 3, 12.5, 0, 0); zone.add(wall); zone.add(shelf);
shelf.position.x = 12.35;                                        // AABBs meet the wall's
scene.updateMatrixWorld(true);
const settle = settleScene(scene, THREE, { groundY: 0, words });
const after = placementTable(scene, THREE, { groundY: 0 });
console.log(JSON.stringify({ settle, after: { assets: after.assets.map((a) => ({ name: a.name, gap: a.ground_gap_m, sunk: a.sunk_m, supported: a.supported, exempt: a.exempt })) } }));
"""


@pytest.fixture(scope="module")
def settled() -> dict:
    return run_node_json(SETTLE_JS)


def test_settle_seats_floating_and_sunken_and_reports_the_moves(settled):
    moves = {m["name"]: m for m in settled["settle"]["moves"]}
    assert moves["Lantern"]["why"] == "floating" and moves["Lantern"]["dy_m"] == pytest.approx(-0.295, abs=5e-3)
    assert moves["Anvil"]["why"] == "sunken" and moves["Anvil"]["dy_m"] == pytest.approx(0.46, abs=5e-3)
    after = {a["name"]: a for a in settled["after"]["assets"]}
    assert after["Lantern"]["supported"] and after["Lantern"]["gap"] == pytest.approx(0.005, abs=3e-3)
    assert after["Anvil"]["sunk"] == pytest.approx(0.04, abs=5e-3)   # reseated with the 4 cm embed


def test_settle_leaves_exempt_water_partial_and_mounted_alone(settled):
    touched = {m["name"] for m in settled["settle"]["moves"]}
    assert touched == {"Lantern", "Anvil"}, touched
    # the slope guard: Aqueduct spans flat ground onto a terrace — one foot column
    # rests / floats low while another is buried 0.6 m in the terrace.  Columns
    # disagree, so settle must refuse (the santorini stairway lesson).
    assert "Aqueduct" not in touched
    after = {a["name"]: a for a in settled["after"]["assets"]}
    assert after["Bird"]["exempt"] == "free"                          # tagged free: untouched
    assert after["Boat"]["supported"]                                 # on water: untouched
    assert after["Rock"]["sunk"] == pytest.approx(0.6, abs=5e-3)      # half-buried rock stays half-buried
