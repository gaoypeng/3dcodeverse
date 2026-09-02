"""Caustics are LIGHT, and every way of getting them wrong is silent.

``caustics.js`` is one ``patchStandard`` that throws the water's moving
net onto what is under it.  Five things can go wrong with no error at
all: a uniform a neighbour already declares is absorbed by dedupeUniforms
and the patch reads someone else's number; a local declared twice in the
one injected main takes the whole material down; a pattern built from one
fbm compiles perfectly and reads as dirt; lifting albedo instead of the
emissive term caps the net at the light the surface already gets, so it
vanishes exactly where a pool is interesting; and a net whose peaks run
past what the tone map holds comes out of ACES as flat white — the
brightest node and one a third as bright arrive at the same pixel value.
These pin the source that avoids that, then compile the real chain on the
real renderer.

Ported 2026-09-01 from the scene_multifile_graphics reference test.  The
assertions about THEIR renderer contract are gone; the colour half (a
per-channel extinction, the dispersion split, the local source) is ours.
"""

from __future__ import annotations

import re

from tests.scene_runtime.lib._probe import LIB_DIR, compile_scene, measure  # noqa: F401

_LIBS = ("shader.js", "caustics.js", "waterside.js", "terrain_shade.js",
         "surface_wear.js")
_LIB = LIB_DIR / "caustics.js"

# The two hooks patchStandard injects into, as a material three would.
# Nothing else is in the source, so what comes back is the patch itself.
_FAKE_SHADER = """
const fake = () => ({
  vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
  fragmentShader: 'void main() {\\n#include <color_fragment>\\n}',
  uniforms: {},
});
const mains = (sh) => sh.fragmentShader.slice(
    sh.fragmentShader.indexOf('void main')) + '\\n' +
    sh.vertexShader.slice(sh.vertexShader.indexOf('void main'));
const locals = (src) => (src.match(
    /^\\s+(?:float|vec2|vec3|vec4|mat3|mat4)\\s+(\\w+)/gm) || [])
    .map((h) => h.trim().split(/\\s+/)[1]);
"""


def _measure(script: str) -> dict:
    return measure(_FAKE_SHADER + script, _LIBS)


def test_the_net_is_ridged_interference_not_one_noise_call():
    """The whole difference between caustics and dirt.  A caustic is a
    net of FILAMENTS — the creases of a wave field, not its blobs — so
    the pattern folds noise about its own mid-line and raises the crease
    to a power, which is what makes a line instead of a smear.  One such
    field is a smear of worms; the effect is the INTERFERENCE of two,
    summed for the net and multiplied so the crossings flare into the
    bright nodes.  A single fbm call would compile and read as mud.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
patchCaustics(new THREE.MeshStandardMaterial()).onBeforeCompile(sh);
const fs = sh.fragmentShader;
console.log(JSON.stringify({
  fs,
  webs: (fs.match(/astraCauWeb\\(/g) || []).length,
  fbm: (fs.match(/astraFbm2\\(/g) || []).length,
}));
""")
    fs = out["fs"]
    # The crease: |n - mid| folded, then powered into a filament.  Two
    # octaves (which top out at 0.75, hence 2.667) kink the contour.
    assert "float d = abs(astraFbm2(p, 2) * 2.667 - 1.0);" in fs
    assert "return pow(1.0 - d, 16.0);" in fs
    # Two fields, each in its OWN frame: a train's filaments run ACROSS
    # the way it travels, so the frame is stretched that way.
    assert out["webs"] == 3, "one definition, two crossing trains"
    assert "vec2 cauU = vec2(0.86, 0.51);" in fs
    assert "vec2 cauV = vec2(-0.51, 0.86);" in fs
    assert "dot(cauP, cauU) + cauT," in fs
    assert "dot(cauP, cauV) * 0.80));" in fs
    assert "dot(cauP, cauV) * 1.43 - cauT * 0.83," in fs
    # Sum = the net, product = the nodes.  They are kept as separate
    # locals because the colour half needs to tell them apart, but the
    # interference term itself is what makes this a caustic: without the
    # product the two fields never meet.
    assert "float cauFil = (cauA + cauB) * 0.5;" in fs
    assert "float cauNode = cauA * cauB;" in fs
    assert "float cauNet = cauFil + 4.0 * cauNode;" in fs
    # Not one noise read pretending to be a pattern.
    assert out["fbm"] >= 2, out["fbm"]
    assert "astraFbmUnit" not in fs, "that is terrain_shade's helper"


