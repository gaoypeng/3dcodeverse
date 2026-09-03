"""waterside.js: the contact where ground, rock and water meet.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_waterside_lib.py).  Their renderer-contract assertions are
dropped; the physics claims, the shared-name contract and the
option-is-a-uniform law are kept, and the grading this port added is pinned
here so a later edit cannot flatten it back.

A bank that meets water at a flat cut reads as two slabs intersecting, and
every way these three patches can fail is SILENT: two of them routinely land
on the same ground material, so a chain that drops one leaves foam on dry
sand; a local declared twice in one injected main takes the whole material
down; and a patch on the addon Water's raw ShaderMaterial finds no
`<color_fragment>` hook and does nothing at all.

THE PORT'S OWN LAWS, each one a defect measured on OUR renderer (ACES,
exposure 1.0, no post chain, baked environment) in the fx showcase:

1. `gloss` retunes the WHOLE material, and a shore ground is mostly dry.
   The reference default of 0.45 took `MAT.soil()` from roughness 0.95 to
   0.4275 — the entire beach turned to satin and the frame came back at
   mean_lum 0.85 with the waterline invisible inside it.  The default is
   now a light 0.85, and the wet gleam is a per-pixel fresnel sheen inside
   the band instead.
2. Wetting darkens AND saturates.  Beach saturation went 0.039 -> 0.143
   and luminance 0.774 -> 0.638 on the same frame.
3. A band in world Y covers `band / slope` metres of ground, so on the one
   surface that is exactly level — a water plane — it covered the entire
   reach (contact 0.59 at t = 1.5) and laid streamers to the horizon.  The
   foam now fades out past its own footprint.
4. No effect here may be one flat value: foam carries a lace break, a thin
   tail and a hue break; the shoal carries a hue break that dies out with
   depth; and the shallow default is a muted jade, because the reference's
   pale sage (0.40-0.57 linear) came back off this renderer as white paint.
"""

from __future__ import annotations

import json

import pytest
from _probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "waterside.js")
_LIB_SRC = (LIB_DIR / "waterside.js").read_text(encoding="utf-8")

# The two hooks patchStandard injects into, as a material three would.
_FAKE_SHADER = """
const fake = () => ({
  vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
  fragmentShader: 'void main() {\\n#include <color_fragment>\\n}',
  uniforms: {},
});
"""


def _measure(script: str) -> dict:
    return measure(_FAKE_SHADER + script, _LIBS)


def test_the_wet_band_fades_above_the_line_instead_of_cutting_it():
    """Wet ground is darker ground — the whole cue.  What makes it a shore
    rather than a painted stripe is that the darkening survives fully BELOW
    the waterline and fades out over the band above it, and that the line
    itself wanders: a level contour is a ruled edge no bank has."""
    out = _measure("""
import * as THREE from 'three';
import { patchShoreWet } from './lib/waterside.js';
const sh = fake();
const m = patchShoreWet(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const u = (k) => m.userData.uniforms[k].value;
const t = patchShoreWet(new THREE.MeshStandardMaterial(),
    { level: 3.5, band: 0.8, darken: 1.4 });
console.log(JSON.stringify({
  fs: sh.fragmentShader, vs: sh.vertexShader,
  level: u('uWetY'), band: u('uWetBand'), darken: u('uWetDark'),
  tunedLevel: t.userData.uniforms.uWetY.value,
  tunedDark: t.userData.uniforms.uWetDark.value,
}));
""")
    assert out["level"] == 0 and out["band"] == 0.25
    assert out["darken"] == 0.45
    assert out["tunedLevel"] == 3.5
    # A darken over 1 would INVERT the albedo (1 - darken < 0) and paint the
    # bank with negative light, so it is clamped where it is set.
    assert out["tunedDark"] == 1.0
    fs = out["fs"]
    # Height above the water, not a UV: the bank has no useful one.
    assert "float wtH = vAstraWorld.y - uWetY;" in fs
    assert "astraContact(wtH, uWetBand)" in fs, "1 below the line, 0 above"
    assert "astraFbm2(vAstraWorld.xz * 0.7, 3)" in fs, "the line must wander"
    # The world position arrives from the vertex stage, instance transform
    # folded in, or every scattered rock takes the origin's waterline.
    assert "#ifdef USE_INSTANCING" in out["vs"]
    assert "wsP = instanceMatrix * wsP;" in out["vs"]


