"""terrain_shade.js — regressions: an off snow line rendered NaN black, and the splat
erased the triplanar it landed on."""
from __future__ import annotations

from tests.scene_runtime.lib._probe import SHADER_JS, measure

_LIBS = ("shader.js", "terrain_shade.js")


def _measure(script: str) -> dict:
    return measure(SHADER_JS + script, _LIBS)


def test_snow_is_off_by_default_and_never_settles_on_a_cliff():
    """`snowLine: Infinity` cannot reach a float uniform — smoothstep(inf, inf, y)
    is NaN, black over the whole terrain — so it is a finite sentinel whose band
    still resolves in float32; above the line the steepest faces stay bare."""
    out = _measure("""
import * as THREE from 'three';
import { patchSlopeSplat } from './lib/terrain_shade.js';
const sh = fake();
const off = patchSlopeSplat(new THREE.MeshStandardMaterial());
off.onBeforeCompile(sh);
const on = patchSlopeSplat(new THREE.MeshStandardMaterial(),
    { snowLine: 180, snow: 0xffffff });
const tight = patchSlopeSplat(new THREE.MeshStandardMaterial(),
    { snowLine: 9, snowBand: 1.8 });
const offTight = patchSlopeSplat(new THREE.MeshStandardMaterial(),
    { snowBand: 0.05 });
const u = (m, k) => m.userData.uniforms[k].value;
const y = (m) => u(m, 'uSplatSnowY'), b = (m) => u(m, 'uSplatSnowBand');
const resolves = (m) =>
    Math.fround(y(m) + b(m)) !== Math.fround(y(m) - b(m));
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  off: y(off), on: y(on), band: b(off),
  tightY: y(tight), tightBand: b(tight),
  offTightBand: b(offTight),
  bandResolves: resolves(off) && resolves(offTight) && resolves(tight),
}));
""")
    assert out["on"] == 180
    assert 1e5 <= out["off"] < 1e9, "a sentinel, not Infinity"
    assert out["band"] == 5, "the documented default half-width, in metres"
    assert out["tightY"] == 9 and out["tightBand"] == 1.8
    assert out["offTightBand"] >= 1.0, "a 0.05 m band cannot resolve at 1e6"
    assert out["bandResolves"], "the band must survive float32 everywhere"
    assert "smoothstep(uSplatHigh, uSplatLow, spUp)" in out["fs"], "snow needs a hold"


def test_the_splat_keeps_the_triplanar_it_lands_on():
    """Both patches assigned `diffuseColor.rgb`, so whichever ran second erased
    the other; over a triplanar the splat now carries its grain and value through."""
    out = measure("""
import * as THREE from 'three';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
const compile = (build) => {
  const m = new THREE.MeshStandardMaterial();
  build(m);
  const s = { uniforms: {},
              vertexShader: 'void main() { #include <begin_vertex> }',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return s.fragmentShader;
};
const both = compile((m) => { patchTriplanar(m, {}); patchSlopeSplat(m, {}); });
const triOnly = compile((m) => patchTriplanar(m, {}));
const splatOnly = compile((m) => patchSlopeSplat(m, {}));
// The composed body must READ the triplanar's own noise, not just
// declare it and throw it away.
const lastWrite = both.slice(both.lastIndexOf('diffuseColor.rgb ='));
console.log(JSON.stringify({
  composedUsesTriNoise: /tpN/.test(lastWrite.split(';')[0]),
  composedUsesTriValue: /tpV/.test(lastWrite.split(';')[0]),
  triAloneStillAssigns: /diffuseColor\\.rgb = tpC;/.test(triOnly),
  splatAloneUnchanged:
      /diffuseColor\\.rgb = spC \\* mix\\(0\\.86, 1\\.14, spVar\\);/
      .test(splatOnly),
  // The detection must survive the documented `name` option: it
  // used to key off the name, so one custom name undid the fix.
  survivesACustomName: (() => {
    const m = new THREE.MeshStandardMaterial();
    patchTriplanar(m, { name: 'myRock' });
    patchSlopeSplat(m, {});
    const s = { uniforms: {},
                vertexShader: 'void main() { #include <begin_vertex> }',
                fragmentShader: '#include <color_fragment>' };
    m.onBeforeCompile(s);
    const last = s.fragmentShader.slice(
        s.fragmentShader.lastIndexOf('diffuseColor.rgb ='));
    return /tpN/.test(last.split(';')[0]);
  })(),
  // Different structure means a different program: sharing the cache
  // key would serve one compiled shader to both shapes.
  distinctKey: (() => {
    const a = new THREE.MeshStandardMaterial();
    patchTriplanar(a, {}); patchSlopeSplat(a, {});
    const b = new THREE.MeshStandardMaterial();
    patchSlopeSplat(b, {});
    return a.customProgramCacheKey() !== b.customProgramCacheKey();
  })(),
}));
""", _LIBS)
    assert out["composedUsesTriNoise"], (
        "the splat threw the triplanar away again")
    assert out["composedUsesTriValue"], (
        "the splat flattened the triplanar's mass value and up-face wash")
    assert out["triAloneStillAssigns"] and out["splatAloneUnchanged"]
    assert out["survivesACustomName"], (
        "a custom name must not undo the composition")
    assert out["distinctKey"]