def test_nothing_above_the_waterline_is_touched():
    """Above ``level`` there is no water, so there is nothing to throw a
    net — a caustic that climbs the dry bank is the tell that it is a
    texture.  The depth is clamped at zero at the line and the whole
    contribution is gated by a smoothstep FROM zero, so at and above the
    line the added light is exactly 0.0, not merely small.  Below it the
    net fades in over the first fraction of a depth (light needs a run
    to converge) and out again with the water column.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
const m = patchCaustics(new THREE.MeshStandardMaterial(),
    { level: 2.5, depthFade: 4 });
m.onBeforeCompile(sh);
const fs = sh.fragmentShader;
const body = fs.slice(fs.indexOf('#include <color_fragment>'));
console.log(JSON.stringify({
  fs, body,
  level: m.userData.uniforms.uCauLevel.value,
  fade: m.userData.uniforms.uCauFade.value,
  zeroFade: patchCaustics(new THREE.MeshStandardMaterial(),
      { depthFade: 0 }).userData.uniforms.uCauFade.value,
  zeroAbs: patchCaustics(new THREE.MeshStandardMaterial(),
      { depthFade: 0 }).userData.uniforms.uCauAbs.value,
}));
""")
    fs, body = out["fs"], out["body"]
    assert out["level"] == 2.5 and out["fade"] == 4
    # Depth below the line, clamped: above it every term that follows
    # sees zero.
    assert "float cauD = max(uCauLevel - vAstraWorld.y, 0.0);" in fs
    # smoothstep(0, x, 0) is exactly 0, so the gate is not "nearly none
    # above the line", it is none.
    assert "float cauK = smoothstep(0.0, uCauFade * 0.3, cauD);" in fs
    # A zero fade would divide by zero and paint NaN over the surface —
    # in the gate AND in the extinction, which is now the other user of
    # that number.
    assert out["zeroFade"] > 0
    assert all(0 < v < 1e6 for v in out["zeroAbs"].values()), out["zeroAbs"]
    # And the gate reaches the ONE write: nothing else in the body may
    # touch an output, or the patch could leak past the waterline by
    # another route.
    assert "* (cauNet * cauK * uCauAmt);" in body
    writes = re.findall(
        r"^\s*(diffuseColor|totalEmissiveRadiance|gl_FragColor|"
        r"roughnessFactor)[.\w]*\s*[-+*/]?=", body, re.M)
    assert writes == ["totalEmissiveRadiance"], writes


def test_the_net_belongs_to_the_water_above_not_to_the_floor():
    """What separates thrown light from a painted pattern: the fragment
    is shaded from where its sunbeam ENTERED the water — its own world
    XZ slid toward the sun by its own depth — so a step riser and the
    floor beside it take one continuous net rather than a pattern each.
    The slide is REFRACTED on the CPU (water bends the ray toward the
    vertical), which is why it is a uniform and not ``sunDir.xz /
    sunDir.y``: that air tangent overshoots by a third and shears the
    net off the wall.  A sun straight overhead throws no slide at all.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
const low = new THREE.Vector3(0.8, 0.35, 0.0);
const m = patchCaustics(new THREE.MeshStandardMaterial(), { sunDir: low });
m.onBeforeCompile(sh);
const u = (mat, k) => mat.userData.uniforms[k].value;
const up = patchCaustics(new THREE.MeshStandardMaterial(),
    { sunDir: new THREE.Vector3(0, 1, 0) });
const under = patchCaustics(new THREE.MeshStandardMaterial(),
    { sunDir: new THREE.Vector3(0.3, -0.9, 0.2) });
const n = low.clone().normalize();
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  slide: u(m, 'uCauSlide'), sun: u(m, 'uCauSun'),
  airTan: Math.hypot(n.x, n.z) / n.y,
  upSlide: u(up, 'uCauSlide'), underSlide: u(under, 'uCauSlide'),
}));
""")
    fs = out["fs"]
    assert "vec2 cauP = (vAstraWorld.xz + uCauSlide * cauD) / uCauScale" in fs
    assert "uniform vec2 uCauSlide;" in fs, "two floats, not a sampler"
    slide = out["slide"]
    # Along the sun's azimuth (+x here), and SHORTER than the air
    # tangent — that shortening is the refraction.
    assert slide["y"] == 0, slide
    assert 0 < slide["x"] < out["airTan"], (slide, out["airTan"])
    assert slide["x"] < out["airTan"] * 0.8, "n = 1.333 is a third"
    # Straight overhead: no slide, and no divide by a zero azimuth.
    assert out["upSlide"] == {"x": 0, "y": 0}
    # A sun below the horizon must still give finite numbers, not NaN.
    assert all(abs(v) < 10 for v in out["underSlide"].values())
    # The facing term: light that arrives edge-on spreads over more
    # floor, so it must not land at full strength on a vertical face.
    assert "max(dot(normalize(vAstraWorldN), uCauSun)," in fs
    assert out["sun"]["y"] > 0.3, "sunDir points TOWARD the sun"


