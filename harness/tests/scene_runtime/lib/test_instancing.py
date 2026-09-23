"""instancing.js — regressions: a multi-material mesh kept only material[0], and a
bucket mergeGeometries could not merge was dropped (a part silently missing)."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("instancing.js",)

# A cottage: walls (a MULTI-material box, timber on +Z), a roof of a second
# material, and a chimney that shares the wall material 2.75 m up — the
# sub-part offset that a bare geometry.clone() would lose.
_ASSET = """
import * as THREE from 'three';
const plaster = new THREE.MeshStandardMaterial({ color: 0xb9a891 });
const tile = new THREE.MeshStandardMaterial({ color: 0x8a4b36 });
const timber = new THREE.MeshStandardMaterial({ color: 0x6d5540 });
function cottage(opts = {}) {
  const g = new THREE.Group();
  const walls = new THREE.Mesh(new THREE.BoxGeometry(2.6, 1.9, 2.6),
      opts.plain ? plaster : [plaster, plaster, plaster, plaster, timber, plaster]);
  walls.position.y = 0.95;
  g.add(walls);
  const roof = new THREE.Mesh(new THREE.ConeGeometry(2, 1.15, 4), tile);
  roof.position.y = 2.5;
  g.add(roof);
  const chimney = new THREE.Mesh(new THREE.BoxGeometry(0.38, 1.1, 0.38), plaster);
  chimney.position.set(0.62, 2.75, 0.55);
  g.add(chimney);
  if (opts.rootAt) g.position.set(...opts.rootAt);
  return g;
}
const tris = (geo) => (geo.index ? geo.index.count : geo.attributes.position.count) / 3;
"""


def test_a_multi_material_mesh_keeps_its_second_material():
    """`material[0]` for the whole geometry painted the timber front face with
    plaster.  Each geometry group is its own draw; no triangle lost or doubled."""
    out = measure(_ASSET + """
import { instanceAsset } from './lib/instancing.js';
const split = instanceAsset(cottage, [{ position: [0, 0, 0] }]);
const plain = instanceAsset(cottage, [{ position: [0, 0, 0] }],
                            { buildOpts: { plain: true } });
const rows = (g) => g.children.map((m) => ({
  mat: m.material.color.getHexString(), tris: tris(m.geometry) }))
  .sort((a, b) => a.mat.localeCompare(b.mat));
const front = split.children.find((m) => m.material.color.getHexString() === '6d5540');
front.geometry.computeBoundingBox();
console.log(JSON.stringify({
  split: rows(split), plain: rows(plain),
  // the +Z face, and only that face
  frontZ: +front.geometry.boundingBox.min.z.toFixed(3),
  backZ: +front.geometry.boundingBox.max.z.toFixed(3),
}));
""", _LIBS)
    assert out["split"] == [
        {"mat": "6d5540", "tris": 2},   # the one face
        {"mat": "8a4b36", "tris": 8},   # the roof cone
        {"mat": "b9a891", "tris": 22},  # five box faces + the chimney
    ], out
    # Same triangles either way — the split adds draws, not geometry.
    assert sum(r["tris"] for r in out["split"]) == 32, out
    assert out["plain"] == [{"mat": "8a4b36", "tris": 8},
                            {"mat": "b9a891", "tris": 24}], out
    assert out["frontZ"] == out["backZ"] == 1.3, out


def test_a_bucket_that_cannot_merge_falls_back_to_draws_not_to_a_hole():
    """`mergeGeometries` returns null on an attribute mismatch (a vertex-coloured
    piece beside a plain one) and that bucket was dropped."""
    out = measure(_ASSET + """
import { instanceAsset } from './lib/instancing.js';
const mixed = () => {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0x777777, vertexColors: true });
  const a = new THREE.BoxGeometry(1, 1, 1);
  const n = a.attributes.position.count;
  a.setAttribute('color', new THREE.BufferAttribute(new Float32Array(n * 3).fill(0.4), 3));
  const b = new THREE.BoxGeometry(1, 1, 1);          // no color attribute
  const m1 = new THREE.Mesh(a, mat); m1.position.y = 0.5;
  const m2 = new THREE.Mesh(b, mat); m2.position.y = 2.0;
  g.add(m1, m2);
  return g;
};
const g = instanceAsset(mixed, [{ position: [0, 0, 0] }, { position: [5, 0, 0] }]);
const geo = g.children[0].geometry;
geo.computeBoundingBox();
const c = geo.attributes.color;
let lo = 9, hi = -9;
for (let i = 0; i < c.count; i++) { lo = Math.min(lo, c.getX(i)); hi = Math.max(hi, c.getX(i)); }
console.log(JSON.stringify({
  draws: g.children.length, tris: g.children.reduce((a, m) => a + tris(m.geometry), 0),
  top: +geo.boundingBox.max.y.toFixed(3), hasColor: !!c, lo, hi,
}));
""", _LIBS)
    assert out["draws"] == 1, out          # harmonised, so still ONE draw
    assert out["tris"] == 24, out          # and both boxes are there
    assert out["top"] == 2.5, out          # including the one 2 m up
    # The geometry that had no colour is filled with white, not with black:
    # a vertexColors material reads a missing attribute as zero and the part
    # renders black.
    assert out["lo"] == pytest.approx(0.4), out
    assert out["hi"] == pytest.approx(1.0), out


