"""atmosphere.js — the two distance cues geometry alone cannot give.

`scene.fog` is a function of camera distance, so it veils a figure's
head as much as its boots: a fog BANK has to be geometry with a height
profile, and the failure mode of that idea is a milky SLAB with a lid, a
rectangular edge and a contour line wherever a sheet crosses something
standing in it.  FogExp2 also dims without shifting hue, so a correctly
built far field still reads as a saturated tabletop model.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_atmosphere_lib.py).  Their renderer-contract assertions are
kept (shader.js still ships the depth + fog chunks here), and the port
adds what THIS renderer needed: the colour of the air is read off the
scene (`scene.environment`, its fog, its lights) instead of being a
baked daylight white, which on a night rig painted the ground under the
bank (69,66,64) against a (26,36,64) sky — the brightest thing in frame.
"""
from __future__ import annotations

import json

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("atmosphere.js", "environment.js", "sky.js")


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


def test_the_bank_is_a_stack_packed_at_the_ground_not_a_slab():
    """One box of fog is a slab; a stack of sheets whose spacing tightens
    toward the ground is what puts the extinction in the first metre,
    where the wading happens."""
    out = _measure("""
import * as THREE from 'three';
import { makeHeightFog } from './lib/atmosphere.js';
const g = makeHeightFog({ extent: 60, top: 4, density: 0.75, seed: 5 });
const mesh = g.children[0];
const geo = mesh.geometry;
const hs = [...new Set(Array.from(geo.attributes.aH.array))]
    .sort((a, b) => a - b);
const pos = geo.attributes.position.array;
let minX = 1e9, maxX = -1e9;
for (let i = 0; i < pos.length; i += 3) {
  minX = Math.min(minX, pos[i]);
  maxX = Math.max(maxX, pos[i]);
}
const u = mesh.material.uniforms;
console.log(JSON.stringify({
  name: g.name, meshName: mesh.name, children: g.children.length,
  sheets: hs.length, lowest: hs[0], highest: hs[hs.length - 1],
  firstGap: hs[1] - hs[0], lastGap: hs[hs.length - 1] - hs[hs.length - 2],
  spanX: maxX - minX,
  transparent: mesh.material.transparent,
  depthWrite: mesh.material.depthWrite,
  doubleSide: mesh.material.side === THREE.DoubleSide,
  radius: u.uRadius.value, top: u.uTop.value, density: u.uDensity.value,
  hasTick: typeof g.userData.tick === 'function',
}));
""")
    assert out["name"] == "HeightFog" and out["children"] == 1
    # A sheet's plane crosses anything vertical along a horizontal line,
    # and the number of sheets in front changes across it: the sheet
    # count IS the quantisation of the height integral.  At the
    # reference's 12 a crate standing in the bank wore six hard contour
    # bands on this renderer.
    assert out["sheets"] >= 20, "the sheet count is the step size"
    # Off the soil (no z-fight with the ground) but low enough that the
    # bottom of the bank is the ground.
    assert 0 < out["lowest"] < 0.1
    assert out["highest"] == 4, "the top sheet sits exactly at `top`"
    assert out["firstGap"] < out["lastGap"] * 0.5, (
        "sheets must pack toward the ground where the fog is dense")
    assert out["spanX"] == 60
    # A fog bank that writes depth erases whatever it is standing in.
    assert out["transparent"] and not out["depthWrite"]
    assert out["doubleSide"], "the bank is seen from above and inside"
    assert out["radius"] * 2 == out["spanX"], (
        "the feather radius is the inscribed circle: any larger and the "
        "square footprint reaches full opacity at its own corner")
    assert out["hasTick"]


