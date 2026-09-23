"""terrain.js (72 recorded scenes import it through the starter's env.js): the ground ships
as a height function that agrees with its mesh; the cliff is one ribbon."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("terrain.js", "materials.js")

_PROBE = """
import * as THREE from 'three';
import { ground, cliff } from './lib/terrain.js';
const prng = (seed) => { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; };

// ground(): mesh + height agree, textured and tiled, seeded, flat() damps.
const g1 = ground({ size: 200, rand: prng(5), relief: 6 });
const g2 = ground({ size: 200, rand: prng(5), relief: 6 });
const g3 = ground({ size: 200, rand: prng(6), relief: 6 });
const pos = g1.mesh.geometry.attributes.position;
let drift = 0, same = 0, other = 0, minY = 1e9, maxY = -1e9;
for (let i = 0; i < pos.count; i += 7) {
  drift = Math.max(drift, Math.abs(g1.height(pos.getX(i), pos.getZ(i)) - pos.getY(i)));
  same = Math.max(same, Math.abs(g2.mesh.geometry.attributes.position.getY(i) - pos.getY(i)));
  other = Math.max(other, Math.abs(g3.mesh.geometry.attributes.position.getY(i) - pos.getY(i)));
  minY = Math.min(minY, pos.getY(i)); maxY = Math.max(maxY, pos.getY(i));
}
const flat = ground({ size: 200, rand: prng(5), relief: 6, flat: (x, z) => (Math.hypot(x, z) < 30 ? 1 : 0) });
const m = g1.mesh.material;

// cliff(): one connected ribbon, strata as vertex colours, faceAt on the mesh.
const c = cliff({ length: 300, height: 40, rand: prng(11) });
let meshes = 0; c.mesh.traverse((o) => { if (o.isMesh) meshes++; });
const geo = c.mesh.geometry, cp = geo.attributes.position;
const xs = [...new Set(Array.from({ length: cp.count }, (_, i) => Math.round(cp.getX(i) * 1000) / 1000))].sort((a, b) => a - b);
let maxGap = 0;
for (let i = 1; i < xs.length; i++) maxGap = Math.max(maxGap, xs[i] - xs[i - 1]);
const cell = (xs[xs.length - 1] - xs[0]) / (xs.length - 1);
const bb = new THREE.Box3().setFromObject(c.mesh);
const colAttr = geo.attributes.color;
const distinct = new Set();
for (let i = 0; i < colAttr.count; i++) distinct.add([0, 1, 2].map((k) => Math.round(colAttr.array[i * 3 + k] * 50)).join(','));
let fdrift = 0;
for (const i of [0, Math.floor(cp.count / 3), Math.floor(cp.count / 2), cp.count - 1]) {
  const p = c.faceAt(cp.getX(i) + 150, cp.getY(i));
  fdrift = Math.max(fdrift, Math.abs(p.z - cp.getZ(i)), Math.abs(p.x - cp.getX(i)), Math.abs(p.y - cp.getY(i)));
}
const c3 = cliff({ length: 120, height: 30, rand: prng(7), strata: [0xff0000, 0x00ff00, 0x0000ff] });
const p3 = c3.mesh.geometry.attributes.position, k3 = c3.mesh.geometry.attributes.color;
const domAt = (ty) => {
  let best = 0, bd = 1e9;
  for (let i = 0; i < p3.count; i++) { const d = Math.abs(p3.getX(i)) + Math.abs(p3.getY(i) - ty); if (d < bd) { bd = d; best = i; } }
  const r = k3.array[best * 3], g = k3.array[best * 3 + 1], b = k3.array[best * 3 + 2];
  return r > g && r > b ? 'r' : g > b ? 'g' : 'b';
};
// The default material comes from the SHARED library cache: retiling it in
// place reached back through that cache into every other asset of the look.
import * as MAT from './lib/materials.js';
const wall = MAT.soil();
const wallRepeatBefore = wall.map.repeat.x;
const small = ground({ size: 120, rand: prng(5) });
const big = ground({ size: 480, rand: prng(5) });
const leak = { wallRepeatBefore, wallRepeatAfter: wall.map.repeat.x,
  wallMaterialReused: small.mesh.material === wall, wallMapReused: small.mesh.material.map === wall.map,
  smallRepeat: small.mesh.material.map.repeat.x, bigRepeat: big.mesh.material.map.repeat.x,
  bumpIsMap: small.mesh.material.bumpMap === small.mesh.material.map,
  sharesGpuSource: small.mesh.material.map.source === big.mesh.material.map.source,
  wraps: small.mesh.material.map.wrapS === THREE.RepeatWrapping };
