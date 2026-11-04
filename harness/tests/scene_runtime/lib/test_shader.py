"""shader.js — the hub every library module builds on."""
from __future__ import annotations

import math

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js",)


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


def compile_fixture(fixture_src: str, libs: tuple[str, ...] = _LIBS, *, timeout_s: float = 180.0) -> tuple[int, str]:
    """GPU-compile what a fixture's build() makes, inside the lit, fogged scene below."""
    return compile_scene(_WRAPPER, libs, extra={"fixture.js": fixture_src},
                         audit_module="src/scene.js", timeout_s=timeout_s)


_WRAPPER = """
import * as THREE from 'three';
import { build } from './fixture.js';
import { tickShaders } from './lib/shader.js';
export const BOUNDS = { min: [-20, 0, -20], max: [20, 20, 20] };
export function heightAt() { return 0; }
export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.003);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 1.0));
  const sun = new THREE.DirectionalLight(0xffffff, 3); sun.position.set(10, 20, 10); sun.castShadow = true; scene.add(sun);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.MeshStandardMaterial({ color: 0x777766 }));
  ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);
  const built = await build();
  if (built && built.isObject3D) scene.add(built);
  return { scene, cameras: [{ name: 'a', position: [6, 4, 8], lookAt: [0, 1, 0], fov: 45 }],
           update(t) { tickShaders(scene, t); } };
}
"""


def test_a_custom_shader_recedes_into_the_scene_fog():
    """Every scene here sets scene.fog."""
    out = _measure("""
import { makeShaderMaterial } from './lib/shader.js';
const m = makeShaderMaterial({ fragmentMain: '  gl_FragColor = vec4(0.5, 0.6, 0.7, 1.0);' });
const off = makeShaderMaterial({ fog: false, fragmentMain: '  gl_FragColor = vec4(0.5, 0.6, 0.7, 1.0);' });
console.log(JSON.stringify({ vs: m.vertexShader, fs: m.fragmentShader, fogFlag: m.fog === true,
  hasFogColor: !!m.uniforms.fogColor, hasFogDensity: !!m.uniforms.fogDensity,
  offVs: off.vertexShader, offFs: off.fragmentShader, offFlag: off.fog === true,
  offUniform: !!off.uniforms.fogColor }));
""")
    for chunk in ("fog_pars_vertex", "fog_vertex"):
        assert f"#include <{chunk}>" in out["vs"], chunk
    for chunk in ("fog_pars_fragment", "fog_fragment"):
        assert f"#include <{chunk}>" in out["fs"], chunk
    assert out["fogFlag"] and out["hasFogColor"] and out["hasFogDensity"]
    assert "vec4 mvPosition = modelViewMatrix" in out["vs"]
    body = out["fs"][out["fs"].index("void main"):]
    assert body.index("gl_FragColor") < body.index("fog_fragment")
    assert "fog_pars_vertex" not in out["offVs"] and "fog_fragment" not in out["offFs"]
    assert not out["offFlag"] and not out["offUniform"]
    assert "3dcode: no-fog" in out["offFs"], "the opt-out marker our audit reads"


def test_roughness_composes_across_libraries_in_any_order_within_range():
    out = _measure("""
import * as THREE from 'three';
import { composeRoughness } from './lib/shader.js';
const a = new THREE.MeshStandardMaterial({ roughness: 0.8 });
composeRoughness(a, 'wet', 0.5); composeRoughness(a, 'wear', 0.9);
const b = new THREE.MeshStandardMaterial({ roughness: 0.8 });
composeRoughness(b, 'wear', 0.9); composeRoughness(b, 'wet', 0.5); composeRoughness(b, 'wet', 0.5);
const c = new THREE.MeshStandardMaterial({ roughness: 0.9 });
composeRoughness(c, 'rust', 1.3); const capped = composeRoughness(c, 'dust', 1.2);
console.log(JSON.stringify({ a: a.roughness, b: b.roughness, base: a.userData.astraRoughness.base, capped,
  floor: composeRoughness(new THREE.MeshStandardMaterial({ roughness: 0.5 }), 'x', 0.0),
  basic: Number.isNaN(composeRoughness(new THREE.MeshBasicMaterial(), 'x', 0.5)) }));
""")
    assert abs(out["a"] - 0.36) < 1e-6 and abs(out["b"] - out["a"]) < 1e-9
    assert out["base"] == 0.8 and out["capped"] == 1
    assert out["floor"] == 0.04 and out["basic"]


