"""shader.js — the hub every library module builds on: assembly, patch chaining, the
cache key, roughness composition, one tick, and every construction path on the GPU."""
from __future__ import annotations

import math
import re

import pytest
from _probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js",)
_CHUNKS = ("logdepthbuf_pars_vertex", "logdepthbuf_vertex",
           "logdepthbuf_pars_fragment", "logdepthbuf_fragment")


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


def compile_fixture(fixture_src: str, libs: tuple[str, ...] = _LIBS, *, timeout_s: float = 180.0) -> tuple[int, str]:
    """GPU-compile what a fixture's build() makes, inside the lit, fogged scene
    below.  The static audit is scoped to the wrapper: its per-file uTime rule
    cannot see that shader.js binds uTime at assembly time."""
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


def test_raw_sources_are_repaired_not_trusted():
    out = _measure("""
import { makeShaderMaterial } from './lib/shader.js';
const m = makeShaderMaterial({
  vertexShader: 'void main() { gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: 'void main() { gl_FragColor = vec4(1.0); }',
});
console.log(JSON.stringify({ vs: m.vertexShader, fs: m.fragmentShader }));
""")
    for chunk in _CHUNKS:
        assert f"#include <{chunk}>" in out["vs"] + out["fs"], chunk
    fs = out["fs"]
    main_body = fs[fs.index("void main"):]
    assert main_body.index("logdepthbuf_fragment") < main_body.index("gl_FragColor")
    assert out["vs"].rindex("logdepthbuf_vertex") > out["vs"].rindex("gl_Position")


def test_utime_is_declared_wherever_it_is_read():
    out = _measure("""
import { makeShaderMaterial } from './lib/shader.js';
const m = makeShaderMaterial({ varyings: 'varying vec2 vUv;',
  vertexMain: '  vUv = uv;\\n  transformed.y += sin(uTime);',
  fragmentMain: '  gl_FragColor = vec4(vUv, sin(uTime), 1.0);' });
const q = makeShaderMaterial({ fragmentMain: '  gl_FragColor = vec4(1.0);' });
const count = (s) => (s.match(/uniform float uTime;/g) || []).length;
console.log(JSON.stringify({ vs: count(m.vertexShader), fs: count(m.fragmentShader),
  unusedVs: count(q.vertexShader), unusedFs: count(q.fragmentShader) }));
""")
    assert out["vs"] == 1 and out["fs"] == 1
    assert out["unusedVs"] == 0 and out["unusedFs"] == 0


def test_a_redeclared_varying_is_absorbed_not_a_compile_error():
    out = _measure("""
import { makeShaderMaterial } from './lib/shader.js';
const m = makeShaderMaterial({ varyings: 'varying vec2 vUv;', vertexMain: '  vUv = uv;',
  fragmentHead: 'varying vec2 vUv;\\nuniform float uScale;',
  fragmentMain: '  gl_FragColor = vec4(vUv * uScale, 0.0, 1.0);' });
const count = (s) => (s.match(/varying vec2 vUv;/g) || []).length;
console.log(JSON.stringify({ vs: count(m.vertexShader), fs: count(m.fragmentShader),
  keptUniform: m.fragmentShader.includes('uniform float uScale;') }));
""")
    assert out["vs"] == 1 and out["fs"] == 1 and out["keptUniform"]


def test_a_custom_shader_recedes_into_the_scene_fog():
    """Every scene here sets scene.fog; three delivers fog ONLY through the
    chunks and their uniforms.  Opting out (`fog: false`) leaves nothing
    behind and writes the marker OUR static audit reads (`3dcode: no-fog`)."""
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