def test_wet_ground_is_darkened_and_deepened_then_gleams_at_the_grazing_edge():
    """A water film is not a grey multiply.  It drops the albedo, DEEPENS
    the colour already there, and gleams where the view grazes it — and it
    is the second of those that separates wet sand from sand in shadow
    (measured on the showcase frame: beach saturation 0.039 -> 0.143 with
    luminance 0.774 -> 0.638).

    The sheen has to be per pixel, because the material-wide `gloss` cannot
    tell the wet band from the dry beach behind it.  Its colour is the
    scene's own fog — the one sky-family colour a `<color_fragment>` patch
    can reach — under `#ifdef USE_FOG`, since `fogColor` does not exist in
    an unfogged program and reading it there fails every material at once.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchShoreWet } from './lib/waterside.js';
const sh = fake();
const m = patchShoreWet(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const u = (k) => m.userData.uniforms[k].value;
const t = patchShoreWet(new THREE.MeshStandardMaterial(),
    { saturate: 0.9, sheen: 0.04 });
const tu = (k) => t.userData.uniforms[k].value;
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  sat: u('uWetSat'), sheen: u('uWetSheen'),
  tSat: tu('uWetSat'), tSheen: tu('uWetSheen'),
}));
""")
    assert out["sat"] == 0.35 and out["sheen"] == 0.22
    assert out["tSat"] == 0.9 and out["tSheen"] == 0.04
    fs = out["fs"]
    assert "vec3 wtDry = diffuseColor.rgb;" in fs
    assert "float wtL = dot(wtDry, vec3(0.2126, 0.7152, 0.0722));" in fs
    # Saturating pushes AWAY from the fragment's own luminance, so the mix
    # factor is over 1 — and the darkening still scales the whole thing.
    assert "mix(vec3(wtL), wtDry, 1.0 + uWetSat)" in fs
    assert "* (1.0 - uWetDark);" in fs
    # A big `saturate` on a nearly-grey pixel can overshoot below zero, and
    # a negative albedo is a black hole in the middle of a beach.
    assert "mix(wtDry, max(wtWet, vec3(0.0)), wtK);" in fs
    # The sheen: world-space fresnel off the base's own normal, no extra
    # varying (cameraPosition is a three built-in in both stages).
    assert "vec3 wtV = normalize(cameraPosition - vAstraWorld);" in fs
    assert "float wtF = astraFresnel(vAstraWorldN, wtV, 4.0);" in fs
    assert "diffuseColor.rgb += wtSky * (wtF * wtK * uWetSheen);" in fs
    # Guarded, with a fallback: an unfogged scene must still compile.
    head = fs[fs.index("vec3 wtSky = vec3("):fs.index("vec3 wtV =")]
    assert "#ifdef USE_FOG" in head and "wtSky = fogColor;" in head
    assert "#endif" in head
    assert fs.count("fogColor") == 1, "only under the guard"


def test_wet_gloss_is_a_light_touch_because_a_shore_is_mostly_dry():
    """patchStandard's only fragment hook runs after `<color_fragment>`,
    which is BEFORE `<roughnessmap_fragment>` declares roughnessFactor — so
    per-pixel roughness is unreachable and writing to it would be a compile
    error on a line in three's assembled source.  The gloss therefore lands
    on the material, computed from the DRY value so that re-applying the
    patch cannot compound it down to a mirror.

    Which is exactly why the default is light.  A waterline goes on the
    SHORE GROUND, and the reference's 0.45 took `MAT.soil()` (roughness
    0.95) to 0.4275 across the whole beach: on this renderer that is satin
    dirt reflecting the sky, and the showcase frame came back at mean_lum
    0.85 with the wet band lost inside the wash.  0.85 keeps the dry sand
    above 0.8; a bank with a material of its own can still ask for 0.45."""
    out = _measure("""
import * as THREE from 'three';
import { patchShoreWet } from './lib/waterside.js';
const sh = fake();
const m = new THREE.MeshStandardMaterial({ roughness: 0.8 });
patchShoreWet(m);
const once = m.roughness;
patchShoreWet(m);
patchShoreWet(m);
const thrice = m.roughness;
patchShoreWet(m, { gloss: 1 });
const undone = m.roughness;
const soil = new THREE.MeshStandardMaterial({ roughness: 0.95 });
patchShoreWet(soil);
const dedicated = new THREE.MeshStandardMaterial({ roughness: 0.95 });
patchShoreWet(dedicated, { gloss: 0.45 });
const glossy = new THREE.MeshStandardMaterial({ roughness: 0.02 });
patchShoreWet(glossy, { gloss: 0 });
const basic = new THREE.MeshBasicMaterial();
patchShoreWet(basic);
patchShoreWet(m).onBeforeCompile(sh);
console.log(JSON.stringify({
  once, thrice, undone, soil: soil.roughness,
  dedicated: dedicated.roughness, floor: glossy.roughness,
  basicOk: basic.userData.astraPatches.length,
  fs: sh.fragmentShader,
}));
""")
    assert abs(out["once"] - 0.8 * 0.85) < 1e-9
    assert out["thrice"] == out["once"], "a retune must not compound"
    assert out["undone"] == 0.8, "gloss 1 restores the dry roughness"
    assert out["soil"] > 0.8, "a patched beach stays dirt, not satin"
    assert abs(out["dedicated"] - 0.95 * 0.45) < 1e-9, "still available"
    assert out["floor"] > 0, "a zero roughness is a black mirror, not wet"
    # A material without a roughness at all (Basic) must still take the
    # patch: the darkening is the cue that matters.
    assert out["basicOk"] == 2
    # Nothing in the GLSL may reach for roughnessFactor: it does not exist
    # yet at this hook.
    assert "roughnessFactor" not in out["fs"]