def test_the_bank_follows_the_terrain_and_is_free_when_it_is_flat():
    """A flat sheet stack over relief hangs in the air on the high
    ground and buries the low ground; over flat ground the same
    tessellation is pure waste, since every variation is per fragment."""
    out = _measure("""
import { makeHeightFog } from './lib/atmosphere.js';
const hAt = (x, z) => Math.sin(x * 0.15) * 2 + Math.cos(z * 0.1);
const t = makeHeightFog({ extent: 40, top: 3, heightAt: hAt, seed: 2 });
const f = makeHeightFog({ extent: 40, top: 3, seed: 2 });
const gt = t.children[0].geometry, gf = f.children[0].geometry;
const p = gt.attributes.position.array, h = gt.attributes.aH.array;
let worst = 0, relief = 0, lowest = 1e9;
for (let i = 0, k = 0; i < p.length; i += 3, k++) {
  worst = Math.max(worst, Math.abs(p[i + 1] - (hAt(p[i], p[i + 2]) + h[k])));
  if (h[k] === h[0]) {
    relief = Math.max(relief, p[i + 1]);
    lowest = Math.min(lowest, p[i + 1]);
  }
}
const pf = gf.attributes.position.array, hf = gf.attributes.aH.array;
let flatWorst = 0;
for (let i = 0, k = 0; i < pf.length; i += 3, k++) {
  flatWorst = Math.max(flatWorst, Math.abs(pf[i + 1] - hf[k]));
}
console.log(JSON.stringify({
  worst, flatWorst, bottomRelief: relief - lowest,
  terrainVerts: gt.attributes.position.count,
  flatVerts: gf.attributes.position.count,
  sheets: new Set(Array.from(hf)).size,
}));
""")
    # Float32 storage, so exactly is 1e-5 in metres — 10 microns.
    assert out["worst"] < 1e-5, "every vertex sits heightAt(x, z) + aH up"
    assert out["flatWorst"] < 1e-5, "with no heightAt the sheets are flat"
    assert out["bottomRelief"] > 3, "the bottom sheet really rides relief"
    assert out["terrainVerts"] > 5000, "relief needs tessellation"
    # Four corners per sheet: flat ground pays for no subdivision at all.
    assert out["flatVerts"] == out["sheets"] * 4


def test_the_bank_is_the_air_a_ray_flies_through_not_a_stack_of_alphas():
    """A stack of horizontal sheets is seen EDGE-ON, so a ray crosses
    every one of them however near the ground it lands: sheets at a
    constant alpha composite to the same near-opaque value for every ray
    alike, near, far, steep or grazing.  Each sheet instead carries the
    slab of column it samples and charges Beer-Lambert on the METRES of
    air the view ray crosses in it, so the ground keeps its own colour
    underfoot and the band thickens down the valley."""
    out = _measure("""
import { makeHeightFog } from './lib/atmosphere.js';

// CPU mirror of the fragment math, over the REAL geometry and
// uniforms; the source assertions below pin that the GLSL is this.
const smoothstep = (a, b, x) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};
function sheets(g) {
  const geo = g.children[0].geometry;
  const h = geo.attributes.aH.array, dh = geo.attributes.aDH.array;
  const seen = new Map();
  for (let i = 0; i < h.length; i++) {
    if (!seen.has(h[i])) seen.set(h[i], dh[i]);
  }
  return [...seen.entries()].sort((a, b) => a[0] - b[0]);
}
// Opacity of a ray from an eye `up` metres over the ground to ground
// `dist` metres away, noise at its mid value.
function opacity(g, dist, up) {
  const u = g.children[0].material.uniforms;
  const sigma = -Math.log(1 - Math.min(u.uDensity.value, 0.995)) / 10;
  const climb = Math.max(up / dist, 1e-3);
  let T = 1;
  for (const [h, dh] of sheets(g)) {
    const prof = Math.exp(-h / u.uScale.value)
        * (1 - smoothstep(u.uTop.value * 0.45, u.uTop.value, h));
    const breakup = 1 - 0.5 * smoothstep(0.10, 0.85, h / u.uTop.value);
    const air = Math.min(dh / climb, dist) * prof;
    T *= Math.exp(-sigma * air * breakup * 1.0);
  }
  return 1 - T;
}
const g = makeHeightFog({ extent: 60, height: 1.6, seed: 7 });
const wide = makeHeightFog({ extent: 200, height: 1.6, seed: 7 });
const m = g.children[0].material;
console.log(JSON.stringify({
  height: m.uniforms.uTop.value,
  topStillWorks: makeHeightFog({ top: 2.5 }).children[0]
      .material.uniforms.uTop.value,
  column: sheets(g).reduce((s, [, dh]) => s + dh, 0),
  near: opacity(g, 8, 5.2),
  mid: opacity(g, 20, 5.2),
  far: opacity(g, 80, 5.2),
  horizon: opacity(g, 200, 5.2),
  // The dial is a property of the AIR, so the same density in a bank
  // three times the size is the same veil.
  wideFar: opacity(wide, 80, 5.2),
  fs: m.fragmentShader,
}));
""")
    assert out["height"] == 1.6, (
        "`height` is what every other lib here calls this (watermist, "
        "rain, grass); ignoring it shipped a 4 m bank to a caller who "
        "asked for 1.6 m")
    assert out["topStillWorks"] == 2.5, "`top` is the old name, kept"
    assert abs(out["column"] - 1.6) < 1e-6, (
        "the slabs must partition [0, height] exactly once — a gap "
        "loses air and an overlap counts it twice")
    # The fix, as a number: a ray that lands 8 m from the eye crosses
    # every sheet and must still collect only a wisp.
    assert out["near"] < 0.10, (
        "ground fog is invisible at your feet; a constant per-sheet "
        "alpha put it at 0.9932 here")
    assert out["far"] > 0.45 and out["horizon"] > 0.75, (
        "and solid down the valley — the fog must not be fixed by "
        "making it invisible")
    assert out["far"] > out["mid"] > out["near"], "monotone with distance"
    assert out["far"] / out["near"] > 5, (
        "the whole cue is the RATIO: a constant alpha gave near and far "
        "the same 0.9932")
    assert abs(out["wideFar"] - out["far"]) < 1e-6, (
        "`density` is extinction per metre of air, so it means the "
        "same veil at any extent")
    fs = out["fs"]
    # The three terms that make the alpha an integral instead of a
    # sticker: the view ray, the slab, and Beer-Lambert over the two.
    assert "vec3 ray = vWorld - cameraPosition;" in fs
    assert "float air = min(vDH / climb, dist) * prof * edge;" in fs, (
        "a sheet charges its own slab crossed at the ray's slope, and "
        "never more air than the ray has flown")
    assert "float a = 1.0 - exp(-sigma * air" in fs, (
        "composited alphas must telescope to exp(-sum of taus), or the "
        "sheets behind the first each take another bite")