def test_caustics_land_on_the_light_not_on_the_albedo():
    """``fragmentBody`` edits ``diffuseColor``, which is ALBEDO, and
    albedo is the wrong place for this: it would cap the net at whatever
    light the surface already gets, so the shaded corner, the soffit and
    the deep step — the places a caustic is the only light there is —
    would get none.  So the lift goes on ``totalEmissiveRadiance``,
    tinted by the albedo because what the eye sees is that light
    REFLECTED.  The cost of that choice is a material that has no such
    term at all, and it is a compile error rather than a dim result, so
    it is said out loud.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
const said = [];
console.warn = (msg) => said.push(String(msg));
const m = patchCaustics(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const quiet = said.length;
patchCaustics(new THREE.MeshBasicMaterial({ name: 'Tile' }));
patchCaustics(new THREE.ShaderMaterial({ name: 'Ocean' }));
patchCaustics(new THREE.MeshPhysicalMaterial());
console.log(JSON.stringify({
  fs: sh.fragmentShader, said, quiet, after: said.length,
  rough: m.roughness,
  color: m.userData.uniforms.uCauColor.value.getHex(),
  amt: m.userData.uniforms.uCauAmt.value,
}));
""")
    fs = out["fs"]
    assert "totalEmissiveRadiance += cauTint * diffuseColor.rgb" in fs
    # Albedo is READ and never written: the surface is not repainted.
    assert "diffuseColor.rgb =" not in fs and "diffuseColor *=" not in fs
    assert "diffuseColor.rgb *=" not in fs
    # roughnessFactor does not exist yet at this hook, and gloss is not
    # this patch's business anyway.
    assert "roughnessFactor" not in fs
    assert out["rough"] == 1.0, "a lit floor is not polished by caustics"
    assert out["amt"] == 4
    # Daylight above the surface, before the water takes the red out of
    # it: near white, and warm rather than the cold white it was, since
    # the cool end is now the DEPTH's job and not the lamp's.
    assert out["color"] == 0xfff2e2
    # A lit material is quiet; the two that cannot carry the term are
    # named, once each.
    assert out["quiet"] == 0
    assert out["after"] == 2, out["said"]
    assert "Tile" in out["said"][0] and "Ocean" in out["said"][1]
    assert "totalEmissiveRadiance" in out["said"][0]