def test_a_custom_shader_ends_the_way_three_ends_its_own():
    """No post chain on this renderer: tone map + sRGB happen in the fragment
    tail, so a custom shader without three's two closing chunks renders dark
    and untonemapped next to every built-in."""
    out = _measure("""
import { makeShaderMaterial } from './lib/shader.js';
const m = makeShaderMaterial({ fragmentMain: '  gl_FragColor = vec4(1.0);' });
const raw = makeShaderMaterial({ fragmentShader: 'void main() { gl_FragColor = vec4(1.0); }' });
const tail = (s) => s.slice(s.lastIndexOf('gl_FragColor'));
console.log(JSON.stringify({
  assembledTone: /tonemapping_fragment/.test(m.fragmentShader),
  assembledSpace: /colorspace_fragment/.test(m.fragmentShader),
  rawGetsThemToo: /colorspace_fragment/.test(raw.fragmentShader),
  afterFog: m.fragmentShader.indexOf('colorspace_fragment') > m.fragmentShader.indexOf('fog_fragment'),
  aloneOnTheirLines: tail(m.fragmentShader).split('\\n').filter((l) => l.includes('#include'))
      .every((l) => l.trim().startsWith('#include')) }));
""")
    assert out["assembledTone"] and out["assembledSpace"] and out["rawGetsThemToo"]
    assert out["afterFog"] and out["aloneOnTheirLines"]


def test_patched_builtins_do_not_share_one_program():
    out = _measure("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
const a = patchStandard(new THREE.MeshStandardMaterial(), { name: 'wet', fragmentBody: '  diffuseColor.rgb *= 0.5;' });
const b = patchStandard(new THREE.MeshStandardMaterial(), { name: 'dusty', fragmentBody: '  diffuseColor.rgb += 0.1;' });
const shaderA = { vertexShader: 'void main() { #include <begin_vertex> }',
                  fragmentShader: 'void main() { #include <color_fragment> }', uniforms: {} };
a.onBeforeCompile(shaderA);
console.log(JSON.stringify({ keyA: a.customProgramCacheKey(), keyB: b.customProgramCacheKey(),
  injected: shaderA.fragmentShader.includes('diffuseColor.rgb *= 0.5'), gotUniform: !!shaderA.uniforms.uTime }));
""")
    assert out["keyA"] != out["keyB"] and out["injected"] and out["gotUniform"]


def test_two_patches_on_one_material_both_survive_in_call_order():
    out = _measure("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
const m = new THREE.MeshStandardMaterial();
patchStandard(m, { name: 'wet', uniforms: { uWet: { value: 0.5 } }, fragmentBody: '  diffuseColor.rgb *= 0.5; // FIRST' });
patchStandard(m, { name: 'dusty', uniforms: { uDust: { value: 0.2 } }, vertexBody: '  transformed.y += 0.01;',
                   fragmentBody: '  diffuseColor.rgb += 0.1; // SECOND' });
patchStandard(m, { name: 'wet', uniforms: { uWet: { value: 0.5 } }, fragmentBody: '  diffuseColor.rgb *= 0.5; // FIRST' });
const shader = { vertexShader: 'void main() { #include <begin_vertex> }',
                 fragmentShader: 'void main() { #include <color_fragment> }', uniforms: {} };
m.onBeforeCompile(shader);
const fs = shader.fragmentShader;
console.log(JSON.stringify({ key: m.customProgramCacheKey(),
  order: fs.indexOf('// FIRST') < fs.indexOf('// SECOND'), onceEach: (fs.match(/\\/\\/ FIRST/g) || []).length,
  vertex: shader.vertexShader.includes('transformed.y += 0.01'),
  uWet: !!shader.uniforms.uWet, uDust: !!shader.uniforms.uDust,
  utilOnce: (fs.match(/float astraFbm2/g) || []).length }));
""")
    assert out["order"] and out["onceEach"] == 1 and out["vertex"]
    assert out["uWet"] and out["uDust"]
    assert out["key"] == "astra:wet+dusty", out["key"]
    assert out["utilOnce"] == 1