def test_the_sheets_stay_out_of_the_ambient_occlusion_buffer():
    """A depth+normal prepass (GTAOPass, and the side track's post chain
    here) rebuilds the scene with an OVERRIDE material, which sees a
    stack of transparent sheets as a stack of solid floors centimetres
    apart, each occluding the next: measured at 50/255 of darkening on
    ground the fog was not even touching, edged by its own footprint."""
    out = _measure("""
import * as THREE from 'three';
import { makeHeightFog } from './lib/atmosphere.js';
const mesh = makeHeightFog({ extent: 60, height: 1.6 }).children[0];
const geo = mesh.geometry;
const own = mesh.material;
const alien = new THREE.MeshNormalMaterial();
mesh.onBeforeRender(null, null, null, geo, alien);
const hidden = geo.drawRange.count;
mesh.onAfterRender(null, null, null, geo, alien);
const restored = geo.drawRange.count;
mesh.onBeforeRender(null, null, null, geo, own);
// Infinity is three's own "draw all of it" and does not survive JSON.
console.log(JSON.stringify({
  hidden, restored: restored === Infinity,
  drawn: geo.drawRange.count === Infinity,
}));
""")
    assert out["hidden"] == 0, "no sheet may reach the G-buffer"
    assert out["drawn"], "and every sheet is drawn in the real pass"
    assert out["restored"], (
        "restored after EVERY pass, or a raycast or a later frame "
        "inherits an empty draw range from whichever pass ran last")


def test_the_bank_thins_to_nothing_feathers_its_rim_and_hides_its_sheets():
    """The three ways this geometry betrays itself: a top edge that stops
    at a value (a lid), a footprint edge that stops at full opacity (a
    rectangle across the frame), and one constant alpha per sheet (a
    contour line across everything standing in it).  All three are fixed
    in the fragment stage — the profile is windowed to zero at uTop, the
    rim by uRadius, and the alpha is broken up by noise WITH A FLOOR at
    the ground plus a screen-space dither."""
    out = _measure("""
import { makeHeightFog } from './lib/atmosphere.js';
const m = makeHeightFog({ extent: 60, top: 4 }).children[0].material;
console.log(JSON.stringify({ vs: m.vertexShader, fs: m.fragmentShader }));
""")
    fs, vs = out["fs"], out["vs"]
    assert "smoothstep(uTop * 0.45, uTop, vH)" in fs, (
        "the height profile must reach exactly zero at uTop")
    assert "length(vPos.xz) / uRadius" in fs, "the rim must be feathered"
    assert "astraFbm2" in fs, "an unbroken layer is a slab"
    assert "vH * 0.31" in fs, (
        "the noise domain must shift with height or every sheet wears "
        "the same blotches and the stack prints as one surface")
    assert "float low = 0.55 + 0.90 * n;" in fs, (
        "a flat 1.0 at the ground is where the contour bands were: the "
        "densest sheets were the ones with no variation at all")
    assert "astraHash21(gl_FragCoord.xy)" in fs, (
        "and a dither over what the noise leaves, or the eye finds the "
        "residual step anyway")
    # The alpha is the whole effect: a fog sheet written opaque is a lid.
    assert "clamp(a, 0.0, 1.0)" in fs
    # fwidth is fragment-only and the util block ships in both stages;
    # the bank must not reach for it in the vertex shader.
    assert "fwidth" not in vs.split("void main")[1]
    for chunk in ("logdepthbuf_pars_vertex", "logdepthbuf_vertex",
                  "fog_pars_vertex", "fog_vertex"):
        assert f"#include <{chunk}>" in vs, chunk
    for chunk in ("logdepthbuf_pars_fragment", "logdepthbuf_fragment",
                  "fog_pars_fragment", "fog_fragment"):
        assert f"#include <{chunk}>" in fs, chunk