def test_the_water_takes_the_red_out_first_without_dimming_the_net():
    """The colour of thrown light is the colour of what it came
    through.  A single scalar fade leaves a 3 m floor lit the same
    colour as a shin-deep one, which is the one thing no photograph of
    a pool shows: red is gone in a couple of metres where blue crosses
    tens, so the ramp runs warm at the step and green-blue at the drain.

    The extinction is per channel AND normalised to Rec.709 luminance,
    which is what keeps it a hue split rather than a second dimmer:
    ``depthFade`` still means the metres it always meant.  Weighting by
    thirds instead would darken the net by about a tenth at every depth,
    because green — the middle channel — carries 71% of the luminance.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
const m = patchCaustics(new THREE.MeshStandardMaterial(), { depthFade: 2.5 });
m.onBeforeCompile(sh);
const abs = m.userData.uniforms.uCauAbs.value;
const at = (d) => [Math.exp(-d * abs.x), Math.exp(-d * abs.y),
                   Math.exp(-d * abs.z)];
const lum = (c) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  abs: [abs.x, abs.y, abs.z],
  rate: 0.2126 * abs.x + 0.7152 * abs.y + 0.0722 * abs.z,
  shallow: at(0.4), deep: at(2.5), lumDeep: lum(at(2.5)),
  wide: (() => { const a = patchCaustics(new THREE.MeshStandardMaterial(),
      { depthFade: 5 }).userData.uniforms.uCauAbs.value; return a.x; })(),
}));
""")
    fs = out["fs"]
    assert "vec3 cauTint = uCauColor * exp(-cauD * uCauAbs);" in fs
    assert "uniform vec3 uCauAbs;" in fs
    r, g, b = out["abs"]
    # Red is drunk first, blue last — and by a real margin, not a hint.
    assert r > g > b > 0, out["abs"]
    assert r / b > 2.0, (r, b)
    # Luminance-neutral: the mid-grey rate is exactly 1 / depthFade, so
    # a caller who asked for 2.5 m still gets 2.5 m.
    assert abs(out["rate"] - 1 / 2.5) < 1e-6, out["rate"]
    # A deeper fade is a slower rate, per channel, in proportion.
    assert abs(out["wide"] - r * 2.5 / 5) < 1e-6
    # The ramp itself: barely tinted at the step, clearly cold at depth.
    sr, sg, sb = out["shallow"]
    dr, dg, db = out["deep"]
    assert sb / sr < 1.35, (sr, sb)
    assert db / dr > 2.2, (dr, db)
    # And the net at one fade of depth still holds the ~exp(-1) it held
    # when the fade was one number (Jensen: never darker, never much
    # brighter).
    assert 0.36 < out["lumDeep"] < 0.46, out["lumDeep"]
    # Dispersion within one net: a lone filament is a fold seen edge-on
    # and keeps the cool end, a crossing is achromatic and warm-white.
    assert "float cauNf = cauNode / (cauNode + cauFil * 0.5 + 1e-4);" in fs
    assert "mix(vec3(0.84, 0.98, 1.10), vec3(1.08, 1.00, 0.92)," in fs
    # Broken colour over metres: one flat green over a whole pool is the
    # tell of a painted pattern.
    assert "cauTint = astraHueBreak(cauTint, cauP, 0.25, 0.22);" in fs


def test_the_peaks_stay_inside_the_tone_map_and_the_far_field_stays_lit():
    """Two folds plus their product run to 5 and the swell carries that
    to 8; at the shipped strength that is 30-odd units of radiance into
    an ACES curve that holds about 4, so the brightest node and one a
    third as bright came out as the same white and the floor's own
    material went with them.  The field is rolled off before it is
    scaled — peaks toward 5.5, mid-tones nearly untouched.

    And the aliasing guard fades the net to the field's MEAN rather than
    to black: fading to zero draws a line across the floor at the
    distance where the water stops being lit.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
patchCaustics(new THREE.MeshStandardMaterial()).onBeforeCompile(sh);
const fs = sh.fragmentShader;
const roll = (x) => x / (1.0 + 0.18 * x);
console.log(JSON.stringify({
  fs,
  peak: roll(5.0 * 1.65), mid: roll(0.5), asymptote: roll(1e6),
}));
""")
    fs = out["fs"]
    assert "cauNet = cauNet / (1.0 + 0.18 * cauNet);" in fs
    # A node lands in the range a bloom threshold can catch and a tone
    # map can still separate; four times the shipped strength is not.
    assert 3.0 < out["peak"] * 4 * 0.25 < 4.0, out["peak"]
    assert out["asymptote"] < 5.6
    # The mid-tones are barely touched: this is a shoulder, not a
    # brightness cut.
    assert out["mid"] > 0.45, out["mid"]
    # Aliasing: to the mean, keeping the swell, never to black.
    assert "cauNet = mix(cauNet, 0.30 * cauSwell," in fs
    assert "smoothstep(0.25, 0.9, cauAa));" in fs
    assert "cauNet *= 1.0 - smoothstep" not in fs, "that ended the water"
    # A ramp of depth over hundreds of pixels is where 8-bit output
    # bands, so the gate carries its own dither.
    assert "cauK *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.05;" in fs


