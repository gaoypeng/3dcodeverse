"""merge.js — regressions."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("merge.js",)


def test_a_mirrored_piece_is_not_baked_inside_out():
    """`scale.x = -1` reverses the winding once baked."""
    out = measure("""
import * as THREE from 'three';
import { mergeStatic } from './lib/merge.js';
const facing = (mesh) => {
  const g = mesh.geometry;
  const p = g.attributes.position, n = g.attributes.normal;
  const idx = g.index;
  const tri = idx ? idx.count / 3 : p.count / 3;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  let out = 0;
  for (let t = 0; t < tri; t++) {
    const i0 = idx ? idx.getX(t * 3) : t * 3;
    const i1 = idx ? idx.getX(t * 3 + 1) : t * 3 + 1;
    const i2 = idx ? idx.getX(t * 3 + 2) : t * 3 + 2;
    a.fromBufferAttribute(p, i0);
    b.fromBufferAttribute(p, i1).sub(a);
    c.fromBufferAttribute(p, i2).sub(a);
    b.cross(c);
    const vn = new THREE.Vector3().fromBufferAttribute(n, i0);
    if (b.dot(vn) > 0) out++;
  }
  return [out, tri];
};
const mk = (sx, geo) => {
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x998877 }));
  m.position.set(4, 1, 0); m.rotation.set(0.2, 0.5, 0.13); m.scale.x = sx;
  return m;
};
const plain = facing(mergeStatic([mk(1, new THREE.BoxGeometry(0.5, 1.9, 1.1))]));
const mirrored = facing(mergeStatic([mk(-1, new THREE.BoxGeometry(0.5, 1.9, 1.1))]));
// the non-indexed path flips vertex runs instead of index triples
const nonIdx = facing(mergeStatic(
    [mk(-1, new THREE.BoxGeometry(0.5, 1.9, 1.1).toNonIndexed())]));
// a mirrored piece merged NEXT TO a plain one must not disturb it
const both = facing(mergeStatic([mk(1, new THREE.BoxGeometry(1, 1, 1)),
                                 mk(-1, new THREE.BoxGeometry(1, 1, 1))]));
console.log(JSON.stringify({ plain, mirrored, nonIdx, both }));
""", _LIBS)
    for name in ("plain", "mirrored", "nonIdx", "both"):
        out_faces, tris = out[name]
        assert tris > 0 and out_faces == tris, (
            f"{name}: {out_faces}/{tris} triangles wind outward")