// A caller's OWN material is still honoured (and still retiled on a copy).
const mine = new THREE.MeshStandardMaterial({ color: 0x808080 });
const custom = ground({ size: 240, rand: prng(5), material: mine });
leak.customKept = custom.mesh.material === mine;

console.log(JSON.stringify({
  leak,
  ground: { drift, same, other, relief: maxY - minY, flatCentre: Math.abs(flat.height(0, 0)), flatEdge: Math.abs(flat.height(90, 90)),
    textured: !!m.map && !!m.roughnessMap, repeat: m.map ? m.map.repeat.x : 0,
    wraps: m.map ? m.map.wrapS === THREE.RepeatWrapping : false, receives: g1.mesh.receiveShadow, name: g1.mesh.name },
  cliff: { meshes, children: c.mesh.children.length, maxGap, cell, minY: bb.min.y, maxY: bb.max.y, zSpan: bb.max.z - bb.min.z,
    hasColor: !!colAttr, colorCount: colAttr.count, posCount: cp.count, distinct: distinct.size, drift: fdrift,
    band0: domAt(5), band1: domAt(15), band2: domAt(25), children3: c3.mesh.children.length },
}));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    return measure(_PROBE, _LIBS)


def test_ground_returns_the_height_function_it_displaced_with(probe):
    g = probe["ground"]
    assert g["drift"] < 1e-5, g
    assert g["relief"] > 3, g
    assert g["receives"] and g["name"] == "Ground"


def test_ground_is_seeded_and_flat_damps_relief(probe):
    g = probe["ground"]
    assert g["same"] == 0 and g["other"] > 0.5, g
    assert g["flatCentre"] < 1e-9 and g["flatEdge"] > 0.1, g


def test_ground_retiles_a_copy_never_the_shared_library_material(probe):
    """Measured 2026-09-01: `MAT.soil()` is a CACHED material whose maps are
    shared with every other caller of that look, and `ground()` set the repeat on
    it in place — a wall built with soil before a 480 m ground came out at
    repeat 40x40, and two grounds of different sizes fought over one texture."""
    k = probe["leak"]
    assert k["wallRepeatBefore"] == 1 and k["wallRepeatAfter"] == 1, k
    assert not k["wallMaterialReused"] and not k["wallMapReused"], k
    assert k["smallRepeat"] == 10 and k["bigRepeat"] == 40, k
    # bumpMap is the same object as map: one clone, or the emboss slides off.
    assert k["bumpIsMap"] and k["wraps"], k
    # A Texture clone shares its GPU source: the copy is state, not an upload.
    assert k["sharesGpuSource"], k
    # A material the caller owns is theirs — never swapped out underneath them.
    assert k["customKept"], k


def test_ground_textures_by_default_and_tiles_to_its_size(probe):
    g = probe["ground"]
    assert g["textured"] and g["wraps"]
    assert abs(g["repeat"] - 200 / 12) < 1e-6, g


def test_cliff_is_one_connected_ribbon_with_no_gaps(probe):
    c = probe["cliff"]
    assert c["meshes"] == 1 and c["children"] == 0
    assert c["maxGap"] <= c["cell"] * 1.5 + 1e-6, c


def test_cliff_strata_are_vertex_colors_on_the_face(probe):
    c = probe["cliff"]
    assert c["hasColor"] and c["colorCount"] == c["posCount"]
    assert c["distinct"] >= 6 and c["children3"] == 0
    assert (c["band0"], c["band1"], c["band2"]) == ("r", "g", "b"), c


def test_cliff_faceat_matches_the_displaced_mesh(probe):
    c = probe["cliff"]
    assert c["drift"] < 1e-4, c
    assert abs(c["minY"]) < 1e-6 and abs(c["maxY"] - 40) < 1e-6
    assert c["zSpan"] > 2, c
