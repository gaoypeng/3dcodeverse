"""rain.js: splashes land where they are aimed, and a numeric `area` is not NaN."""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "rain.js")


def test_splashes_land_on_the_surfaces_they_are_given():
    """Drops hit the car roof at its real height, not THROUGH it onto the road;
    a steep face collects none, and a rejected sample is re-drawn, not dropped."""
    out = measure("""
import * as THREE from 'three';
import { makeSplashes } from './lib/rain.js';
const car = new THREE.Mesh(new THREE.BoxGeometry(2, 1.4, 4.5), new THREE.MeshStandardMaterial());
car.position.set(0, 0.7, 0);
car.updateMatrixWorld(true);
const area = { x: 0, z: 0, w: 20, d: 20 };
const rings = makeSplashes({ seed: 11, count: 300, surfaces: [car], area }).getObjectByName('SplashRings');
const off = rings.geometry.attributes.iOff.array, nrm = rings.geometry.attributes.iNrm.array;
let onRoof = 0, onGround = 0, sideways = 0;
for (let i = 0; i < off.length / 3; i++) {
  if (off[i * 3 + 1] > 1.4) onRoof++; else if (off[i * 3 + 1] < 0.1) onGround++;
  if (nrm[i * 3 + 1] < 0.4) sideways++;
}
const wall = new THREE.Mesh(new THREE.PlaneGeometry(8, 8),
    new THREE.MeshStandardMaterial({ side: THREE.DoubleSide }));
wall.rotation.x = -Math.PI / 2 + 1.4;   // 80 deg off horizontal
wall.position.set(0, 3, 0);
wall.updateMatrixWorld(true);
const onWall = makeSplashes({ seed: 4, count: 120, surfaces: [wall], area, surfaceBias: 1.0 })
    .getObjectByName('SplashRings');
const mixed = makeSplashes({ seed: 4, count: 120, surfaces: [wall], area }).getObjectByName('SplashRings');
let mixedHigh = 0;
const mo = mixed.geometry.attributes.iOff.array;
for (let i = 0; i < mo.length / 3; i++) if (mo[i * 3 + 1] > 0.5) mixedHigh++;
console.log(JSON.stringify({ onRoof, onGround, sideways, mixedHigh,
  wallStuck: onWall ? onWall.geometry.instanceCount : 0, mixedCount: mixed.geometry.instanceCount }));
""", _LIBS)
    assert out["onRoof"] > 60 and out["onGround"] > 40, out
    assert out["sideways"] == 0 and out["wallStuck"] == 0 and out["mixedHigh"] == 0, out
    assert out["mixedCount"] == 120, out


def test_a_square_area_given_as_one_number_is_not_nan():
    """`area: 12` read x and w as undefined, and every ground-fallback impact
    landed at NaN while still being created and drawn."""
    out = measure("""
import { makeSplashes } from './lib/rain.js';
const finite = (g) => {
  let bad = 0, total = 0;
  g.traverse((o) => {
    const a = o.geometry && o.geometry.getAttribute('iOff');
    if (!a) return;
    for (let i = 0; i < a.array.length; i++) { total++; if (!Number.isFinite(a.array[i])) bad++; }
  });
  return { bad, total };
};
console.log(JSON.stringify(finite(makeSplashes({ area: 12, y: 0, seed: 4 }))));
""", _LIBS)
    assert out["total"] > 0 and out["bad"] == 0, out