def test_foam_streamers_run_along_the_shore_not_across_it():
    """A field ranked ACROSS the shore gives contours parallel to the waves,
    which march at the viewer as bands.  So the noise is built in a frame
    taken from the surface itself — a bank tilts its normal up-slope, so the
    horizontal normal is the across-shore axis and its perpendicular is the
    shore — and stretched several-fold ALONG the shore into streamers.  A
    dead-flat surface has no shore direction to find, and a divide by its
    zero length would take the material with it, so it falls back to the
    world X axis."""
    out = _measure("""
import * as THREE from 'three';
import { patchShoreFoam } from './lib/waterside.js';
const sh = fake();
patchShoreFoam(new THREE.MeshStandardMaterial()).onBeforeCompile(sh);
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "vec2 fmNx = vAstraWorldN.xz;" in fs, "the frame is the surface's"
    assert "vec2 fmA = fmL > 1e-3 ? fmNx / fmL : vec2(1.0, 0.0);" in fs
    assert "vec2 fmB = vec2(-fmA.y, fmA.x);" in fs, "along = across turned 90"
    assert "float fmU = dot(vAstraWorld.xz, fmB);" in fs
    assert "float fmV = dot(vAstraWorld.xz, fmA);" in fs
    # The anisotropy IS the effect: slow along the shore (fmU), fast across
    # it (fmV), so the tear elongates into streamers.
    assert "fmU * 0.30" in fs and "fmV * 2.10" in fs
    along = float(fs.split("fmU * ")[1].split(" ")[0])
    across = float(fs.split("fmV * ")[1].split(" ")[0])
    assert across > along * 4, (along, across)


def test_foam_breathes_and_hands_over_to_both_sides_of_the_line():
    """An effect hands over to its neighbours instead of ending: the band is
    strongest AT the waterline and fades both up the bank and down under the
    surface, and the tear is a smoothstep, not a threshold.  It breathes by
    moving the LINE — two decorrelated swells, because one sine is a
    metronome — which is what makes the foam run up and drain rather than
    blink.  uTime must reach the fragment stage DECLARED; patchStandard adds
    that only because the declaration pass runs after the bodies are
    injected."""
    out = _measure("""
import * as THREE from 'three';
import { patchShoreFoam, patchShoreWet } from './lib/waterside.js';
const sh = fake();
const m = patchShoreFoam(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const u = (k) => m.userData.uniforms[k].value;
const t = patchShoreFoam(new THREE.MeshStandardMaterial(),
    { color: 0x223344, speed: 2.5, strength: 0.4, band: 1.2, level: -1 });
const tu = (k) => t.userData.uniforms[k].value;
const still = patchShoreWet(new THREE.MeshStandardMaterial());
const stillSh = fake();
still.onBeforeCompile(stillSh);
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  band: u('uFoamBand'), speed: u('uFoamSpeed'), amt: u('uFoamAmt'),
  color: u('uFoamColor').getHex(), level: u('uFoamY'),
  tColor: tu('uFoamColor').getHex(), tSpeed: tu('uFoamSpeed'),
  tAmt: tu('uFoamAmt'), tBand: tu('uFoamBand'), tLevel: tu('uFoamY'),
  timeDecls: (sh.fragmentShader.match(/uniform float uTime;/g) || []).length,
  wetIsStill: !/\\buTime\\b/.test(stillSh.fragmentShader),
  hasUniform: !!sh.uniforms.uFoamColor,
}));
""")
    assert out["band"] == 0.35 and out["speed"] == 0.6
    assert out["amt"] == 0.85 and out["level"] == 0
    assert out["tColor"] == 0x223344 and out["tSpeed"] == 2.5
    assert out["tAmt"] == 0.4 and out["tBand"] == 1.2 and out["tLevel"] == -1
    fs = out["fs"]
    assert "astraContact(abs(fmH), uFoamBand)" in fs, "strongest AT the line"
    assert "fmK *= smoothstep(0.24, 0.62, fmN);" in fs, "torn, not thresholded"
    # The swell moves the line itself, and two terms at incommensurate rates
    # keep it off a metronome.
    assert "sin(fmT) * 0.35 + sin(fmT * 0.63 + 1.7) * 0.22" in fs
    assert "vAstraWorld.y - uFoamY - fmS * uFoamBand" in fs
    assert "float fmT = uTime * uFoamSpeed;" in fs
    assert out["timeDecls"] == 1, "read undeclared, uTime is a compile error"
    assert out["wetIsStill"], "only the animated patch pays for uTime"
    assert out["hasUniform"], "the patch's uniforms reach the program"


def test_foam_is_lace_and_a_thin_tail_not_one_poured_white():
    """Foam is bubbles.  One field tears the band into streamers, a second
    much finer one breaks the churn inside it, only the churn goes bright
    (the tail is a thin film that still shows what is under it), and a hue
    break keeps any two square metres of it off the same white.

    The default white is graded DOWN as well: 0xeef4f5 runs 0.87 linear in
    blue, over the 0.8 ceiling this project holds non-emissive albedos to,
    and it is the first thing to clip when a bloom pass lands."""
    out = _measure("""