def test_the_drift_is_seeded_and_runs_off_one_tick():
    """Math.random is banned — a re-render must not reshuffle the bank —
    and an un-advanced uTime is a frozen frame that reads as a broken
    effect."""
    out = _measure("""
import { makeHeightFog } from './lib/atmosphere.js';
const off = (g) => g.children[0].material.uniforms.uOffset.value.toArray();
const a = makeHeightFog({ seed: 3 }), b = makeHeightFog({ seed: 3 });
const c = makeHeightFog({ seed: 9 });
const g = makeHeightFog({});
const u = g.children[0].material.uniforms;
const before = u.uTime.value;
const n = g.userData.tick(7.5);
console.log(JSON.stringify({
  same: JSON.stringify(off(a)) === JSON.stringify(off(b)),
  differs: JSON.stringify(off(a)) !== JSON.stringify(off(c)),
  before, after: u.uTime.value, ticked: n,
  extent: u.uRadius.value * 2, top: u.uTop.value,
  density: u.uDensity.value,
}));
""")
    assert out["same"], "same seed, same bank"
    assert out["differs"], "a different seed must move the noise"
    assert out["before"] == 0 and out["after"] == 7.5
    assert out["ticked"] == 1
    # The documented defaults, which the scene author is told to trust.
    assert out["extent"] == 60 and out["top"] == 4
    assert out["density"] == 0.75


def test_the_bank_takes_its_colour_from_the_scene_it_stands_in():
    """The port's real bug.  A bank at a baked-in daylight white
    (0xdce9f2) is in-scattered NOON light, and on the night rig it
    rendered a slab of milk: the ground under it read (69,66,64) against
    a (26,36,64) sky, the brightest thing in the frame.  The colour of
    fog is the radiance of the air, and the scene already carries it —
    `sunRig` bakes the graded sky into `scene.environment`, so the band
    from the horizon to 25 degrees up IS the answer, in every mood."""
    out = _measure("""
import * as THREE from 'three';
import { makeHeightFog } from './lib/atmosphere.js';
import { sunRig } from './lib/environment.js';
const lum = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
function scene(elevation, fogHex) {
  const s = new THREE.Scene();
  const rig = sunRig({ azimuth: 215, elevation });
  s.add(rig.sun); s.add(rig.fill);
  s.environment = rig.envTex;
  s.fog = new THREE.FogExp2(fogHex, 0.0035);
  return { s, rig };
}
const day = scene(38, 0xcfd8e6);
const night = scene(-20, 0x0b0f1a);
const u = (o) => o.children[0].material.uniforms;
const dayU = u(makeHeightFog({ scene: day.s, height: 2.2 }));
const nightU = u(makeHeightFog({ scene: night.s, height: 2.2 }));
const bare = u(makeHeightFog({ height: 2.2 }));
const forced = u(makeHeightFog({ scene: night.s, color: 0xff0000 }));
// No environment at all: the fog colour, floored by the fill.
const noEnv = new THREE.Scene();
noEnv.add(night.rig.fill.clone());
noEnv.fog = new THREE.FogExp2(0x0b0f1a, 0.0035);
const noEnvU = u(makeHeightFog({ scene: noEnv, height: 2.2 }));
console.log(JSON.stringify({
  dayLum: lum(dayU.uColor.value), nightLum: lum(nightU.uColor.value),
  bareLum: lum(bare.uColor.value),
  fogLum: lum(new THREE.Color(0x0b0f1a)),
  noEnvLum: lum(noEnvU.uColor.value),
  dayRB: dayU.uColor.value.r / dayU.uColor.value.b,
  nightRB: nightU.uColor.value.r / nightU.uColor.value.b,
  forced: forced.uColor.value.toArray(),
  // The key light: its direction, its warmth, its strength.
  daySun: dayU.uSunDir.value.toArray().map((v) => Math.round(v * 1000) / 1000),
  moon: nightU.uSunDir.value.toArray().map((v) => Math.round(v * 1000) / 1000),
  authored: night.rig.sunDir.toArray().map((v) => Math.round(v * 1000) / 1000),
  dayAmt: dayU.uSunAmt.value, nightAmt: nightU.uSunAmt.value,
  bareAmt: bare.uSunAmt.value,
  dayBeam: lum(dayU.uBeam.value), nightBeam: lum(nightU.uBeam.value),
  bareBeam: lum(bare.uBeam.value),
  sunWarm: dayU.uSunTint.value.r / dayU.uSunTint.value.b,
  ambCool: dayU.uAmbTint.value.r / dayU.uAmbTint.value.b,
  overrideAmt: u(makeHeightFog({ scene: day.s, sunAmount: 0.2 }))
      .uSunAmt.value,
  overrideDir: u(makeHeightFog({ scene: day.s, sunDir: [0, 0, 3] }))
      .uSunDir.value.toArray(),
}));
""")
    # Twenty to one between noon and midnight air, off the same bake.
    assert out["dayLum"] > 0.5, "day air is bright"
    assert 0.02 < out["nightLum"] < 0.08, (
        "night air is dark but LIT — the night mood's own fog colour "
        f"({out['fogLum']:.4f}) is set to swallow the far field and is "
        "seven times too dark to be the air in front of the camera")
    assert out["nightLum"] > out["fogLum"] * 4
    # Blue, both times, because the sky is: never the neutral white the
    # far-field fog tint carries.
    assert out["dayRB"] < 0.75 and out["nightRB"] < 0.75
    assert 0.4 < out["bareLum"] < 1.0, "no scene: the day horizon"
    # No environment to read, so the fog colour, lifted off the floor by
    # the fill rather than used raw.
    assert out["noEnvLum"] > out["fogLum"] * 3
    assert out["forced"][0] > 0.9 and out["forced"][2] < 0.05, (
        "an explicit `color` still wins outright")
    # A SET sun is a moon rig: the light that reaches the air is above
    # the horizon even when the authored sun is not.
    assert out["daySun"][1] > 0.5 and out["moon"][1] > 0.5
    assert out["authored"][1] < 0, (
        "and the authored sunDir at night points under the ground — "
        "reading the scene's own light is what gets this right")
    assert out["dayAmt"] > out["nightAmt"] > 0
    assert out["dayBeam"] > out["nightBeam"] > 0, (
        "the beam is the key light's own radiance in the air")
    assert out["bareAmt"] == 0 and out["bareBeam"] == 0, (
        "with no light to read there is no in-scatter to invent")
    assert out["sunWarm"] > 1.2, "the sun side of the bank is warm"
    assert out["ambCool"] < 0.5, "and the shadow side takes the fill"
    assert abs(out["overrideAmt"] - 0.2) < 1e-6
    assert out["overrideDir"] == [0, 0, 1], "an authored sunDir is used"