def test_every_construction_path_compiles_on_our_gpu():
    """Assembled mains, raw sources, an additive veil, a billboard field, two chained patches with shadowLike, instanceVariation on a built-in, and the th..."""
    code, out = compile_fixture("""
import * as THREE from 'three';
import { makeShaderMaterial, patchStandard, shadowLike, instanceVariation, instancedQuad, keepOutOfDepthPasses } from './lib/shader.js';
export function build() {
  const g = new THREE.Group();
  g.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), makeShaderMaterial({
    name: 'Assembled', uniforms: { uColor: { value: new THREE.Color(0xff8844) } },
    varyings: 'varying vec2 vUv;', fragmentHead: 'uniform vec3 uColor;',
    vertexMain: '  vUv = uv;\\n  transformed.y += 0.1 * sin(uTime + position.x);',
    fragmentMain: '  float n = astraFbm2(vUv * 8.0, 3);\\n  gl_FragColor = vec4(uColor * n * astraStroke(vUv.y * 10.0, 0.05), 1.0);',
  })));
  g.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), makeShaderMaterial({ name: 'Raw',
    vertexShader: 'varying vec2 vUv;\\nvoid main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: 'varying vec2 vUv;\\nvoid main() { gl_FragColor = vec4(vUv, 0.5, 1.0); }' })));
  g.add(keepOutOfDepthPasses(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), makeShaderMaterial({ name: 'Additive', additive: true,
    fragmentMain: '  gl_FragColor = vec4(1.0, 0.9, 0.7, 0.3 * (0.5 + 0.5 * sin(uTime)));' }))));
  g.add(new THREE.Mesh(instancedQuad(50, 0.3, 0.3, 20), makeShaderMaterial({ name: 'Quads', vertexHead: 'attribute vec3 aCorner;',
    vertexMain: '  transformed = aCorner + vec3(float(gl_InstanceID) * 0.1, 1.0, 0.0);',
    fragmentMain: '  gl_FragColor = vec4(0.9, 0.9, 0.5, 1.0);' })));
  const mat = new THREE.MeshStandardMaterial({ color: 0x668844 });
  // A map-less built-in has no vUv: carry a world-space varying instead.
  const head = 'varying float vSway;\\nvarying vec3 vAstraW;';
  const body = '  vSway = sin(uTime + position.x * 3.0);\\n  transformed.x += 0.05 * vSway;\\n  vAstraW = (modelMatrix * vec4(transformed, 1.0)).xyz;';
  patchStandard(mat, { name: 'sway', vertexHead: head, vertexBody: body, fragmentHead: head,
    fragmentBody: '  diffuseColor.rgb = astraHueBreak(diffuseColor.rgb, vAstraW.xz, 6.0, 0.6) * (0.9 + 0.1 * vSway);' });
  patchStandard(mat, { name: 'wet', uniforms: { uWet: { value: 0.4 } }, fragmentHead: 'uniform float uWet;',
    fragmentBody: '  diffuseColor.rgb *= 1.0 - 0.3 * uWet;' });
  const leaf = new THREE.Mesh(new THREE.PlaneGeometry(1, 1, 8, 8), mat);
  shadowLike(leaf, 'sway-depth', head, body); g.add(leaf);
  const inst = new THREE.InstancedMesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), new THREE.MeshStandardMaterial({ color: 0x884422, name: 'boxes' }), 30);
  const m = new THREE.Matrix4();
  for (let i = 0; i < 30; i++) inst.setMatrixAt(i, m.makeTranslation(i * 0.4 - 6, 0.5, 2));
  instanceVariation(inst, { seed: 3 }); g.add(inst);
  // the three later hooks: roughnessFactor / metalnessFactor in scope, gl_FragColor writable
  const crustMat = new THREE.MeshStandardMaterial({
    color: 0x9a6b3f, roughness: 0.35, metalness: 0.8 });
  patchStandard(crustMat, {
    name: 'crust',
    uniforms: { uCrust: { value: 0.6 } },
    fragmentHead: 'uniform float uCrust;\\nvarying vec3 vCrustW;',
    vertexHead: 'varying vec3 vCrustW;',
    vertexBody: '  vCrustW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
    fragmentBody:
        '  float crust = uCrust * astraFbm2(vCrustW.xz * 1.5, 3);\\n'
        + '  diffuseColor.rgb = mix(diffuseColor.rgb,'
        + ' vec3(0.42, 0.20, 0.09), crust);',
    // the point of the hook: kill the specular ONLY where the crust is
    roughnessBody: '  roughnessFactor = mix(roughnessFactor, 0.98, crust);',
    metalnessBody: '  metalnessFactor *= 1.0 - crust;',
    outputBody: '  gl_FragColor.rgb += vec3(0.03, 0.02, 0.01) * crust;',
  });
  const tank = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 3, 24), crustMat);
  tank.position.y = 1.5; tank.castShadow = true; g.add(tank);
  // the same material on an InstancedMesh: a second permutation, and the
  // one place USE_INSTANCING inside the world-space helper is compiled.
  const crustInst = new THREE.InstancedMesh(
      new THREE.BoxGeometry(0.4, 0.4, 0.4), crustMat, 8);
  const m4b = new THREE.Matrix4();
  for (let i = 0; i < 8; i++) {
    crustInst.setMatrixAt(i, m4b.makeTranslation(i * 0.7 - 2.5, 0.2, 2.5));
  }
  crustInst.instanceMatrix.needsUpdate = true;
  g.add(crustInst);
  return g;
}
""")
    assert code == 0, out
    assert "ERROR" not in out, out