import * as THREE from 'three';
import { patchShoreFoam } from './lib/waterside.js';
const sh = fake();
const m = patchShoreFoam(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const c = m.userData.uniforms.uFoamColor.value;
console.log(JSON.stringify({
  fs: sh.fragmentShader, hex: c.getHex(),
  linear: [c.r, c.g, c.b],
}));
""")
    fs = out["fs"]
    assert out["hex"] == 0xdfe6e4
    # THREE.Color holds the working (linear) value; the ceiling is the
    # point of the regrade, so measure it rather than trusting the hex.
    assert max(out["linear"]) < 0.8, out["linear"]
    assert min(out["linear"]) > 0.7, "still reads as foam, not grey"
    # The lace: a second field, finer across the shore than the streamers
    # and drifting faster.
    assert "float fmD = astraFbm2(vec2(fmU * 1.70," in fs
    assert "fmV * 6.40 - fmT * 0.60), 2);" in fs
    assert "fmK *= 0.55 + 0.45 * smoothstep(0.18, 0.72, fmD);" in fs
    # The tail keeps most of the wet colour; only the churn is white.
    assert "vec3 fmC = mix(uFoamColor * 0.70, uFoamColor," in fs
    assert "smoothstep(0.30, 0.88, fmN));" in fs
    assert "fmC = astraHueBreak(fmC, vAstraWorld.xz, 1.7, 0.14);" in fs
    assert "mix(diffuseColor.rgb, fmC," in fs


def test_foam_fades_off_a_surface_too_level_to_hold_a_band():
    """A band of `band` metres in world Y covers `band * Ny / |Nxz|` metres
    of GROUND, so the flatter the surface the wider it spreads — and on the
    one surface that is exactly level, a water plane, it spreads over the
    whole reach.  Measured in the showcase: a foam patch on a flat reach hit
    every fragment of it (contact 0.59 at t = 1.5) and laid streamers out to
    the horizon.

    So the band fades once its own footprint passes `reach`.  This runs the
    SHIPPED lines — they are scalar GLSL, which is valid JS once the type
    keywords go — on a level plane, a 5-degree beach and a mooring pile."""
    out = _measure("""
import * as THREE from 'three';
import { patchShoreFoam } from './lib/waterside.js';
const sh = fake();
const m = patchShoreFoam(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const fs = sh.fragmentShader;
const from = fs.indexOf('  float fmSpan =');
const tail = 'uFoamReach * 2.5, fmSpan);';
const to = fs.indexOf(tail) + tail.length;
const src = fs.slice(from, to).replace(/\\bfloat\\b/g, 'let');
const run = (ny, nxz, band, reach) => {
  const f = new Function(
      'vAstraWorldN', 'fmL', 'uFoamBand', 'uFoamReach', 'fmK',
      'abs', 'max', 'smoothstep',
      src + '\\nreturn fmK;');
  return f({ y: ny }, nxz, band, reach, 1,
           Math.abs, Math.max,
           (e0, e1, x) => {
             const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
             return t * t * (3 - 2 * t);
           });
};
console.log(JSON.stringify({
  src,
  reach: m.userData.uniforms.uFoamReach.value,
  tuned: patchShoreFoam(new THREE.MeshStandardMaterial(), { reach: 40 })
      .userData.uniforms.uFoamReach.value,
  level: run(1, 0, 0.35, 3),
  beach5deg: run(0.9962, 0.0872, 0.35, 3),
  bank20deg: run(0.9397, 0.3420, 0.35, 3),
  pile: run(0.0, 1.0, 0.35, 3),
  levelOptOut: run(1, 0, 0.35, 1e9),
}));
""")
    assert out["reach"] == 3 and out["tuned"] == 40
    assert "uFoamBand * abs(vAstraWorldN.y) / max(fmL, 1e-4)" in out["src"]
    assert out["level"] == 0, "a level plane holds no band of its own"
    assert out["pile"] == 1, "a vertical face is all waterline"
    assert out["bank20deg"] == 1, "a real bank is untouched"
    # A 5-degree beach spreads 4 m, just past the default: it keeps most of
    # its foam, and losing the last of it is the point of the ramp.
    assert 0.8 < out["beach5deg"] < 1.0, out["beach5deg"]
    # The opt-out is a uniform, so a caller who WANTS a wash over a whole
    # reach can still have one without a second compiled program.
    assert out["levelOptOut"] == 1


def test_shallow_water_takes_its_depth_from_a_bed_fitted_on_the_cpu():
    """Depth is the whole patch, and there are only two ways to get it in a
    fragment shader here.  World Y against a bedLevel is ONE number over a
    flat water plane, so it paints the reach a single colour; a true bedAt
    per fragment needs the heightfield as a texture or a depth prepass, and
    this engine runs neither.  So bedAt is fitted on the CPU to a tilted
    plane — exact for a bed that slopes, which is what a bank is — and
    shipped as three floats in one uniform."""
    out = _measure("""
import * as THREE from 'three';
import { patchShallowWater } from './lib/waterside.js';
const sh = fake();
const bedAt = (x, z) => -1.25 + 0.05 * x - 0.02 * z;
const m = patchShallowWater(new THREE.MeshStandardMaterial(), {
  bedAt, bounds: [-30, -20, 30, 40], deep: 0x102030,
  shallow: new THREE.Color(0xccbb99), range: 3.5,
});
m.onBeforeCompile(sh);
const u = (k) => m.userData.uniforms[k].value;
// A bed that is not a plane still has to give a sane average slope.
const bowl = patchShallowWater(new THREE.MeshStandardMaterial(),
    { bedAt: (x, z) => -4 + 0.002 * (x * x + z * z),
      bounds: [-20, -20, 20, 20] });
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  bed: u('uShoalBed'), range: u('uShoalRange'),
  deep: u('uShoalDeep').getHex(), shallow: u('uShoalShallow').getHex(),
  bowl: bowl.userData.uniforms.uShoalBed.value,
}));
""")
    bed = out["bed"]
    assert abs(bed["x"] - -1.25) < 1e-6, bed
    assert abs(bed["y"] - 0.05) < 1e-9 and abs(bed["z"] - -0.02) < 1e-9
    assert out["range"] == 3.5
    assert out["deep"] == 0x102030 and out["shallow"] == 0xccbb99
    # A symmetric bowl has no net tilt, and its mean sits above its lowest
    # point — the fit is least squares, not a corner sample.
    assert abs(out["bowl"]["y"]) < 1e-9 and abs(out["bowl"]["z"]) < 1e-9
    assert -4 < out["bowl"]["x"] < -3
    fs = out["fs"]
    assert "uniform vec3 uShoalBed;" in fs, "three floats, not a sampler"
    assert "sampler2D" not in fs, "no texture upload, no extra pass"
    assert "uShoalBed.y * vAstraWorld.x" in fs
    assert "uShoalBed.z * vAstraWorld.z" in fs
    assert "float swD = vAstraWorld.y - swBed;" in fs
    assert "mix(uShoalShallow, uShoalDeep, swK)" in fs
    # The shoal edge wanders, or the ramp rules a contour across open water
    # in exactly the way a real one never does.
    assert "astraFbm2(vAstraWorld.xz * 0.35, 3)" in fs


def test_shallow_water_without_a_bed_falls_back_to_a_constant_level():
    """The documented degenerate case: no bedAt means a flat bed at
    bedLevel, which on a flat surface is one depth for the whole reach.  It
    must still be a valid plane (zero tilt), never a NaN — a NaN colour
    renders the water black."""
    out = _measure("""