def test_the_bank_moves_with_the_light_and_up_its_own_height():
    """One flat value over the whole bank is the tell of painted fog.
    Real air is warm and bright looking through it toward the key light
    (droplets scatter hard forward), cool looking away, and brighter at
    the top of the bank than in the shadowed soil at the bottom."""
    out = _measure("""
import { makeHeightFog } from './lib/atmosphere.js';
const fs = makeHeightFog({}).children[0].material.fragmentShader;
console.log(JSON.stringify({ fs }));
""")
    fs = out["fs"]
    assert "float ct = clamp(dot(V, uSunDir), -1.0, 1.0);" in fs, (
        "the phase is a function of the VIEW ray against the light")
    assert "hgD * sqrt(hgD)" in fs, "Henyey-Greenstein, not a cosine lobe"
    assert ", 0.75, 2.6);" in fs, (
        "clamped: single-scattering HG runs to 16x forward and 0.4x "
        "back, and fog is thick enough that multiple scattering fills "
        "the backward half in")
    assert "mix(shadeC, litC, sunMix) + uBeam * phase" in fs, (
        "the beam ADDS: the sky in uColor outruns the moon twenty to "
        "one by day and two to one at night, so a multiplier tuned for "
        "one mood is invisible in the other")
    assert "mix(0.88, 1.25, smoothstep(0.0, 0.75, vH / uTop))" in fs, (
        "and the bank is lit from above, so its top is the bright face")
    assert "astraHueBreak(tint, p, 0.55, 0.30)" in fs, (
        "patch-to-patch hue spread on the SAME field the density uses, "
        "so the warm and cool blooms sit on the thick and thin air")