# The catalog's one wind object, an [x,z] pair, a number and a Vector3: each handed to
# both wind readers.  Before readWind, windOf ignored the pair and blew the default way.
_WIND = """
import * as THREE from 'three';
import { readWind } from './lib/shader.js';
import { windOf } from './lib/grass.js';
const xz = (v) => v.isVector3 ? [v.x, v.z] : typeof v === 'number' ? [v, 0] : [v.x, v.y];
const winds = { object: { dir: [1, 0.3], strength: 1.2, speed: 1 }, pair: [1, 0.3], number: 1.2,
                vec3: new THREE.Vector3(1, 5, 0.3) };
const out = {};
for (const [k, w] of Object.entries(winds)) {
  const r = readWind(w, [0, 0]);
  out[k] = { read: [r.dir.x, r.dir.y], windOf: xz(windOf(w).dir) };
}
console.log(JSON.stringify(out));
"""

_WIND_LIBS = ("grass.js",)


@pytest.fixture(scope="module")
def wind() -> dict:
    return measure(_WIND, _WIND_LIBS)


@pytest.mark.parametrize("spelling", ["object", "pair", "number", "vec3"])
def test_one_wind_is_read_the_same_way_by_every_effect(wind, spelling):
    w = wind[spelling]
    ref = w["read"]
    # the number has no direction of its own: it blows along the fallback's
    expect = [1, 0] if spelling == "number" else [1 / math.hypot(1, 0.3), 0.3 / math.hypot(1, 0.3)]
    assert ref == pytest.approx(expect)
    v = w["windOf"]
    n = math.hypot(*v)
    assert n > 0 and all(math.isfinite(c) for c in v), v
    if spelling != "number":   # windOf's own fallback direction is (1, 0.45)
        assert v == pytest.approx(ref)