import * as THREE from 'three';
import { patchShallowWater } from './lib/waterside.js';
const d = patchShallowWater(new THREE.MeshStandardMaterial());
const l = patchShallowWater(new THREE.MeshStandardMaterial(),
    { bedLevel: -6.5 });
const bad = patchShallowWater(new THREE.MeshStandardMaterial(),
    { bedAt: () => NaN, bedLevel: -3 });
const u = (m) => m.userData.uniforms.uShoalBed.value;
console.log(JSON.stringify({
  def: u(d), lev: u(l), bad: u(bad),
  range: d.userData.uniforms.uShoalRange.value,
  deep: d.userData.uniforms.uShoalDeep.value.getHex(),
}));
""")
    assert out["def"] == {"x": -2, "y": 0, "z": 0}
    assert out["lev"] == {"x": -6.5, "y": 0, "z": 0}
    assert out["bad"] == {"x": -3, "y": 0, "z": 0}, "a NaN bed falls back"
    assert out["range"] == 2
    # The deep end defaults to water.js's graded waterColor, so a reach
    # beside the scene's one RTT ocean agrees with it.
    assert out["deep"] == 0x0e3f5c


def test_shallow_water_is_a_graded_reach_not_a_two_colour_ramp():
    """Three things separate water from a painted ramp, and the reference
    shipped one of them.  The wander is theirs.  This port adds the hue
    break — silt, weed and bed patches move the colour at metre scale, and
    they do it in the SHALLOWS, where the bed shows through, while the deep
    end holds still — and the alpha fade, so thin water is see-through
    water and the bed under the shoal edge says where the bank goes.

    The shallow default is regraded too: the reference's pale sage ran
    0.40-0.57 linear and came back off this renderer (ACES, exposure 1.0)
    as white paint on the water.  A muted jade at ~0.27 reads as water."""
    out = _measure("""