def test_aerial_perspective_shifts_hue_at_constant_brightness():
    """The cue is colour, not exposure.  Mixing toward the raw sky colour
    would also brighten every distant surface, which is the same lever
    FogExp2 is already pulling — the two would fight.  Tinting the
    fragment's OWN luminance with the sky's chromaticity leaves the
    dimming to fog, so the two compose."""
    out = _measure("""
import * as THREE from 'three';
import { patchAerialPerspective } from './lib/atmosphere.js';
const m = patchAerialPerspective(new THREE.MeshStandardMaterial(),
    { skyColor: new THREE.Color(0xf2c17e), start: 40, end: 600 });
const shader = {
  vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
  fragmentShader: 'void main() {\\n#include <color_fragment>\\n' +
      '#include <fog_fragment>\\n}',
  uniforms: {},
};
m.onBeforeCompile(shader);
const s = shader.uniforms.uAerialSky.value;
const raw = new THREE.Color(0xf2c17e);
const lum = (c) => c.r * 0.2126 + c.g * 0.7152 + c.b * 0.0722;
console.log(JSON.stringify({
  skyLum: lum(s),
  hueKept: Math.abs(s.r / s.b - raw.r / raw.b),
  ownLum: shader.uniforms.uAerialLum.value, rawLum: lum(raw),
  fs: shader.fragmentShader, vs: shader.vertexShader,
  fog: m.fog !== false,
}));
""")
    assert abs(out["skyLum"] - 1.0) < 1e-6, (
        "the sky uniform is normalised to unit luminance, so "
        "uAerialSky * L returns exactly the fragment's own brightness")
    assert out["hueKept"] < 1e-6, "only the brightness is normalised away"
    assert abs(out["ownLum"] - out["rawLum"]) < 1e-6, (
        "and the brightness it was normalised BY is kept, because the "
        "airlight lift needs the sky's real luminance")
    fs = out["fs"]
    # Order is the composition: albedo -> lighting -> three's fog.
    assert fs.index("<color_fragment>") < fs.index("diffuseColor.rgb = mix")
    assert fs.index("diffuseColor.rgb = mix") < fs.index("<fog_fragment>")
    assert "gl_FragColor" not in fs, (
        "writing the final colour would bypass lighting and the fog "
        "chunk — the patch belongs on the albedo")
    assert out["fog"], "the material's own fog flag is untouched"


def test_airlight_lifts_the_far_field_and_never_dims_it():
    """What pure luminance preservation cannot reach: air GLOWS, so a
    shadowed far surface loses contrast from below, not only in hue.
    Measured here on a ridge 70 m out — (67,88,100), a dark slab that
    read as a hole in the frame, against (113,136,157) with the lift.
    One-sided on purpose: pulling the other way would dim every far
    surface brighter than the sky, which is the extinction half, and
    three's fog is already doing that."""
    out = _measure("""
import * as THREE from 'three';
import { patchAerialPerspective } from './lib/atmosphere.js';
const m = patchAerialPerspective(new THREE.MeshStandardMaterial());
const soft = patchAerialPerspective(new THREE.MeshStandardMaterial(),
    { lift: 0 });
const shader = {
  vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
  fragmentShader: 'void main() {\\n#include <color_fragment>\\n}',
  uniforms: {},
};
m.onBeforeCompile(shader);
console.log(JSON.stringify({
  lift: shader.uniforms.uAerialLift.value,
  off: soft.userData.uniforms.uAerialLift.value,
  fs: shader.fragmentShader,
}));
""")
    assert 0.2 <= out["lift"] <= 0.5, "a default that lifts, not floods"
    assert out["off"] == 0, "`lift: 0` is the reference's pure hue shift"
    assert ("float aerialB = aerialL\n"
            "      + uAerialLift * max(0.0, uAerialLum - aerialL);"
            in out["fs"]), "max(0.0, ...): airlight only ever adds"


def test_distance_is_the_same_ruler_three_uses_for_fog():
    """three's fog measures view-space Z (`vFogDepth = -mvPosition.z`).
    Any other measure makes the hue shift and the fog disagree about
    where 'far' is.  And <begin_vertex> runs BEFORE instancing, so an
    InstancedMesh whose matrix is ignored gives every one of its
    hundreds of copies the distance of the mesh origin."""
    out = _measure("""
import * as THREE from 'three';
import { patchAerialPerspective } from './lib/atmosphere.js';
const m = patchAerialPerspective(new THREE.MeshStandardMaterial());
const shader = {
  vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
  fragmentShader: 'void main() {\\n#include <color_fragment>\\n}',
  uniforms: {},
};
m.onBeforeCompile(shader);
console.log(JSON.stringify({
  vs: shader.vertexShader,
  start: shader.uniforms.uAerialStart.value,
  end: shader.uniforms.uAerialEnd.value,
  k: shader.uniforms.uAerialK.value,
}));
""")
    vs = out["vs"]
    assert "vAerialDepth = -(modelViewMatrix * aerialP).z;" in vs
    assert "#ifdef USE_INSTANCING" in vs and "instanceMatrix" in vs
    for line in vs.splitlines():
        if line.strip().startswith("#"):
            assert line == line.strip(), "a directive must own its line"
    assert out["start"] == 40 and out["end"] == 600 and out["k"] == 0.8