def test_patch_declarations_are_deduped_and_utime_declared():
    out = _measure("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
const m = new THREE.MeshStandardMaterial();
const head = 'varying float vK;\\nuniform float uTime;\\nuniform vec3 uShared[8];';
patchStandard(m, { name: 'a', vertexHead: head, fragmentHead: head,
                   vertexBody: '  vK = sin(uTime);', fragmentBody: '  diffuseColor.rgb += uShared[0] * vK;' });
patchStandard(m, { name: 'b', vertexHead: head, fragmentHead: head, vertexBody: '  transformed.x += cos(uTime);' });
const sway = patchStandard(new THREE.MeshStandardMaterial(), { name: 'sway',
  vertexBody: '  transformed.x += sin(uTime) * 0.1;', fragmentBody: '  diffuseColor.rgb *= 0.9 + 0.1 * sin(uTime);' });
const s1 = { vertexShader: 'void main() { #include <begin_vertex> }', fragmentShader: 'void main() { #include <color_fragment> }', uniforms: {} };
const s2 = { vertexShader: 'void main() { #include <begin_vertex> }', fragmentShader: 'void main() { #include <color_fragment> }', uniforms: {} };
m.onBeforeCompile(s1); sway.onBeforeCompile(s2);
const count = (s, re) => (s.match(re) || []).length;
console.log(JSON.stringify({
  vK: count(s1.vertexShader, /varying float vK;/g) + count(s1.fragmentShader, /varying float vK;/g),
  time: count(s1.vertexShader, /uniform float uTime;/g), arr: count(s1.fragmentShader, /uniform vec3 uShared\\[8\\];/g),
  both: s1.vertexShader.includes('sin(uTime)') && s1.vertexShader.includes('cos(uTime)'),
  swayVs: count(s2.vertexShader, /uniform float uTime;/g), swayFs: count(s2.fragmentShader, /uniform float uTime;/g) }));
""")
    assert out["vK"] == 2 and out["time"] == 1 and out["arr"] == 1 and out["both"]
    assert out["swayVs"] == 1 and out["swayFs"] == 1


def test_two_patches_defining_one_function_name_say_so():
    out = _measure("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
const compile = (type, a, b) => {
  const fn = (body) => `${type} astraShared(vec2 p) {\\n  return ${body};\\n}`;
  const m = new THREE.MeshStandardMaterial();
  patchStandard(m, { name: 'first', fragmentHead: fn(a), fragmentBody: '  diffuseColor.rgb *= 1.0;' });
  patchStandard(m, { name: 'second', fragmentHead: '  ' + fn(b) });
  const s = { uniforms: {}, vertexShader: 'void main() {}', fragmentShader: '#include <color_fragment>' };
  try { m.onBeforeCompile(s); return { ok: true, copies: (s.fragmentShader.match(/astraShared\\(vec2/g) || []).length }; }
  catch (e) { return { ok: false, msg: e.message }; }
};
const same = compile('float', 'p.x', 'p.x'), differ = compile('float', 'p.x', 'p.y');
console.log(JSON.stringify({ sameOk: same.ok, sameCopies: same.copies, differThrew: !differ.ok,
  namesBoth: !differ.ok && /first/.test(differ.msg) && /second/.test(differ.msg) && /astraShared/.test(differ.msg),
  ivec: !compile('ivec3', 'ivec3(0)', 'ivec3(1)').ok, bvec: !compile('bvec2', 'bvec2(true)', 'bvec2(false)').ok,
  matNxM: !compile('mat2x3', 'mat2x3(0.0)', 'mat2x3(1.0)').ok }));
""")
    assert out["sameOk"] and out["sameCopies"] == 1
    assert out["differThrew"] and out["namesBoth"]
    assert out["ivec"] and out["bvec"] and out["matNxM"]


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


def test_tick_drives_every_shader_from_one_call():
    out = _measure("""
import * as THREE from 'three';
import { makeShaderMaterial, patchStandard, tickShaders } from './lib/shader.js';
const scene = new THREE.Scene();
const a = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), makeShaderMaterial({ fragmentMain: '  gl_FragColor = vec4(uTime);' }));
const b = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), patchStandard(new THREE.MeshStandardMaterial(), { name: 'x' }));
const c = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
scene.add(a, b, c);
const n = tickShaders(scene, 4.25);
console.log(JSON.stringify({ n, a: a.material.uniforms.uTime.value, b: b.material.userData.uniforms.uTime.value }));
""")
    assert out["n"] == 2 and out["a"] == 4.25 and out["b"] == 4.25


def test_every_construction_path_compiles_on_our_gpu():
    """Assembled mains, raw sources, an additive veil, a billboard field, two
    chained patches with shadowLike, instanceVariation on a built-in, and the
    three later hooks (only the GPU can say roughnessFactor and metalnessFactor
    are in scope where they inject) — plain and instanced."""
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