import * as THREE from 'three';
import { patchShallowWater } from './lib/waterside.js';
const sh = fake();
const m = new THREE.MeshStandardMaterial({ transparent: true, opacity: 0.9 });
patchShallowWater(m);
m.onBeforeCompile(sh);
const shallow = m.userData.uniforms.uShoalShallow.value;
const opaque = new THREE.MeshStandardMaterial();
patchShallowWater(opaque);
const asked = new THREE.MeshStandardMaterial({ transparent: true });
patchShallowWater(asked, { edgeFade: 0.2 });
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  hex: shallow.getHex(), linear: [shallow.r, shallow.g, shallow.b],
  fade: m.userData.uniforms.uShoalFade.value,
  opaqueFade: opaque.userData.uniforms.uShoalFade.value,
  askedFade: asked.userData.uniforms.uShoalFade.value,
  key: m.customProgramCacheKey(),
  opaqueKey: opaque.customProgramCacheKey(),
}));
""")
    assert out["hex"] == 0x6fa392
    assert 0.15 < max(out["linear"]) < 0.4, out["linear"]
    fs = out["fs"]
    assert "vec3 swC = mix(uShoalShallow, uShoalDeep, swK);" in fs
    assert "swC = astraHueBreak(swC, vAstraWorld.xz, 0.09," in fs
    # The break dies out with depth: 1 - swK * 0.8 is the shallow weight.
    assert "0.34 * (1.0 - swK * 0.8));" in fs
    assert "diffuseColor.a *= mix(uShoalFade, 1.0, swK);" in fs
    assert out["fade"] == 0.55, "a transparent reach thins at its edge"
    assert out["opaqueFade"] == 1, "three discards alpha on an opaque one"
    assert out["askedFade"] == 0.2
    # The fade is a UNIFORM, never a second source variant: the cache key
    # names the patch, and the first material to compile a key decides the
    # GLSL for every material that shares it.
    assert out["key"] == out["opaqueKey"]


def test_the_three_patches_chain_on_one_material():
    """Two of these land on the same ground material as a matter of course,
    so the chain is the load-bearing case.  patchStandard chains by name:
    the shared world-space base must be injected ONCE however many patches
    ask for it, every body must survive, the cache key must name the whole
    chain in order (the first material to compile a key decides the source
    for all of them), and no local may be declared twice in one main."""
    out = _measure("""