def test_the_defaults_agree_with_the_scene_the_lib_ships_with():
    """A hue shift toward a colour the sky is not is worse than none:
    the far field would drift away from the horizon it is supposed to
    dissolve into.  With no scene to read the default sky is
    `worldShell`'s day horizon and the ramp ends at one e-fold of that
    mood's own fog density; with one, both come off the scene."""
    out = _measure("""
import * as THREE from 'three';
import { patchAerialPerspective } from './lib/atmosphere.js';
import { worldShell, sunRig } from './lib/environment.js';
const { fog } = worldShell({});
const lum = (c) => c.r * 0.2126 + c.g * 0.7152 + c.b * 0.0722;
const compile = (m) => {
  const shader = {
    vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
    fragmentShader: 'void main() {\\n#include <color_fragment>\\n}',
    uniforms: {},
  };
  m.onBeforeCompile(shader);
  return shader.uniforms;
};
const bare = compile(patchAerialPerspective(new THREE.MeshStandardMaterial()));
const s = bare.uAerialSky.value;
const h = fog.color.clone().multiplyScalar(1 / lum(fog.color));

// With a scene: the scene's own fog, and its own density.
const day = new THREE.Scene();
const rig = sunRig({ azimuth: 215, elevation: 38 });
day.add(rig.sun); day.add(rig.fill);
day.environment = rig.envTex;
day.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
const scened = compile(patchAerialPerspective(
    new THREE.MeshStandardMaterial(), { scene: day }));
// Night: the mood's fog is set to swallow the far field, so used raw it
// takes every distant surface to black with it.
const night = new THREE.Scene();
const nrig = sunRig({ azimuth: 215, elevation: -20 });
night.add(nrig.sun); night.add(nrig.fill);
night.environment = nrig.envTex;
night.fog = new THREE.FogExp2(0x0b0f1a, 0.0035);
const dark = compile(patchAerialPerspective(
    new THREE.MeshStandardMaterial(), { scene: night }));
console.log(JSON.stringify({
  dr: Math.abs(s.r - h.r), dg: Math.abs(s.g - h.g), db: Math.abs(s.b - h.b),
  efolds: bare.uAerialEnd.value * fog.density,
  start: bare.uAerialStart.value,
  scenedEnd: scened.uAerialEnd.value,
  scenedLum: scened.uAerialLum.value,
  sceneFogLum: lum(new THREE.Color(0xcfd8e6)),
  nightLum: dark.uAerialLum.value,
  nightFogLum: lum(new THREE.Color(0x0b0f1a)),
  nightRB: dark.uAerialSky.value.r / dark.uAerialSky.value.b,
}));
""")
    for k in ("dr", "dg", "db"):
        assert out[k] < 1e-6, "the default sky IS worldShell's horizon"
    assert 0.9 < out["efolds"] < 1.3, (
        "the shift must finish about where the day mood's FogExp2 has "
        "taken over (1/0.0018 m), or one cue outruns the other")
    # Nothing inside 40 m shifts: aerial perspective is a far-field cue,
    # and shifting the foreground is what makes a scene look hazy.
    assert out["start"] == 40
    assert abs(out["scenedEnd"] - 1 / 0.0035) < 1e-6, (
        "given a scene, the ramp ends at ONE e-fold of ITS fog, not of "
        "the mood the library happens to ship with")
    assert abs(out["scenedLum"] - out["sceneFogLum"]) < 1e-6, (
        "and by day the scene's own fog colour is the airlight, "
        "untouched")
    assert out["nightLum"] > out["nightFogLum"] * 3, (
        "at night it is floored by the radiance of the air, or the far "
        "field crushes to a black-blue silhouette: measured (9,23,50) "
        "under a (26,36,64) sky")
    assert out["nightRB"] < 0.6, "and what it is floored to is sky blue"


def test_one_program_serves_every_patched_material():
    """three caches programs by material type plus this key, so a key
    per material would compile the identical GLSL over and over.  The
    uniforms must still be per material, or the last call would retune
    every surface in the scene."""
    out = _measure("""
import * as THREE from 'three';
import { patchAerialPerspective } from './lib/atmosphere.js';
const a = patchAerialPerspective(new THREE.MeshStandardMaterial(),
    { start: 10, end: 200, strength: 0.4 });
const b = patchAerialPerspective(new THREE.MeshStandardMaterial(),
    { start: 80, end: 900 });
console.log(JSON.stringify({
  keyA: a.customProgramCacheKey(), keyB: b.customProgramCacheKey(),
  sharedMap: a.userData.uniforms === b.userData.uniforms,
  kA: a.userData.uniforms.uAerialK.value,
  kB: b.userData.uniforms.uAerialK.value,
  endA: a.userData.uniforms.uAerialEnd.value,
  endB: b.userData.uniforms.uAerialEnd.value,
  flagged: a.userData.astraShader === true,
}));
""")
    assert out["keyA"] == out["keyB"], "identical GLSL, one program"
    assert not out["sharedMap"], "every material keeps its own uniforms"
    assert out["kA"] == 0.4 and out["kB"] == 0.8
    assert out["endA"] == 200 and out["endB"] == 900
    assert out["flagged"], "tickShaders must be able to find it"