def test_a_lamp_over_the_water_lights_a_pool_and_the_sun_lights_a_bay():
    """A night pool is a POOL: one bulb whose net reaches the far bank of
    a reservoir is the same tell as a net that climbs a dry wall.  The
    sun is the opposite — parallel light, no falloff — so it stays the
    default, and its packed reach of 0 makes the falloff term exactly
    1.0 with no branch and no second shader variant.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
const sun = patchCaustics(new THREE.MeshStandardMaterial());
sun.onBeforeCompile(sh);
const u = (m) => m.userData.uniforms.uCauSrc.value;
const lamp = patchCaustics(new THREE.MeshStandardMaterial(),
    { source: new THREE.Vector3(4, 5, -2), reach: 6 });
const noReach = patchCaustics(new THREE.MeshStandardMaterial(),
    { source: new THREE.Vector3(0, 3, 0) });
const zero = patchCaustics(new THREE.MeshStandardMaterial(),
    { source: new THREE.Vector3(0, 3, 0), reach: 0 });
const fall = (r, w) => 1 / (1 + (r * w) * (r * w));
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  sun: u(sun), lamp: u(lamp), noReach: u(noReach).w, zeroW: u(zero).w,
  atReach: fall(6, u(lamp).w), atSun: fall(500, u(sun).w),
  key: sun.customProgramCacheKey() === lamp.customProgramCacheKey(),
}));
""")
    fs = out["fs"]
    assert "uniform vec4 uCauSrc;" in fs
    assert "float cauR = length(vAstraWorld.xz - uCauSrc.xz) * uCauSrc.w;" in fs
    assert "cauK /= 1.0 + cauR * cauR;" in fs
    # The sun: no source, no falloff — and 500 m away is still full
    # strength, which is what "parallel" means.
    assert out["sun"] == {"x": 0, "y": 0, "z": 0, "w": 0}
    assert out["atSun"] == 1.0
    # The lamp: its position, and 1 / reach packed into w.
    assert out["lamp"] == {"x": 4, "y": 5, "z": -2, "w": 1 / 6}
    assert abs(out["atReach"] - 0.5) < 1e-9, "reach is the half-way mark"
    assert out["noReach"] == 1 / 8, "a default reach, not an infinity"
    assert 0 < out["zeroW"] < 1e6, "reach 0 must not pack an infinity"
    # One shader for both: the source is a uniform, so a lamp-lit pool
    # and a sunlit one share a program.
    assert out["key"], "the cache key must not fork on a uniform"


def test_the_net_moves_with_the_water_instead_of_crawling():
    """A scrolled pattern reads as a texture sliding under the water,
    which is the failure this shape exists to avoid.  Three things stop
    it: the two trains counter-drift at incommensurate rates, so their
    crossings travel at neither speed; the sample point is warped by two
    noise fields of its own, coarse and fine, so the filaments writhe
    rather than translate; and the whole thing rides ``uTime``, which
    must reach the fragment stage DECLARED — patchStandard adds that
    only because its declaration pass runs after the bodies go in.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const sh = fake();
const m = patchCaustics(new THREE.MeshStandardMaterial(), { speed: 0.9 });
m.onBeforeCompile(sh);
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  speed: m.userData.uniforms.uCauSpeed.value,
  timeDecls: (sh.fragmentShader.match(/uniform float uTime;/g) || []).length,
  hasUniform: !!sh.uniforms.uCauSlide,
  tickable: !!m.userData.uniforms.uTime,
}));
""")
    fs = out["fs"]
    assert out["speed"] == 0.9
    assert "float cauT = uTime * uCauSpeed;" in fs
    assert out["timeDecls"] == 1, "read undeclared, uTime is a compile error"
    assert out["tickable"], "tickShaders finds uTime on userData.uniforms"
    assert out["hasUniform"], "the patch's uniforms reach the program"
    # Counter-drift: one train down +cauT, the other down -cauT, and at
    # a rate that is not a multiple of the first's.
    assert "dot(cauP, cauU) + cauT," in fs
    assert "- cauT * 0.83," in fs
    # Two warps, coarse and fine, both moving: the net writhes.
    assert "astraNoise2(cauP * 0.35 + cauT * 0.07)" in fs
    assert "astraNoise2(cauP * 2.2 + cauT * 0.30)" in fs
    assert "cauP += (cauW - 0.5) * 0.40;" in fs
    # And a slow broad cell, so the whole field breathes rather than
    # lighting the floor evenly.
    assert "float cauSwell = 0.35 + 1.30 * cauC;" in fs
    assert "cauNet *= cauSwell;" in fs