def test_the_later_hooks_land_after_their_own_chunks_in_order():
    """Each opt-in hook lands after the chunk that puts its variable in scope
    (gl_FragColor while still LINEAR), and bodies join in CALL order."""
    out = _measure("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
const m = new THREE.MeshStandardMaterial({ color: 0x8b8478, roughness: 0.4 });
patchStandard(m, { name: 'rust',
  roughnessBody: '  roughnessFactor = mix(roughnessFactor, 0.95, 0.5);',
  metalnessBody: '  metalnessFactor *= 0.4;',
  outputBody: '  gl_FragColor.rgb += vec3(0.02, 0.01, 0.0);' });
patchStandard(m, { name: 'dust',
  roughnessBody: '  roughnessFactor = min(1.0, roughnessFactor + 0.2);',
  outputBody: '  gl_FragColor.rgb *= 0.98;' });
const shader = { vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
  fragmentShader: ['void main() {', '#include <color_fragment>',
    '#include <roughnessmap_fragment>', '#include <metalnessmap_fragment>',
    '#include <opaque_fragment>', '}'].join('\\n'), uniforms: {} };
m.onBeforeCompile(shader);
console.log(JSON.stringify({ fs: shader.fragmentShader }));
""")
    fs = out["fs"]
    for chunk, body in (
        ("roughnessmap_fragment", "roughnessFactor = mix(roughnessFactor, 0.95, 0.5);"),
        ("metalnessmap_fragment", "metalnessFactor *= 0.4;"),
        ("opaque_fragment", "gl_FragColor.rgb += vec3(0.02, 0.01, 0.0);"),
    ):
        inc, at = f"#include <{chunk}>", fs.index(body)
        assert fs.index(inc) < at, f"{body} landed before {inc}"
    # Call order, both hooks that two patches wrote into.
    assert fs.index("roughnessFactor = mix(") < fs.index("roughnessFactor = min("), fs
    assert fs.index("gl_FragColor.rgb +=") < fs.index("gl_FragColor.rgb *="), fs
    # A patch that asks for none of them injects none of them.
    assert fs.count("#include <opaque_fragment>") == 1, fs


def test_a_material_with_no_later_hooks_is_byte_for_byte_unchanged():
    """The hooks are additive: every patch shipped before them asks for none,
    so their chunks must come back untouched — no stray blank line, nothing
    for a later `.replace()` in this chain to land on twice."""
    out = _measure("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
const m = new THREE.MeshStandardMaterial({ color: 0x8b8478 });
patchStandard(m, { name: 'albedo',
  fragmentBody: '  diffuseColor.rgb *= 0.9;' });
const src = ['void main() {', '#include <color_fragment>',
  '#include <roughnessmap_fragment>', '#include <metalnessmap_fragment>',
  '#include <opaque_fragment>', '}'].join('\\n');
const shader = { vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
  fragmentShader: src, uniforms: {} };
m.onBeforeCompile(shader);
const tail = shader.fragmentShader.slice(
    shader.fragmentShader.indexOf('#include <roughnessmap_fragment>'));
console.log(JSON.stringify({ tail }));
""")
    assert out["tail"] == ("#include <roughnessmap_fragment>\n"
                          "#include <metalnessmap_fragment>\n"
                          "#include <opaque_fragment>\n}")