def test_the_environment_is_read_once_per_bake():
    """`patchAerialPerspective` is called once per material, and a scene
    with fifty materials would otherwise re-read a quarter of the
    equirect fifty times.  Cached on the TEXTURE, and never handed out:
    every caller of the scene reader mutates what it gets."""
    out = _measure("""
import * as THREE from 'three';
import { makeHeightFog } from './lib/atmosphere.js';
import { sunRig } from './lib/environment.js';
const s = new THREE.Scene();
const rig = sunRig({ azimuth: 215, elevation: 38 });
s.add(rig.sun); s.add(rig.fill);
s.environment = rig.envTex;
const first = makeHeightFog({ scene: s }).children[0]
    .material.uniforms.uColor.value.clone();
// A caller that mutates its own colour must not move anyone else's.
const mine = makeHeightFog({ scene: s }).children[0].material.uniforms.uColor;
mine.value.multiplyScalar(0.01);
const again = makeHeightFog({ scene: s }).children[0]
    .material.uniforms.uColor.value;
const t0 = performance.now();
for (let i = 0; i < 40; i++) makeHeightFog({ scene: s });
const ms = performance.now() - t0;
console.log(JSON.stringify({
  stable: Math.abs(first.r - again.r) < 1e-9
      && Math.abs(first.b - again.b) < 1e-9,
  msPer: ms / 40,
}));
""")
    assert out["stable"], "the cached radiance is cloned on the way out"
    # 2 300 texels read and sorted per call is ~1 ms; the cache makes the
    # whole build cheaper than one geometry.
    assert out["msPer"] < 5


_SCENE = """
import * as THREE from 'three';
import { makeHeightFog, patchAerialPerspective } from './lib/atmosphere.js';

export const BOUNDS = { min: [-80, 0, -80], max: [80, 20, 80] };

export function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  scene.add(new THREE.HemisphereLight(0x9db8e8, 0x8a7f6a, 1.4));
  const sun = new THREE.DirectionalLight(0xfff0d8, 5.4);
  sun.position.set(-90, 86, -63);
  sun.castShadow = true;
  scene.add(sun);
  const g = new THREE.Group();
  const heightAt = (x, z) => Math.sin(x * 0.06) * 1.2 + Math.cos(z * 0.05);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(160, 160, 40, 40),
      new THREE.MeshStandardMaterial({ color: 0x6b7a5a }));
  ground.rotation.x = -Math.PI / 2;
  patchAerialPerspective(ground.material, { scene, start: 30, end: 500 });
  g.add(ground);

  // Instanced: the one case where <begin_vertex> alone would give every
  // copy the distance of the mesh origin.
  const posts = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.2, 0.2, 3, 8),
      new THREE.MeshStandardMaterial({ color: 0x8a7f6a }), 12);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 12; i++) {
    m4.makeTranslation(i * 4 - 22, 1.5, -6);
    posts.setMatrixAt(i, m4);
  }
  patchAerialPerspective(posts.material, { scene, strength: 0.6 });
  g.add(posts);

  // Both ways in: the bank reads the scene, and an authored sunDir
  // (as a plain array, which is what a scene author has to hand)
  // overrides it.
  const fog = makeHeightFog({ scene, extent: 60, top: 4, heightAt, seed: 4 });
  const authored = makeHeightFog({ extent: 40, height: 2, seed: 5,
      sunDir: [0.4, 0.7, -0.5], sunAmount: 0.9 });
  authored.position.set(60, 0, 0);
  g.add(fog, authored);
  scene.add(g);
  return {
    scene,
    cameras: [{ name: 'a', position: [18, 6, 24], lookAt: [0, 2, 0] }],
    update(t) { fog.userData.tick(t); authored.userData.tick(t); },
  };
}
"""


def test_both_exports_compile_on_the_gpu():
    """The one thing no source assertion can answer.  Runs the same
    wrapper the authoring agent runs, on the renderer flags the
    framework renders with: the custom attribute, the height varying,
    the instancing ifdef and the patched standard material all have to
    survive three's assembly."""
    code, out = compile_scene(_SCENE, ("atmosphere.js",))
    assert code == 0, out
    assert "ERROR" not in out, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] and report["errors"] == [], report
    # Two banks (one ShaderMaterial each) and two patched built-ins.
    assert report["compile"]["custom_materials"] >= 4, report
    assert report["compile"]["gpu"], report
    # A transparent layer without the fog chunks keeps full contrast
    # while the world recedes — the sticker look, on the one object in
    # the scene whose whole job is to recede.
    assert "no fog chunks" not in out