def test_its_uniforms_and_locals_collide_with_no_sibling_library():
    """The failure with no symptom.  dedupeUniforms drops a repeated
    ``uniform`` declaration and keeps the FIRST, so a caustic patch that
    named a uniform waterside already declares would silently read the
    waterline's number for the rest of the run.  A repeated local is the
    opposite and just as fatal: two patches share ONE injected main, and
    a redeclared local is a GLSL redefinition that takes the whole
    material down.  This library lands on exactly the materials those
    siblings are already on, so both sets must be disjoint from all of
    them — with uTime the one deliberate share.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
import { patchShoreWet, patchShoreFoam, patchShallowWater }
    from './lib/waterside.js';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
const mineSh = fake(), sibSh = fake();
const mine = patchCaustics(new THREE.MeshStandardMaterial());
mine.onBeforeCompile(mineSh);
const sib = new THREE.MeshStandardMaterial();
patchShoreWet(sib); patchShoreFoam(sib); patchShallowWater(sib);
patchTriplanar(sib); patchSlopeSplat(sib);
patchMicroBreakup(sib); patchEdgeWear(sib);
sib.onBeforeCompile(sibSh);
console.log(JSON.stringify({
  mineU: Object.keys(mine.userData.uniforms),
  sibU: Object.keys(sib.userData.uniforms),
  mineL: locals(mains(mineSh)),
  sibL: locals(mains(sibSh)),
}));
""")
    shared_uniforms = set(out["mineU"]) & set(out["sibU"])
    assert shared_uniforms == {"uTime"}, shared_uniforms
    assert all(u.startswith("uCau") for u in out["mineU"] if u != "uTime")
    shared_locals = set(out["mineL"]) & set(out["sibL"])
    assert shared_locals == set(), shared_locals
    assert all(x.startswith("cau") for x in out["mineL"]), out["mineL"]
    # And nothing declared twice within the patch's own bodies either.
    assert len(out["mineL"]) == len(set(out["mineL"])), out["mineL"]


def test_the_whole_chain_shares_one_main_without_a_redefinition():
    """The load-bearing case: a submerged bank wears the rock
    projection, the waterline and this at once.  patchStandard chains by
    name, so every body must survive in call order, the cache key must
    name the whole chain (the first material to compile a key decides
    the source for all of them), the shared world varyings must be
    declared once however many patches ask for them, and no local may
    appear twice in the one main.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
import { patchShoreWet } from './lib/waterside.js';
import { patchTriplanar } from './lib/terrain_shade.js';
const sh = fake();
const m = new THREE.MeshStandardMaterial();
patchTriplanar(m);
patchShoreWet(m, { level: 1.2 });
patchCaustics(m, { level: 1.2, seed: 4 });
patchCaustics(m, { level: 1.4, seed: 4 });  // retune, not a 4th patch
m.onBeforeCompile(sh);
const fs = sh.fragmentShader, vs = sh.vertexShader;
const count = (s, re) => (s.match(re) || []).length;
const seen = {};
for (const name of locals(mains(sh))) seen[name] = (seen[name] || 0) + 1;
console.log(JSON.stringify({
  key: m.customProgramCacheKey(),
  level: m.userData.uniforms.uCauLevel.value,
  order: fs.indexOf('uTriA') < fs.indexOf('uWetDark')
      && fs.indexOf('uWetDark') < fs.indexOf('cauNet'),
  cau: fs.includes('totalEmissiveRadiance += cauTint'),
  varyingVs: count(vs, /varying vec3 vAstraWorld;/g),
  varyingFs: count(fs, /varying vec3 vAstraWorld;/g),
  utilOnce: count(fs, /float astraFbm2\\(/g),
  webOnce: count(fs, /float astraCauWeb\\(vec2 p\\)/g),
  timeOnce: count(fs, /uniform float uTime;/g),
  uniforms: ['uTriA', 'uWetY', 'uCauLevel', 'uCauAbs', 'uTime']
      .every((k) => !!sh.uniforms[k]),
  dupLocals: Object.keys(seen).filter((k) => seen[k] > 1),
}));
""")
    assert out["cau"], out
    assert out["order"], "bodies run in call order"
    # The key names the whole chain, in order, and this patch is last
    # because it was applied last.  (The sibling halves are asserted by
    # shape, not spelling: they are their own modules' contract.)
    parts = out["key"].split("+")
    assert parts[0].startswith("astra:") and len(parts) >= 3, out["key"]
    assert parts[-1] == "caustics:net", out["key"]
    assert out["level"] == 1.4, "re-applying retunes in place"
    assert out["uniforms"], "every patch's uniforms reach the program"
    # The world varyings are shared with the neighbours BY NAME on
    # purpose, so the pair is declared once for the three of them.
    assert out["varyingVs"] == 1 and out["varyingFs"] == 1
    assert out["utilOnce"] == 1 and out["timeOnce"] == 1
    assert out["webOnce"] == 1, "the helper is defined once"
    assert out["dupLocals"] == [], out["dupLocals"]