import * as THREE from 'three';
import { patchShoreWet, patchShoreFoam, patchShallowWater }
    from './lib/waterside.js';
const sh = fake();
const m = new THREE.MeshStandardMaterial();
patchShoreWet(m, { level: 0.4 });
patchShoreFoam(m, { level: 0.4 });
patchShallowWater(m, { bedLevel: -1 });
patchShoreWet(m, { level: 0.5 });  // retune, not a fourth patch
m.onBeforeCompile(sh);
const fs = sh.fragmentShader, vs = sh.vertexShader;
const count = (s, re) => (s.match(re) || []).length;
// Only what the bodies declare INSIDE main: the util block's own helpers
// are functions, each with its own scope.
const locals = {};
const mains = fs.slice(fs.indexOf('void main')) + '\\n' +
    vs.slice(vs.indexOf('void main'));
const decl = /^\\s+(?:float|vec2|vec3|vec4)\\s+(\\w+)/gm;
for (const hit of mains.match(decl) || []) {
  const name = hit.trim().split(/\\s+/)[1];
  locals[name] = (locals[name] || 0) + 1;
}
console.log(JSON.stringify({
  key: m.customProgramCacheKey(),
  wet: fs.includes('uWetDark'),
  foam: fs.includes('mix(diffuseColor.rgb, fmC,'),
  shallow: fs.includes('mix(uShoalShallow, uShoalDeep, swK)'),
  order: fs.indexOf('wtK') < fs.indexOf('fmK') &&
      fs.indexOf('fmK') < fs.indexOf('swK'),
  level: m.userData.uniforms.uWetY.value,
  uniforms: ['uWetY', 'uWetSheen', 'uFoamY', 'uFoamReach', 'uShoalBed',
             'uShoalFade', 'uTime'].every((k) => !!sh.uniforms[k]),
  baseOnce: count(vs, /vAstraWorld = /g),
  varyingVs: count(vs, /varying vec3 vAstraWorld;/g),
  varyingFs: count(fs, /varying vec3 vAstraWorld;/g),
  utilOnce: count(fs, /float astraFbm2\\(/g),
  timeOnce: count(fs, /uniform float uTime;/g),
  dupLocals: Object.keys(locals).filter((k) => locals[k] > 1),
}));
""")
    assert out["wet"] and out["foam"] and out["shallow"], out
    assert out["order"], "bodies run in call order"
    assert out["key"] == ("astra:waterside:base+waterside:shoreWet"
                          "+waterside:shoreFoam+waterside:shallow")
    assert out["level"] == 0.5, "re-applying retunes in place"
    assert out["uniforms"], "every patch's uniforms reach the program"
    assert out["baseOnce"] == 1, "the shared world body is injected once"
    assert out["varyingVs"] == 1 and out["varyingFs"] == 1
    assert out["utilOnce"] == 1 and out["timeOnce"] == 1
    # One redeclared local is a GLSL redefinition that takes the whole
    # material down — the reason each patch prefixes its own.
    assert out["dupLocals"] == [], out["dupLocals"]


def test_a_hookless_material_is_reported_instead_of_doing_nothing():
    """The addon Water from water.js is a raw ShaderMaterial: it has no
    `<color_fragment>` and no `<begin_vertex>`, so patching it is a no-op
    with no error — the exact silent failure this library exists to avoid.
    Say so, and only there: a built-in must stay quiet."""
    out = _measure("""
import * as THREE from 'three';
import { patchShallowWater, patchShoreFoam } from './lib/waterside.js';
const said = [];
console.warn = (msg) => said.push(String(msg));
patchShallowWater(new THREE.ShaderMaterial({ name: 'Ocean' }));
const quiet = said.length;
patchShoreFoam(new THREE.MeshStandardMaterial());
patchShallowWater(new THREE.MeshPhysicalMaterial());
console.log(JSON.stringify({ said, quiet, after: said.length }));
""")
    assert out["quiet"] == 1 and out["after"] == 1
    said = out["said"][0]
    assert "Ocean" in said and "color_fragment" in said


def test_the_shipped_module_is_deterministic_and_stage_safe():
    """A waterline must come back identical on a re-render, and its GLSL
    ships into the VERTEX stage as well: a fragment-only builtin there
    fails every program in the engine at once."""
    src = _LIB_SRC
    assert "Math.random" not in src
    assert "Date.now" not in src
    assert "fwidth" not in src
    for name in ("patchShoreWet", "patchShoreFoam", "patchShallowWater"):
        assert f"export function {name}" in src, name
    # Three exports, no more: the library is the contact transitions.
    assert src.count("\nexport ") == 3
    assert max(len(ln) for ln in src.splitlines()) <= 80


_SCENE = """
import * as THREE from 'three';
import { patchShoreWet, patchShoreFoam, patchShallowWater }
    from './lib/waterside.js';
import { tickShaders } from './lib/shader.js';

const LEVEL = 0.4;
const bedAt = (x, z) => -0.9 + 0.03 * x - 0.02 * z;

export function createScene() {
  const scene = new THREE.Scene();
  // FOGGED, because the wet sheen reads fogColor under #ifdef USE_FOG and
  // an unfogged compile never touches that branch.
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  scene.add(new THREE.DirectionalLight(0xffffff, 2.4));
  scene.add(new THREE.HemisphereLight(0xbfd4ea, 0x6b5a44, 0.7));

  // The bank: two patches on ONE material, the routine case.
  const bank = new THREE.Mesh(
      new THREE.PlaneGeometry(60, 40, 32, 24),
      new THREE.MeshStandardMaterial({ color: 0x6f6250, roughness: 0.92 }));
  bank.rotation.x = -Math.PI / 2.2;
  bank.receiveShadow = true;
  bank.name = 'Bank';
  patchShoreWet(bank.material, { level: LEVEL, band: 0.3 });
  patchShoreFoam(bank.material, { level: LEVEL });
  scene.add(bank);

  // Rocks: instanced, so the USE_INSTANCING branch has to compile.
  const rocks = new THREE.InstancedMesh(
      new THREE.IcosahedronGeometry(0.8, 1),
      new THREE.MeshStandardMaterial({ color: 0x7d766c }), 8);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 8; i++) {
    m4.makeTranslation(-12 + i * 3, LEVEL - 0.2, 2 + (i % 3));
    rocks.setMatrixAt(i, m4);
  }
  rocks.castShadow = true;
  rocks.name = 'ShoreRocks';
  patchShoreWet(rocks.material, { level: LEVEL, gloss: 0.5 });
  patchShoreFoam(rocks.material, { level: LEVEL, strength: 0.7 });
  scene.add(rocks);

  // The reach: a plain standard material, never the addon Water.
  const water = new THREE.Mesh(
      new THREE.PlaneGeometry(60, 40),
      new THREE.MeshStandardMaterial({
        color: 0xffffff, roughness: 0.18, metalness: 0,
        transparent: true, opacity: 0.9 }));
  water.rotation.x = -Math.PI / 2;
  water.position.y = LEVEL;
  water.name = 'Reach';
  patchShallowWater(water.material, { bedAt, range: 1.6 });
  patchShoreFoam(water.material, { level: LEVEL, strength: 0.6 });
  scene.add(water);

  // A camera is not decoration: the host reports `ok: false` with an EMPTY
  // error when `cameras` is empty, so a scene with none fails the compile
  // preflight before a program is built.
  return {
    scene,
    cameras: [{ name: 'hero', position: [9, 4, 11], lookAt: [0, 1, 0],
                fov: 45 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_the_whole_waterline_compiles_on_the_gpu_in_a_fogged_scene():
    """The only witness that counts.  Everything above reads a string;
    whether the GPU accepts the GLSL — the instancing branch, the shore
    frame's divide, the fogColor branch of the sheen, three chained bodies
    in one main, a vec3 uniform carrying a fitted plane — cannot be
    asserted from source, and a patch that does not compile is worth
    nothing.  Runs the same preflight the authoring agent runs."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] and report["errors"] == [], report
    # Patched built-ins carry the built-in's own depth and fog chunks, so
    # nothing here may be flagged as discardable or unfogged.
    assert report["warnings"] == [], report["warnings"]
    # bank, rocks (one key, two programs with the instanced one), reach.
    assert report["compile"]["custom_materials"] >= 3, report
    assert report["compile"]["gpu"], report
    # And the OTHER side of the guard: with no fog in the scene there is no
    # `fogColor` in the program at all, and the sheen has to fall back to
    # its constant rather than fail every material that wears it.
    code, out = compile_scene(
        _SCENE.replace("scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);",
                       "// unfogged: the sheen falls back to its constant"),
        _LIBS)
    assert code == 0, out
    assert json.loads(out.strip().splitlines()[-1])["ok"], out
