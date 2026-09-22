"""terrain_shade.js — the surface chooses the shading, not a UV.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_terrain_shade_lib.py). Their renderer-contract assertions are
dropped; the scenarios are kept, and the value assertions follow this
port's regrade (see below), which was measured on our host rather than
argued in code:

* the patched rock rendered COLDER than the unpatched dirt beside it
  (R-B = -32 against +21) at saturation 0.06-0.09 with a hue spread of
  0.02 across a whole region — a grey putty card. One `astraHueBreak`
  is a TINT: its field is metres wide, so anything boulder-sized lands
  inside a single lobe. `astraTerrainGrain` stacks two decorrelated
  scales and pulls the mean warm; patched rock now reads R-B = +6 where
  the unpatched soil beside it in the same shadow reads -6.
* the snow cap was one flat value (hue spread 0.003, luminance sigma
  0.018 over an entire peak) at an albedo of 0.93-0.97, above the 0.8 an
  unlit dielectric may hold. The drift field now moves the COVERAGE, so
  scoured rock shows through.
* the +/-5 m snow transition was hardcoded, which hazes anything smaller
  than a mountain from base to tip; it is `snowBand` now, and the
  off-sentinel floors it so the smoothstep edges can still resolve in
  float32.
"""
from __future__ import annotations

import re

from tests.scene_runtime.lib._probe import LIB_DIR, SHADER_JS, measure

_LIBS = ("shader.js", "terrain_shade.js")
_LIB = LIB_DIR / "terrain_shade.js"


def _measure(script: str) -> dict:
    return measure(SHADER_JS + script, _LIBS)


def test_triplanar_projects_on_three_world_planes_and_reads_no_uv():
    """The whole point of the patch: a face at any angle takes the
    projection it faces most, so nothing stretches and no UV is needed.
    Weights that are not renormalised would darken or blow out every
    45-degree face."""
    out = _measure("""
import * as THREE from 'three';
import { patchTriplanar } from './lib/terrain_shade.js';
const sh = fake();
patchTriplanar(new THREE.MeshStandardMaterial()).onBeforeCompile(sh);
console.log(JSON.stringify({ vs: sh.vertexShader, fs: sh.fragmentShader }));
""")
    fs, vs = out["fs"], out["vs"]
    for plane in ("tpP.yz", "tpP.zx", "tpP.xy"):
        assert plane in fs, plane
    assert "w.x + w.y + w.z" in fs, "the three weights must sum to one"
    assert "abs(n)" in fs and "uTriSharp" in fs
    # No UV anywhere: the geometry this shades has none worth using.
    assert not re.search(r"\buv\b|\bvUv\b", fs), fs
    assert not re.search(r"\buv\b|\bvUv\b", vs), vs
    # The surface itself is the input, and it arrives in WORLD space.
    assert "modelMatrix" in vs and "vAstraWorld =" in vs
    assert "vAstraWorldN = normalize" in vs
    # A varying declared in one stage only is a link failure, and the
    # symptom is the patch simply not drawing.
    for decl in ("varying vec3 vAstraWorld;", "varying vec3 vAstraWorldN;"):
        assert decl in vs and decl in fs, decl


def test_triplanar_tunables_are_uniforms_with_the_documented_defaults():
    """Metres per repeat and the blend exponent are what an author
    retunes after seeing a frame; as uniforms they need no recompile —
    and, being uniforms rather than baked literals, two materials that
    share a program still keep their own values."""
    out = _measure("""
import * as THREE from 'three';
import { patchTriplanar } from './lib/terrain_shade.js';
const u = (m) => {
  const o = {};
  for (const k of Object.keys(m.userData.uniforms)) {
    const v = m.userData.uniforms[k].value;
    o[k] = (v && v.isColor) ? v.getHex() : v;
  }
  return o;
};
const d = patchTriplanar(new THREE.MeshStandardMaterial());
const t = patchTriplanar(new THREE.MeshStandardMaterial(), {
  scale: 7.5, sharpness: 12, noiseOctaves: 9,
  colorA: new THREE.Color(0x112233), colorB: 0x445566,
});
console.log(JSON.stringify({ d: u(d), t: u(t) }));
""")
    assert out["d"]["uTriScale"] == 2 and out["d"]["uTriSharp"] == 4
    assert out["d"]["uTriOct"] == 4
    assert out["t"]["uTriScale"] == 7.5 and out["t"]["uTriSharp"] == 12
    # astraFbm2 loops over at most six octaves; asking for more would
    # silently do nothing, so it is clamped where it can be seen.
    assert out["t"]["uTriOct"] == 6
    assert out["t"]["uTriA"] == 0x112233 and out["t"]["uTriB"] == 0x445566


