"""Rendered water lighting and refracted caustic transport regressions."""
from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure

pytestmark = pytest.mark.node


def test_normal_texture_resolution_preserves_wave_slopes():
    out = measure("""
import { makeWaterNormals } from './lib/water.js';
const slopes = (size, broadband) => {
 const texture = makeWaterNormals(size, {broadband}), data = texture.image.data;
 let sum = 0;
 for(let i = 0; i < data.length; i += 4) {
  const x = data[i] / 255 * 2 - 1, y = data[i + 1] / 255 * 2 - 1, z = data[i + 2] / 255 * 2 - 1;
  sum += (x * x + y * y) / (z * z);
 }
 texture.dispose(); return Math.sqrt(sum / (data.length / 4));
};
console.log(JSON.stringify({ narrow: [128, 256, 512].map(s => slopes(s, false)),
 broad: [128, 256, 512].map(s => slopes(s, true)) }));
""", ("water.js",))
    for spectrum in out.values():
        assert spectrum == pytest.approx([spectrum[1]] * 3, rel=0.025), out
    # The default 256px appearance is preserved, not flattened to the 512px one.
    assert out["narrow"][1] == pytest.approx(0.6003103576558382), out


def test_underwater_extinction_is_finite_nonnegative_and_caller_owned():
    out = measure("""
import * as THREE from 'three';
import { patchUnderwater } from './lib/submerged.js';
const invalid = [-1, NaN, Infinity, [-1, .2, .3], [0, NaN, 1], [0, 1],
 new THREE.Vector3(0, -.2, .3), new THREE.Vector3(0, Infinity, .3)];
let rejected = 0;
for(const extinction of invalid) {
 const material = new THREE.MeshStandardMaterial();
 try { patchUnderwater(material, {extinction}); }
 catch(error) { if(error instanceof RangeError) rejected++; else throw error; }
 material.dispose();
}
const input = new THREE.Vector3(0, .2, .3), material = new THREE.MeshStandardMaterial();
patchUnderwater(material, {extinction: input});
const before = material.userData.uniforms.uSubExt.value.toArray(); input.set(1, 1, 1);
console.log(JSON.stringify({rejected, total: invalid.length, before,
 after: material.userData.uniforms.uSubExt.value.toArray()}));
material.dispose();
""", ("submerged.js",))
    assert out["rejected"] == out["total"], out
    assert out["before"] == out["after"] == [0, 0.2, 0.3], out


