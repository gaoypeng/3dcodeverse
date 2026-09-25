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


def test_hidden_surfaces_do_not_collect_splashes_or_shelter_rain():
    """A hidden ancestor/material must agree with what is actually rendered."""
    out = measure("""
import * as THREE from 'three';
import { makeRain, makeSplashes } from './lib/rain.js';
const scene = new THREE.Scene(), hidden = new THREE.Group();
hidden.visible = false; scene.add(hidden);
const roof = new THREE.Mesh(new THREE.BoxGeometry(5, .3, 5), new THREE.MeshStandardMaterial());
roof.position.y = 4; hidden.add(roof);
const invisible = roof.clone(); invisible.material = roof.material.clone();
invisible.material.visible = false; invisible.position.y = 2; scene.add(invisible);
scene.updateMatrixWorld(true);
const surfaces = [roof, invisible];
const splashes = makeSplashes({ surfaces, area: 4, count: 60, surfaceBias: 0, crowns: false });
const impact = splashes.getObjectByName('SplashRings').geometry.attributes.iOff;
const rainfall = makeRain({ surfaces, radius: 3, count: 2, follow: false, groundY: 0 });
scene.add(rainfall); scene.updateMatrixWorld(true);
const camera = new THREE.PerspectiveCamera(); camera.position.set(0, 3, 6); camera.updateMatrixWorld(true);
rainfall.onBeforeRender({}, scene, camera, rainfall.geometry, rainfall.material);
rainfall.onAfterRender({}, scene, camera, rainfall.geometry, rainfall.material);
console.log(JSON.stringify({ heights: Array.from({length: impact.count}, (_, i) => impact.getY(i)),
  shelter: rainfall.userData.sampleShelterHeight(0, 0) }));
splashes.userData.dispose(); rainfall.userData.dispose();
""", _LIBS)
    assert out["shelter"] == 0, out
    assert out["heights"] == pytest.approx([0.012] * 60), out


def test_instanced_splash_normals_follow_affine_surface_and_reject_steep_faces():
    """The normal and lift belong to each tilted instance, including parent scale."""
    out = measure("""
import * as THREE from 'three';
import { makeSplashes } from './lib/rain.js';
const parent = new THREE.Group(); parent.rotation.y = .4; parent.scale.set(1.6, .8, 1.2);
const geometry = new THREE.PlaneGeometry(5, 5); geometry.rotateX(-Math.PI / 2);
const roof = new THREE.InstancedMesh(geometry, new THREE.MeshStandardMaterial({side: THREE.DoubleSide}), 1);
parent.add(roof); const instance = new THREE.Matrix4().makeRotationZ(.55);
instance.setPosition(0, 3, 0); roof.setMatrixAt(0, instance); parent.updateMatrixWorld(true);
const expected = new THREE.Vector3(0, 1, 0).applyMatrix3(new THREE.Matrix3()
  .getNormalMatrix(roof.matrixWorld.clone().multiply(instance))).normalize();
const center = new THREE.Vector3(0, 0, 0).applyMatrix4(roof.matrixWorld.clone().multiply(instance));
const splash = makeSplashes({surfaces: [roof], count: 80, surfaceBias: 1, ground: false, crowns: false});
const rings = splash.getObjectByName('SplashRings'), normal = rings.geometry.attributes.iNrm, off = rings.geometry.attributes.iOff;
let normalError = 0, liftError = 0;
for (let i = 0; i < off.count; i++) {
  normalError = Math.max(normalError, new THREE.Vector3().fromBufferAttribute(normal, i).distanceTo(expected));
  liftError = Math.max(liftError, Math.abs(new THREE.Vector3().fromBufferAttribute(off, i).sub(center).dot(expected) - .012));
}
instance.makeRotationZ(1.48).setPosition(0, 3, 0); roof.setMatrixAt(0, instance);
roof.computeBoundingBox(); roof.computeBoundingSphere();
const steep = makeSplashes({surfaces: [roof], count: 40, surfaceBias: 1, ground: false, crowns: false});
console.log(JSON.stringify({normalError, liftError, count: off.count, steepCount: steep.children.length}));
splash.userData.dispose(); steep.userData.dispose();
""", _LIBS)
    assert out["count"] == 80, out
    assert out["normalError"] < 1e-6 and out["liftError"] < 1e-6, out
    assert out["steepCount"] == 0, out