def test_the_triplanar_default_pair_is_hue_separated_not_two_greys():
    """The noise mixes A into B, so if the pair shares a hue the mix is
    a VALUE ramp and the grain has no colour to travel through — which
    is how a whole cliff came out one flat tone. The shipped pair now
    spans a shaded grey-pink to a buff ochre."""
    out = _measure("""
import * as THREE from 'three';
import { patchTriplanar } from './lib/terrain_shade.js';
const m = patchTriplanar(new THREE.MeshStandardMaterial());
const hsl = (c) => { const o = {}; c.getHSL(o); return o; };
console.log(JSON.stringify({
  a: hsl(m.userData.uniforms.uTriA.value),
  b: hsl(m.userData.uniforms.uTriB.value),
}));
""")
    spread = abs(out["b"]["h"] - out["a"]["h"]) * 360.0
    assert spread > 20.0, (out["a"], out["b"])
    # Albedo, not light: an unlit dielectric holds 0.02..0.8.
    for end in ("a", "b"):
        assert 0.02 < out[end]["l"] < 0.8, out[end]


def test_slope_splat_bands_run_flats_to_scree_to_bare_rock():
    """Grass on the flats, scree between, bare rock on the steepest —
    and the two thresholds are ordered so the bands cannot cross. GLSL
    smoothstep with edge0 > edge1 is undefined, so an author who swaps
    slopeLow and slopeHigh would get an undefined snow mask, not a
    warning."""
    out = _measure("""
import * as THREE from 'three';
import { patchSlopeSplat } from './lib/terrain_shade.js';
const sh = fake();
const m = patchSlopeSplat(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const swapped = patchSlopeSplat(new THREE.MeshStandardMaterial(),
    { slopeLow: 0.3, slopeHigh: 0.9 });
const val = (x, k) => x.userData.uniforms[k].value;
const hsl = (c) => { const o = {}; c.getHSL(o); return o; };
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  low: val(m, 'uSplatLow'), high: val(m, 'uSplatHigh'),
  blend: val(m, 'uSplatBlend'),
  swappedLow: val(swapped, 'uSplatLow'),
  swappedHigh: val(swapped, 'uSplatHigh'),
  grass: hsl(val(m, 'uSplatGrass')),
  scree: hsl(val(m, 'uSplatScree')),
  rock: hsl(val(m, 'uSplatRock')),
  snow: hsl(val(m, 'uSplatSnow')),
}));
""")
    assert out["low"] == 0.75 and out["high"] == 0.45
    assert out["blend"] == 0.08
    assert out["swappedLow"] == 0.9 and out["swappedHigh"] == 0.3
    fs = out["fs"]
    # Grass is what the FLATTEST slope resolves to and rock what the
    # steepest does: the rock weight is the complement of the same ramp.
    assert "smoothstep(uSplatLow - spB, uSplatLow + spB, spUp)" in fs
    assert "1.0 - smoothstep(uSplatHigh - spB, uSplatHigh + spB, spUp)" in fs
    assert "mix(mix(uSplatScree, uSplatRock, spR), uSplatGrass, spG)" in fs
    # Slope is read from the surface normal, not from height.
    assert "spN.y" in fs and "normalize(vAstraWorldN)" in fs
    # The four zones must read as MATERIALS, not one grey at four
    # brightnesses: bare rock is the darkest AND the most saturated,
    # scree is pale and dusty, and every one of them is an albedo.
    rock, scree, snow = out["rock"], out["scree"], out["snow"]
    assert rock["l"] < scree["l"], "bare rock is the darker of the two"
    assert rock["s"] > scree["s"], "and the more saturated"
    assert snow["s"] < 0.10, "snow is neutral; the LIGHT makes it blue"
    for zone in (out["grass"], scree, rock, snow):
        assert 0.02 < zone["l"] <= 0.80, zone