def test_the_shipped_module_is_deterministic_and_stage_safe():
    """A pool must come back identical on a re-render, so the seed is
    arithmetic and never a Math.random; and the patch's GLSL ships into
    the VERTEX stage as well, where a fragment-only builtin would fail
    every program in the engine at once — so fwidth and gl_FragCoord may
    appear only in the fragment body.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
const a = fake(), b = fake();
const opts = { level: 1.5, seed: 9, scale: 0.4, strength: 2 };
const ma = patchCaustics(new THREE.MeshStandardMaterial(), opts);
const mb = patchCaustics(new THREE.MeshStandardMaterial(), opts);
ma.onBeforeCompile(a);
mb.onBeforeCompile(b);
const seed = (m) => m.userData.uniforms.uCauSeed.value;
console.log(JSON.stringify({
  same: a.fragmentShader === b.fragmentShader
      && a.vertexShader === b.vertexShader,
  seedA: seed(ma), seedB: seed(mb),
  seed8: seed(patchCaustics(new THREE.MeshStandardMaterial(), { seed: 8 })),
  vsBody: a.vertexShader.slice(a.vertexShader.indexOf('void main')),
  vsHead: a.vertexShader.slice(0, a.vertexShader.indexOf('void main')),
}));
""")
    assert out["same"], "one seed, one shader"
    assert out["seedA"] == out["seedB"]
    # A seed must MOVE the lattice, not shift it by a cell — a one-cell
    # offset is the same pool drawn twice.
    assert abs(out["seed8"]["x"] - out["seedA"]["x"]) > 5, out["seed8"]
    # Nothing of this patch's own reaches the vertex stage but the world
    # position: fwidth there is only GLSL_UTIL's, behind ASTRA_FRAG.
    assert "fwidth" not in out["vsBody"]
    assert "gl_FragCoord" not in out["vsBody"], "vertex stage has no such"
    assert "astraCauWeb" not in out["vsHead"], "the web is fragment work"
    src = _LIB.read_text(encoding="utf-8")
    assert "Math.random" not in src
    assert "Date.now" not in src
    assert "export function patchCaustics" in src
    # One export: the library is the net, nothing else.
    assert src.count("\nexport ") == 1
    assert max(len(ln) for ln in src.splitlines()) <= 80