def test_water_sums_fill_and_caustics_follow_the_refracted_beam():
    code, output = compile_scene(r"""
import * as THREE from 'three';
import { makeOcean } from './lib/water.js';
import { patchCaustics } from './lib/caustics.js';
import { withRendererState } from './lib/shader.js';
export function createScene({renderer}) {
 const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(45, 1, .1, 50);
 camera.position.set(0, 4, 5); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
 const sky = new THREE.HemisphereLight(0x6ca1ca, 0x101010, .7);
 const fill = new THREE.AmbientLight(0xc99771, .45);
 const other = new THREE.AmbientLight(0x637486, .3);
 const key = new THREE.DirectionalLight(0xffbbaa, 2); key.position.set(4, 7, 1);
 key.target.position.set(1, 2, -2); scene.add(sky, fill, other, key, key.target);
 const water = makeOcean(6, 6, { rttSize: 64 }); scene.add(water); scene.updateMatrixWorld(true);
 const checkColor = (a, b) => {
  if (Math.max(...a.toArray().map((v, i) => Math.abs(v - b.toArray()[i]))) > 1e-10)
   throw Error('Water omitted ambient/hemisphere illumination');
 };
 water.onBeforeRender(renderer, scene, camera);
 checkColor(water.material.uniforms.ambientColor.value, sky.color.clone().multiplyScalar(.7)
  .add(fill.color.clone().multiplyScalar(.45)).add(other.color.clone().multiplyScalar(.3)).multiplyScalar(1 / Math.PI));
 const expectedDirection = key.position.clone().sub(key.target.position).normalize();
 if(water.material.uniforms.sunDirection.value.distanceTo(expectedDirection) > 1e-10) throw Error('Water ignored light target');
 sky.visible = false; fill.visible = false; other.visible = false;
 water.onBeforeRender(renderer, scene, camera);
 checkColor(water.material.uniforms.ambientColor.value, new THREE.Color(0, 0, 0));
 scene.remove(water); water.userData.dispose();
 const pinned = makeOcean(6, 6, { rttSize: 64, ambient: 0x123456, sunDir: new THREE.Vector3(1, 2, 3), sunColor: 0xabcdef });
 scene.add(pinned); scene.updateMatrixWorld(true); pinned.onBeforeRender(renderer, scene, camera);
 checkColor(pinned.material.uniforms.ambientColor.value, new THREE.Color(0x123456));
 checkColor(pinned.material.uniforms.sunColor.value, new THREE.Color(0xabcdef));
 if(pinned.material.uniforms.sunDirection.value.distanceTo(new THREE.Vector3(1, 2, 3).normalize()) > 1e-10) throw Error('Water changed pinned direction');
 scene.remove(pinned); pinned.userData.dispose();

 const basin = new THREE.Scene(), top = new THREE.OrthographicCamera(-1, 1, 1, -1, .1, 20);
 top.position.set(0, 4, 0); top.up.set(0, 0, -1); top.lookAt(0, -1, 0); top.updateMatrixWorld(true);
 const sun = new THREE.Vector3(1, .1, 0).normalize();
 const material = new THREE.MeshStandardMaterial({color: 0xffffff, roughness: 1, side: THREE.DoubleSide});
 patchCaustics(material, {sunDir: sun, level: 0, strength: 1, color: 0xffffff, depthFade: 3});
 const geometry = new THREE.PlaneGeometry(2, 2); geometry.rotateX(-Math.PI / 2);
 const surface = new THREE.Mesh(geometry, material); surface.position.y = -1; basin.add(surface);
 const target = new THREE.WebGLRenderTarget(32, 32, {type: THREE.FloatType});
 const pixels = new Float32Array(32 * 32 * 4), u = material.userData.uniforms;
 function energy(normal) {
  for(let i = 0; i < geometry.attributes.normal.count; i++) geometry.attributes.normal.setXYZ(i, ...normal);
  geometry.attributes.normal.needsUpdate = true;
  renderer.setRenderTarget(target); renderer.render(basin, top); renderer.readRenderTargetPixels(target, 0, 0, 32, 32, pixels);
  const sum = [0, 0, 0];
  for(let i = 0; i < pixels.length; i += 4) for(let c = 0; c < 3; c++) sum[c] += pixels[i + c];
  return sum;
 }
 try { withRendererState(renderer, () => {
  renderer.toneMapping = THREE.NoToneMapping;
  const upward = energy([0, 1, 0]), facing = energy([1, 0, 0]), away = energy([-1, 0, 0]);
  const slide = u.uCauSlide.value.length(), cosWater = 1 / Math.hypot(1, slide);
  if(upward[0] <= 0 || Math.abs(facing[0] / upward[0] - slide) > .002) throw Error('Caustic incidence used the air ray');
  if(Math.max(...away) > 1e-6) throw Error('Back-facing surface received direct caustics');
  // Move to twice the depth while preserving the surface entry point. The
  // pattern, focus and incidence stay fixed; only Beer-Lambert changes.
  surface.position.set(-u.uCauSlide.value.x, -2, -u.uCauSlide.value.y);
  top.position.x = surface.position.x; top.position.z = surface.position.z; top.updateMatrixWorld(true);
  const deep = energy([0, 1, 0]);
  for(let c = 0; c < 3; c++) {
   const expected = Math.exp(-u.uCauAbs.value.getComponent(c) / cosWater);
   if(Math.abs(deep[c] / upward[c] - expected) > .003) throw Error('Caustics absorbed over vertical depth instead of beam length');
  }
  u.uCauSun.value.y *= -1;
  if(Math.max(...energy([0, 1, 0])) > 1e-6) throw Error('A set sun still produced caustics');
 }); } finally { target.dispose(); geometry.dispose(); material.dispose(); }
 return {scene: new THREE.Scene(), cameras: [{name: 'probe', position: [0, 3, 5], lookAt: [0, 0, 0]}]};
}
""", ("water.js", "caustics.js"), audit_module="src/scene.js")
    assert code == 0, output