def test_slope_splat_boundaries_wander_instead_of_ruling_a_line():
    """A threshold on normal.y alone draws a contour line no hillside
    has — the band edge reads as a decal laid over the terrain. The
    boundary is displaced by world-space noise, which is also what puts
    the snow line on the rock rather than on a plane. ONE wander is not
    enough: at 12 m it still draws a smooth rim across anything
    boulder-sized (the grass cap on a 1 m rock measured saturation 0.39
    against 0.06 for the rock under it), so a hand-scale term breaks
    that rim into speckle."""
    out = _measure("""
import * as THREE from 'three';
import { patchSlopeSplat } from './lib/terrain_shade.js';
const sh = fake();
patchSlopeSplat(new THREE.MeshStandardMaterial()).onBeforeCompile(sh);
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "astraFbmUnit(vAstraWorld.xz * 0.08, 3)" in fs
    assert "spN.y + spJit + spFine" in fs, "the slope threshold must wander"
    assert "spFine" in fs and "vAstraWorld.xz * 1.6" in fs, "at TWO scales"
    assert "vAstraWorld.y + spJit * uSplatSnowBand" in fs, "so must the snow"
    # The library noise is the shipped one, so a mesh displaced on the
    # CPU and this shader agree on what the same noise means.
    assert "float astraFbm2(" in fs and "float astraHash21(" in fs


def test_snow_is_off_by_default_and_never_settles_on_a_cliff():
    """`snowLine: Infinity` means no snow, but Infinity cannot reach a
    float uniform: smoothstep(inf, inf, y) is 0/0, and a NaN colour
    renders black over the whole terrain. It becomes a finite sentinel
    far above any scene whose transition band still resolves in float32
    — and now that the band is an OPTION, a small one against that
    sentinel would put the two edges on the same float and bring the
    NaN straight back, so the off case floors it at a metre.
    Above the line, snow is gated by the SAME slope knobs — the steepest
    faces stay bare rock, which is what makes a peak read as a peak."""
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
    fs = out["fs"]
    assert "smoothstep(uSplatHigh, uSplatLow, spUp)" in fs, "snow needs a hold"
    # The DRIFT moves the coverage, not just the tint: a smooth ramp
    # painted the whole cap one value.
    assert "spSnow * spHold * mix(0.45, 1.45, spDrift)" in fs
    assert "mix(spC, spSnowC, spCov)" in fs


def test_the_grain_stacks_two_scales_and_leans_warm():
    """One `astraHueBreak` is a tint: its 2-octave field is metres wide,
    so a boulder lands whole inside one lobe and comes out uniformly
    cold (measured R-B = -32 beside dirt at +21). Two decorrelated
    scales — patch-sized over hand-sized — plus an ochre term whose warm
    excursion is larger than its cool one is what makes a rock face run
    pink, grey and near-black across a metre. Shared by both patches
    under an include guard: a material wearing BOTH would otherwise
    redefine the function and die."""
    out = _measure("""
import * as THREE from 'three';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
const one = fake(), both = fake();
patchTriplanar(new THREE.MeshStandardMaterial()).onBeforeCompile(one);
const m = new THREE.MeshStandardMaterial();
patchTriplanar(m); patchSlopeSplat(m);
m.onBeforeCompile(both);
console.log(JSON.stringify({ fs: one.fragmentShader,
                             bothFs: both.fragmentShader }));
""")
    fs = out["fs"]
    assert "vec3 astraTerrainGrain(vec3 c, vec3 wp, float swing)" in fs
    calls = re.findall(r"astraHueBreak\(\w+, wp\.\w\w[^;]*?, ([\d.]+),", fs)
    assert len(calls) == 2, calls
    coarse, fine = (float(c) for c in calls)
    assert fine > coarse * 3, ("the two scales must be far apart", calls)
    # The warm end of the ochre mix moves further than the cool end, so
    # the MEAN of the field is warm — rock in daylight is never cooler
    # than the ground beside it.
    warm = re.search(
        r"mix\(vec3\(([\d.]+), [\d.]+, ([\d.]+)\),"
        r" vec3\(([\d.]+), [\d.]+, ([\d.]+)\), w\)", fs)
    assert warm, fs
    cool_r, cool_b, hot_r, hot_b = (float(g) for g in warm.groups())
    assert hot_r - 1.0 > 1.0 - cool_r, "the warm excursion must be larger"
    assert hot_r > hot_b and cool_r < cool_b, "one axis, red against blue"
    # Both patches ship the block, and a material wearing BOTH must end
    # up with ONE body — a second definition is "already has a body" and
    # a dead material. patchStandard's duplicate-helper detector drops
    # the repeat here; the `#ifndef` guard is the second line of defence
    # for the day a sibling library ships its own copy of the block.
    assert out["bothFs"].count("vec3 astraTerrainGrain(") == 1
    assert "#ifndef ASTRA_TERRAIN_GRAIN" in fs
    assert "#define ASTRA_TERRAIN_GRAIN" in fs


def test_the_two_patches_never_share_one_compiled_program():
    """three caches programs by material type and parameters, so two
    differently patched MeshStandardMaterials would compile to ONE
    program and the second patch would silently never run. Two materials
    wearing the SAME patch are the safe case, and only because every
    tunable is a uniform: their GLSL is byte-identical while their
    values stay their own."""
    out = _measure("""
import * as THREE from 'three';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
const tri = patchTriplanar(new THREE.MeshStandardMaterial());
const splat = patchSlopeSplat(new THREE.MeshStandardMaterial());
const a = patchTriplanar(new THREE.MeshStandardMaterial(), { scale: 3 });
const b = patchTriplanar(new THREE.MeshStandardMaterial(), { scale: 40 });
const shA = fake(), shB = fake();
a.onBeforeCompile(shA);
b.onBeforeCompile(shB);
console.log(JSON.stringify({
  triKey: tri.customProgramCacheKey(),
  splatKey: splat.customProgramCacheKey(),
  namedKey: patchTriplanar(new THREE.MeshStandardMaterial(),
      { name: 'scree' }).customProgramCacheKey(),
  sameGlsl: shA.fragmentShader === shB.fragmentShader,
  scaleA: shA.uniforms.uTriScale.value,
  scaleB: shB.uniforms.uTriScale.value,
  marked: !!tri.userData.astraShader,
}));
""")
    assert out["triKey"] != out["splatKey"], "each patch kind needs its own"
    assert out["namedKey"] not in (out["triKey"], out["splatKey"])
    assert out["sameGlsl"], "one patch kind must compile to one source"
    assert out["scaleA"] == 3 and out["scaleB"] == 40
    assert out["marked"], "tickShaders finds patches by this flag"


def test_both_patches_inject_at_the_hooks_patchstandard_documents():
    """`transformed` only exists after <begin_vertex> and `diffuseColor`
    only after <color_fragment>; injected anywhere else the patch is a
    compile error on a line number in three's assembled source. The
    instance transform is folded in at the vertex hook because
    `transformed` is still object-space there — without it every
    scattered rock projects from the world origin."""
    out = _measure("""
import * as THREE from 'three';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
const res = {};
for (const [k, patch] of [['tri', patchTriplanar],
                          ['splat', patchSlopeSplat]]) {
  const sh = fake();
  patch(new THREE.MeshStandardMaterial()).onBeforeCompile(sh);
  const vs = sh.vertexShader, fs = sh.fragmentShader;
  res[k] = {
    vertexAfterHook: vs.indexOf('#include <begin_vertex>') <
        vs.indexOf('vAstraWorld ='),
    fragAfterHook: fs.indexOf('#include <color_fragment>') <
        fs.indexOf('diffuseColor.rgb ='),
    instancing: vs.includes('#ifdef USE_INSTANCING') &&
        vs.includes('instanceMatrix * astraWp'),
    fragOnly: !vs.includes('astraFbmUnit(') && fs.includes('astraFbmUnit('),
    fragDefine: fs.includes('#define ASTRA_FRAG'),
    vertexDefine: vs.includes('#define ASTRA_FRAG'),
    gotUniforms: !!sh.uniforms.uTime,
  };
}
console.log(JSON.stringify(res));
""")
    for kind in ("tri", "splat"):
        got = out[kind]
        assert got["vertexAfterHook"], kind
        assert got["fragAfterHook"], kind
        assert got["instancing"], kind
        assert got["fragOnly"], kind
        # The util block ships in BOTH stages and its fwidth helper is
        # fragment-only, so ASTRA_FRAG must stay undefined in the vertex
        # stage or every program in the engine fails to compile.
        assert got["fragDefine"] and not got["vertexDefine"], kind
        assert got["gotUniforms"], kind


def test_the_shipped_module_is_deterministic():
    """Terrain has to come back identical on a re-render, so the whole
    library is seeded noise and constants — the same rule terrain.js
    holds to on the CPU side."""
    src = _LIB.read_text(encoding="utf-8")
    assert "Math.random" not in src
    assert "Date.now" not in src
    # Its GLSL ships into the VERTEX stage too, so it may not call a
    # fragment-only builtin: fwidth there fails every program at once.
    assert "fwidth" not in src
    assert "export function patchTriplanar" in src
    assert "export function patchSlopeSplat" in src


def test_the_splat_keeps_the_triplanar_it_lands_on():
    """Both patches used to assign `diffuseColor.rgb` outright, so on
    the terrain the prompts recommend — triplanar for the rock, splat
    for grass-to-scree-to-snow — whichever ran second erased the other
    completely. The two are a colour and a variation, not two colours:
    applied over a triplanar the splat carries its grain through, and
    (this port) its mass-scale value and up-face wash as well, or the
    splat flattens the cliff structure the triplanar just built."""
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