_SCENE = """
import * as THREE from 'three';
import { patchCaustics } from './lib/caustics.js';
import { patchShoreWet } from './lib/waterside.js';
import { patchTriplanar } from './lib/terrain_shade.js';
import { tickShaders } from './lib/shader.js';

export const BOUNDS = { min: [-8, -1, -6], max: [8, 5, 6] };

const LEVEL = 1.3;
const SUN = new THREE.Vector3(0.45, 0.75, 0.35);

export function createScene() {
  const scene = new THREE.Scene();
  // Without this USE_FOG is undefined and every fog branch compiles to
  // nothing — which is how a library ships fog chunks that cannot
  // compile at all and nobody finds out.
  scene.fog = new THREE.FogExp2(0xc9d6e0, 0.02);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  // And without a shadow CASTER the depth variant of every patched
  // material is never compiled either.
  const sun = new THREE.DirectionalLight(0xfff0d6, 2.5);
  sun.position.set(5, 8, 7);
  sun.castShadow = true;
  scene.add(sun);

  // The pool: floor, wall and steps on ONE material wearing all three
  // patches, which is the case the chain has to survive.
  const tile = new THREE.MeshStandardMaterial(
      { color: 0x4a5a5e, roughness: 0.85 });
  patchTriplanar(tile, { scale: 1.4 });
  patchShoreWet(tile, { level: LEVEL, band: 0.25 });
  patchCaustics(tile, { level: LEVEL, sunDir: SUN, depthFade: 2.4 });

  const floor = new THREE.Mesh(new THREE.BoxGeometry(11, 0.2, 8), tile);
  floor.position.set(0, 0.1, 0);
  floor.receiveShadow = true;
  scene.add(floor);

  const wall = new THREE.Mesh(new THREE.BoxGeometry(0.4, 2.6, 8), tile);
  wall.position.set(-5.3, 1.3, 0);
  wall.castShadow = wall.receiveShadow = true;
  scene.add(wall);

  // Four steps out of the water: two below the line, two above.
  for (let i = 0; i < 4; i++) {
    const h = 0.45 * (i + 1);
    const step = new THREE.Mesh(new THREE.BoxGeometry(0.7, h, 4), tile);
    step.position.set(4.6 - i * 0.7, 0.1 + h / 2, 1.4);
    step.castShadow = step.receiveShadow = true;
    scene.add(step);
  }

  // A boulder, half under: a curved surface at every angle to the sun.
  const rock = new THREE.MeshStandardMaterial(
      { color: 0x6f6559, roughness: 0.95 });
  patchTriplanar(rock, { scale: 0.9 });
  patchCaustics(rock, { level: LEVEL, sunDir: SUN, seed: 3 });
  const boulder = new THREE.Mesh(
      new THREE.IcosahedronGeometry(1.05, 2), rock);
  boulder.position.set(-1.6, 0.85, -1.2);
  boulder.castShadow = true;
  scene.add(boulder);

  // A lamp-lit corner: the local-source branch has to compile too.
  const ledge = new THREE.MeshStandardMaterial(
      { color: 0x585d5a, roughness: 0.9 });
  patchCaustics(ledge, { level: LEVEL, sunDir: SUN, seed: 5,
                         source: new THREE.Vector3(3, 3.2, -2.4),
                         reach: 5, color: 0xffe6c0 });
  const slab = new THREE.Mesh(new THREE.BoxGeometry(3, 0.3, 3), ledge);
  slab.position.set(3, 0.35, -2.4);
  slab.receiveShadow = true;
  scene.add(slab);

  // Instanced cobbles, so the USE_INSTANCING branch has to compile.
  const pebbles = new THREE.InstancedMesh(
      new THREE.IcosahedronGeometry(0.3, 1), rock, 10);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 10; i++) {
    m4.makeTranslation(-4 + i * 0.9, 0.32, -2.6 + (i % 3) * 0.7);
    pebbles.setMatrixAt(i, m4);
  }
  pebbles.castShadow = true;
  scene.add(pebbles);

  // A camera, because the host reports a scene with none as not booted
  // and never reaches the compile stage at all.
  return {
    scene,
    cameras: [{ name: 'a', position: [7, 3, 9], lookAt: [0, 1, 0],
                fov: 45 }],
    // One tick drives every patch in the chain; an un-advanced uTime is
    // a frozen net, which is the other way this reads as a texture.
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_the_chain_compiles_on_the_real_renderer():
    """The only witness that counts.  Everything above reads a string;
    whether the GPU accepts three chained bodies in one main, the
    instancing branch, a two-octave fbm inside a helper, a pow(16) and a
    gl_FragCoord dither per fragment cannot be asserted from source, and
    a patch that does not compile is worth nothing.  Runs the same
    wrapper the authoring agent runs, on our own renderer.
    """
    code, out = compile_scene(
        _SCENE, ("shader.js", "caustics.js", "waterside.js",
                 "terrain_shade.js"))
    assert code == 0, out
    assert "every program compiled" in out
    # A patched built-in keeps the built-in's own depth and fog chunks,
    # so nothing here may be flagged as discardable.
    assert "DISCARDED" not in out