# The catalog's one wind object, the [x,z] pair the fire/smoke rows use, a number and a
# Vector3: each handed to every wind reader.  Before readWind, the object threw in
# firefield/smoke/clouds/cloudvolume (RangeError) and rain (TypeError), fire read no wind,
# snow read NaN, and windOf ignored the pair and blew the default way.
_WIND = """
import * as THREE from 'three';
import { readWind } from './lib/shader.js';
import { windOf } from './lib/grass.js';
import { makeFire } from './lib/fire.js';
import { makeFireField } from './lib/firefield.js';
import { makeSmoke } from './lib/smoke.js';
import { makeRain } from './lib/rain.js';
import { makeCloudVolume } from './lib/cloudvolume.js';
import { makeClouds } from './lib/clouds.js';
import { patchSnow } from './lib/accumulation.js';
import { makeRainVeil } from './lib/veils.js';
const uni = (g, name) => { let u; g.traverse((o) => { const v = o.material?.uniforms?.[name]; if (v && !u) u = v.value; }); return u; };
const xz = (v) => v.isVector3 ? [v.x, v.z] : typeof v === 'number' ? [v, 0] : [v.x, v.y];
const winds = { object: { dir: [1, 0.3], strength: 1.2, speed: 1 }, pair: [1, 0.3], number: 1.2,
                vec3: new THREE.Vector3(1, 5, 0.3) };
const out = {};
for (const [k, w] of Object.entries(winds)) {
  const r = readWind(w, [0, 0]);
  out[k] = {
    read: [r.dir.x, r.dir.y],
    windOf: xz(windOf(w).dir),
    fire: xz(uni(makeFire({ wind: w }), 'uWind')),
    firefield: 'ok' && makeFireField({ emitters: [{ position: [0, 0, 0], radius: 0.3, height: 1 }], wind: w, quality: 'low' }) && 'ok',
    smoke: xz(makeSmoke({ wind: w }).material.uniforms.uWind.value),
    rain: (({ x, z }) => [x, z])(makeRain({ wind: w, count: 10 }).material.uniforms.uVel.value),
    cloudVolume: xz(makeCloudVolume({ wind: w, quality: 'low' }).material.uniforms.uWind.value),
    clouds: uni(makeClouds({ wind: w, count: 2 }), 'uWind'),
    snow: xz(patchSnow(new THREE.MeshStandardMaterial(), { wind: w }).userData.uniforms.uSnowWind.value),
    veil: xz(uni(makeRainVeil({ direction: w }), 'uWind')),
  };
}
console.log(JSON.stringify(out));
"""

_WIND_LIBS = ("grass.js", "fire.js", "firefield.js", "smoke.js", "rain.js", "cloudvolume.js",
              "clouds.js", "accumulation.js", "veils.js")


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
    for reader in ("fire", "smoke", "rain", "cloudVolume", "snow", "veil"):
        v = w[reader]
        n = math.hypot(*v)
        assert n > 0 and all(math.isfinite(c) for c in v), (reader, v)
        if spelling != "number":   # a number blows along each effect's own default direction
            assert (v[0] / n, v[1] / n) == pytest.approx(tuple(ref), abs=1e-6), (reader, v)
    if spelling != "number":   # windOf's own fallback direction is (1, 0.45)
        assert w["windOf"] == pytest.approx(ref)
    assert w["firefield"] == "ok" and math.isfinite(w["clouds"]) and w["clouds"] > 0


def test_the_surface_gradient_bump_is_written_once():
    """astraBump/astraBumpSlope return n at a degenerate footprint (det 0); five modules
    kept their own copy after the helper landed, and stream.js's had no guard, so a
    grazing pixel shaded NaN.  Only tree.js's clamped variant and windows.js's
    cotangent frame (a different job) build a screen-space frame of their own."""
    frame = re.compile(r"cross\(\s*\w+\s*,\s*(normal|n)\s*\)")
    own = sorted(p.name for p in LIB_DIR.glob("*.js") if frame.search(p.read_text(encoding="utf-8")))
    assert own == ["shader.js", "tree.js", "windows.js"], own


def test_seed_lattice_keeps_the_formula_the_five_copies_used():
    """neon, urban, caustics, submerged and windows each carried this body; the one
    helper must give every recorded seed the same pattern it had."""
    out = measure("""
import { seedLattice } from './lib/shader.js';
const old = (seed, k, m) => (Math.abs(Math.round(seed === undefined ? 1 : seed)) % 9973 * k) % m;
const seeds = [undefined, 0, 1, 7, -7, 3.6, 9980, 123456];
console.log(JSON.stringify(seeds.flatMap((s) => [[16807, 9973], [48271, 9973], [16807, 257]]
  .map(([k, m]) => seedLattice(s, k, m) === old(s, k, m)))));
""", _LIBS)
    assert out and all(out), out
