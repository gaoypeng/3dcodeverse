"""mirror.js: the one real planar mirror, and the cheap route for everything
else — a baked equirect city that a whole facade can reflect for free.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_mirror_lib.py).  Their budget, standoff, target-shape and
retarget scenarios are kept as they stood; their `logarithmicDepthBuffer:
true` renderer contract is retargeted (ours has it OFF by default, but
`--log-depth` is still a host option and the addon's four chunks cost
nothing, so the "never hand the Reflector a replacement shader" law stays);
and their render probe is dropped in favour of the showcase harness, which
renders this module through OUR host at fx/out/mirror/{before,after}.

THE PORT'S OWN LAWS — each one a frame rendered on our host, looked at, and
measured, and each one a defect the reference shipped:

1. THE BAKE HAS TO SHIP sRGB BYTES.  Every colour in `bakeSkylineEnvironment`
   is mixed in the LINEAR working space a THREE.Color holds, and the
   DataTexture it writes is flagged SRGBColorSpace — so it is DECODED as
   sRGB when sampled.  The reference wrote the linear numbers straight into
   the bytes and ran the transfer backwards: the intended zenith 0x5d8fd6
   shipped as (28,70,171) and came back as linear (0.011,0.061,0.415).  A
   sixth of the light, and blue pulled far ahead of red, because the error
   is a power curve and not a scale.  On our host every facade `glazeFacade`
   touched rendered as a flat navy slab: mean_lum 0.145, saturation 0.72,
   modal_frac 0.153 — the "black hole" the module's own docstring says the
   cheap route exists to prevent.  After the fix, 0.355 / 0.334 / 0.046,
   with the reflected skyline legible in the frame.

2. A CURTAIN WALL'S MIRROR LIVES IN THE COATING, NOT THE METAL.  A metal's
   F0 IS its colour, and this glass tint is linear (0.031,0.058,0.088), so
   the reference's metalness 0.35 multiplied a third of the reflection by a
   near-black blue.  An untinted `clearcoat` is what real reflective glazing
   actually is: the sky arrives in the sky's own colour, with its own
   fresnel.  Measured together with law 1 on the glazed block: pane region
   p05 0.063 -> 0.256.

3. A REFLECTED CITY IS NOT ONE SWATCH, AND IT KNOWS WHAT TIME IT IS.  The
   skyline was one hue scaled by one random number per building, unlit
   unless asked, and lit by a hardcoded noon palette even when handed a
   sun that had set.  It now takes hue and saturation per building, shades
   facades by the key light's own azimuth, hazes toward the horizon, and
   flips to the night mood — moon where lib/environment.js puts the moon,
   windows on — the moment `sunDir` goes below the horizon.
"""

from __future__ import annotations

import math

import pytest

from tests.scene_runtime.lib._probe import LIB_DIR, NODE_MODULES, measure

pytestmark = pytest.mark.node

_LIBS = ("mirror.js", "wetground.js", "building.js", "materials.js",
         "noise.js")

_PROBE = """
import * as THREE from 'three';
import { makeMirror, envMirrorMaterial, bakeSkylineEnvironment,
         glassFacadeMaterial, glazeFacade, reflectionBudget,
         setReflectionBudget } from './lib/mirror.js';
import { makeMirrorFloor } from './lib/wetground.js';
import { block } from './lib/building.js';
import { mulberry32 } from './lib/noise.js';

// 1) The one real mirror, and what the Reflector was handed.
const scene = new THREE.Scene();
const m1 = makeMirror(4, 3, { scene });
scene.add(m1);
const surf = m1.userData.surface;
const rttTex = surf.material.uniforms.tDiffuse.value;
const rttSamples = surf.getRenderTarget().samples;
const tint = surf.material.uniforms.color.value;
const back = m1.getObjectByName('MirrorBack');
const frame = m1.getObjectByName('MirrorFrame');

// 2) A SECOND surface in the same scene must not get its own RTT.
const m2 = makeMirror(4, 3, { scene });

// 3) A mirror floor built by another module charges the same budget.
setReflectionBudget(1);
const wet = new THREE.Scene();
wet.add(makeMirrorFloor(20, 20));
const m3 = makeMirror(4, 3, { scene: wet });
const wetBudget = reflectionBudget(wet);

// 4) A fresh budget hands out a real mirror again; 'env' never takes one.
setReflectionBudget(1);
const forcedEnv = makeMirror(4, 3, { mode: 'env' });
const m4 = makeMirror(4, 3, {});

// 5) The baked environment: deterministic, filtered, structured, sRGB.
const e1 = bakeSkylineEnvironment({ seed: 3 });
const e2 = bakeSkylineEnvironment({ seed: 3 });
const flat = bakeSkylineEnvironment({ seed: 3, skyline: false });
const warm = bakeSkylineEnvironment({ seed: 3, lit: 0.6 });
// A sun straight DOWN leaves the upper hemisphere free of glow, so the top
// row is the requested zenith and nothing else: the transfer function is
// then the only thing between the hex asked for and the bytes shipped.
const clean = bakeSkylineEnvironment({
  seed: 3, skyline: false, sunDir: new THREE.Vector3(0, -1, 0),
  zenith: 0x5d8fd6, horizon: 0xdce9f2, ground: 0x8a7f6a });
const d1 = e1.image.data, d2 = e2.image.data;
let sameBake = d1.length === d2.length;
for (let i = 0; sameBake && i < d1.length; i++) {
  if (d1[i] !== d2[i]) sameBake = false;
}
const W = e1.image.width, H = e1.image.height;
function rowMean(data, r0, r1) {
  let s = 0, n = 0;
  for (let y = r0; y < r1; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      s += (data[i] + data[i + 1] + data[i + 2]) / 3;
      n++;
    }
  }
  return s / n;
}
function rowMin(data, r0, r1) {
  let m = 255;
  for (let y = r0; y < r1; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const l = (data[i] + data[i + 1] + data[i + 2]) / 3;
      if (l < m) m = l;
    }
  }
  return m;
}
// Circular spread of hue, in degrees, over the pixels with colour in them.
function hueStdDeg(data, r0, r1) {
  let cx = 0, cy = 0, n = 0;
  for (let y = r0; y < r1; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const M = Math.max(r, g, b), mn = Math.min(r, g, b), c = M - mn;
      if (M <= 0 || c / M <= 0.04) continue;
      let h;
      if (M === r) h = ((g - b) / c + 6) % 6;
      else if (M === g) h = (b - r) / c + 2;
      else h = (r - g) / c + 4;
      const a = h * 60 * Math.PI / 180;
      cx += Math.cos(a); cy += Math.sin(a); n++;
    }
  }
  if (n < 64) return 0;
  const R = Math.min(1, Math.hypot(cx, cy) / n);
  return Math.sqrt(-2 * Math.log(Math.max(R, 1e-9))) * 180 / Math.PI;
}
// Rows just above the horizon (v = 0.5) are where the neighbours are.
const bandTop = (H >> 1) + 2, bandBot = (H >> 1) + 22;
let warmPx = 0;
for (let y = bandTop; y < bandBot; y++) {
  for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 4;
    const wd = warm.image.data;
    if (wd[i] > 110 && wd[i] - wd[i + 2] > 30) warmPx++;
  }
}
const topRow = (H - 1) * W * 4;
const cd = clean.image.data;

// 6) The same bake once the sun has set: azimuth 215, elevation -20, the
//    vector sunRig() reports as `sunDir` on a night scene.
const nEl = -20 * Math.PI / 180, nAz = 215 * Math.PI / 180;
const nightSun = new THREE.Vector3(
    Math.cos(nEl) * Math.cos(nAz), Math.sin(nEl), Math.cos(nEl) * Math.sin(nAz));
const night = bakeSkylineEnvironment({ seed: 3, sunDir: nightSun });
const nd = night.image.data;
// Where is the brightest pixel of the night sky? The moon, and it has to be
// ABOVE the horizon: a set sun left as the glow source puts it underground.
let bestY = -1, bestV = -1;
for (let y = 0; y < H; y++) {
  for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 4;
    const l = (nd[i] + nd[i + 1] + nd[i + 2]) / 3;
    if (l > bestV) { bestV = l; bestY = y; }
  }
}
let nightWarmPx = 0;
for (let y = bandTop; y < bandBot; y++) {
  for (let x = 0; x < W; x++) {
    const i = (y * W + x) * 4;
    if (nd[i] > 90 && nd[i] - nd[i + 2] > 20) nightWarmPx++;
  }
}

// 7) A facade from lib/building.js, retargeted.
// block() splits its panes on `rand()`, and its default rand is a
// constant 0.5 that always lands in bin 0 — a seeded one is what makes
// the second batch exist at all.
const b = block({ w: 16, d: 14, h: 30, style: 'glass',
                  rand: mulberry32(5) });
const glazing = b.getObjectByName('Glazing');
const glazing2 = b.getObjectByName('Glazing2');
const reveals = b.getObjectByName('Reveals');
const revealsBefore = reveals.material.uuid;
glazeFacade(b, { envMap: e1 });
const gm = glazing.material;
// The same tower, glazed with an explicit material: one value everywhere.
const b2 = block({ w: 16, d: 14, h: 30, style: 'glass',
                   rand: mulberry32(5) });
glazeFacade(b2, { material: glassFacadeMaterial({ envMap: e1 }) });

const chrome = envMirrorMaterial({ envMap: e1 });
const gTrans = glassFacadeMaterial({ opacity: 0.6 });

console.log(JSON.stringify({
  mode1: m1.userData.mode, mode2: m2.userData.mode,
  mode3: m3.userData.mode, mode4: m4.userData.mode,
  modeForcedEnv: forcedEnv.userData.mode,
  wetUsed: wetBudget.used, wetLeft: wetBudget.left,
  tintLinear: tint.r,
  rttW: rttTex.image.width, rttH: rttTex.image.height,
  rttSamples,
  standoff: surf.position.z, backZ: back.position.z,
  surfCast: surf.castShadow, surfReceive: surf.receiveShadow,
  forward: m1.userData.forward,
  backLum: back.material.color.r * 0.2126 + back.material.color.g * 0.7152
      + back.material.color.b * 0.0722,
  frameLum: frame.material.color.r * 0.2126
      + frame.material.color.g * 0.7152 + frame.material.color.b * 0.0722,
  frameWarm: frame.material.color.r - frame.material.color.b,
  sameBake,
  envW: W, envH: H,
  equirect: e1.mapping === THREE.EquirectangularReflectionMapping,
  srgb: e1.colorSpace === THREE.SRGBColorSpace,
  linearFiltered: e1.minFilter === THREE.LinearFilter
      && e1.magFilter === THREE.LinearFilter,
  zenith: rowMean(e1.image.data, H - 6, H - 1),
  bandSkyline: rowMean(e1.image.data, bandTop, bandBot),
  bandOpenSky: rowMean(flat.image.data, bandTop, bandBot),
  bandMin: rowMin(e1.image.data, bandTop, bandBot),
  bandHueStd: hueStdDeg(e1.image.data, bandTop, bandBot),
  openHueStd: hueStdDeg(flat.image.data, bandTop, bandBot),
  warmPx,
  cleanTop: [cd[topRow], cd[topRow + 1], cd[topRow + 2]],
  nightZenith: rowMean(nd, H - 6, H - 1),
  dayZenith: rowMean(e1.image.data, H - 6, H - 1),
  nightBandMin: rowMin(nd, bandTop, bandBot),
  nightMoonAboveHorizon: bestY > (H >> 1),
  nightWarmPx,
  glazed: b.userData.glazed,
  glazeIsPhysical: !!gm.isMeshPhysicalMaterial,
  glazeEnv: gm.envMap === e1,
  glazeIor: gm.ior, glazeTransparent: gm.transparent,
  glazeClearcoat: gm.clearcoat, glazeMetalness: gm.metalness,
  batchesDiffer: glazing.material.uuid !== glazing2.material.uuid,
  batchEnvBoth: glazing2.material.envMap === e1,
  forcedOneMaterial: b2.getObjectByName('Glazing').material.uuid
      === b2.getObjectByName('Glazing2').material.uuid,
  revealsUntouched: reveals.material.uuid === revealsBefore,
  chromeMetal: chrome.metalness, chromeRough: chrome.roughness,
  transFlag: gTrans.transparent, transOpacity: gTrans.opacity,
}));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return measure(_PROBE, _LIBS)


def test_the_scene_gets_one_real_mirror_and_no_more(probe):
    """The budget is the module's reason to exist: a second RTT surface
    renders INSIDE the first one's pass, so it cannot be left to the
    caller's discipline. A mirror floor from wetground.js counts too --
    the budget is per scene, not per module."""
    m = probe
    assert m["mode1"] == "rtt", "the first mirror must be a real one"
    assert m["mode2"] == "env", (
        "a second mirror in the same scene took its own render target"
    )
    assert m["mode3"] == "env", (
        "a wetground makeMirrorFloor in the scene did not charge the "
        "budget — two RTT surfaces ship whenever the modules differ"
    )
    assert (m["wetUsed"], m["wetLeft"]) == (1, 0), m
    assert m["mode4"] == "rtt", "a fresh budget hands out a real mirror"
    assert m["modeForcedEnv"] == "env", "mode:'env' still took an RTT"


def test_the_reflection_is_untinted_and_frame_shaped(probe):
    """Two Reflector defaults are wrong for a mirror. Its 0x7f7f7f
    OVERLAY-blends to ~0.42x on darks (dim, dirty glass) — overlay's
    identity is linear 0.5. And its square target oversamples vertically at
    1.8x the pixel cost, because the target holds a SCREEN-space image, not
    a picture of the mirror.

    Overlay's identity holds on OUR host for a second reason worth keeping
    written down: the addon renders the reflection to a render target, where
    three forces NoToneMapping and a linear output, and applies the tone map
    and the sRGB encode in the mirror's OWN fragment tail instead. So the
    values `color` blends against are linear radiance that can exceed 1, and
    blendOverlay at 0.5 is the identity there too."""
    m = probe
    assert abs(m["tintLinear"] - 0.5) < 0.02, (
        f"mirror tint is linear {m['tintLinear']:.3f}, not the overlay "
        "identity 0.5 — the reflection is being darkened or blown out"
    )
    assert abs(m["rttW"] / m["rttH"] - 16 / 9) < 0.02, (
        f"reflection target {m['rttW']}x{m['rttH']} is not frame-shaped"
    )
    assert m["rttW"] >= 640, f"reflection target too coarse: {m['rttW']}"
    assert m["rttSamples"] == 0, (
        f"MSAA {m['rttSamples']} on the reflection target: measured 28 "
        "ms/frame on SwiftShader for a difference invisible at 1:1 — "
        "the reflection is minified onto the mirror and the frame is "
        "antialiased anyway"
    )


def test_nothing_sits_within_a_centimetre_behind_the_glass(probe):
    """Measured: geometry within 1 cm behind the glass leaks past the
    Reflector's oblique clip plane and fills the mirror with a dark slab
    from 20 degrees off-axis (2 cm is clean to 55 degrees). The standoff is
    what buys that clearance, so it is not decoration."""
    m = probe
    assert m["standoff"] >= 0.02, (
        f"glass stands only {m['standoff']} m off the mount plane"
    )
    assert m["standoff"] - m["backZ"] >= 0.02, (
        f"backing plate is {m['standoff'] - m['backZ']:.3f} m behind "
        "the glass — it will leak into the reflection"
    )
    assert not m["surfCast"] and not m["surfReceive"], (
        "an RTT plane that casts shadows shadows the scene it resamples"
    )
    assert m["forward"] == "+Z", m["forward"]


def test_the_frame_is_the_only_lit_thing_next_to_the_reflection(probe):
    """PORT LAW. A mirror is full of the scene's own brightness, so the two
    surfaces around it decide whether it reads as an object or as a hole cut
    in the wall — and the reference set both at the floor of a legal albedo
    (frame 0x4a3a2a, linear 0.068; plate 0x2a2724, linear 0.024). Rendered
    on our host at exposure 1.0 the frame came back as a black border with
    no grain in it at all. Walnut keeps it a dark frame and gives it a hue:
    the close view's frame band went p05 0.170 -> 0.217 and hue spread
    0.843 -> 0.928."""
    m = probe
    assert m["frameLum"] > 0.05, (
        f"frame albedo is linear {m['frameLum']:.3f} — under ACES at "
        "exposure 1.0 that is a black rectangle, not a frame"
    )
    assert m["frameWarm"] > 0.03, (
        "the frame has no hue in it; wood is warmer than it is blue"
    )
    assert m["backLum"] > 0.025, (
        f"backing plate is linear {m['backLum']:.3f} — it has nowhere "
        "left to fall in shade"
    )


def test_the_bake_ships_srgb_bytes_and_not_linear_ones(probe):
    """PORT LAW 1, and the worst defect in the module. Every colour is mixed
    in the LINEAR working space a THREE.Color holds, and the DataTexture is
    flagged SRGBColorSpace, so it is DECODED as sRGB on sample. Writing the
    linear numbers straight out runs the transfer backwards: 0x5d8fd6 left
    as (28,70,171) instead of (93,143,214) and came back as a sixth of the
    light with blue far ahead of red, because the error is a power curve and
    not a scale. On our host that is what made every glazed facade a flat
    navy slab."""
    r, g, b = probe["cleanTop"]
    for got, want, ch in ((r, 0x5d, "r"), (g, 0x8f, "g"), (b, 0xd6, "b")):
        assert abs(got - want) <= 3, (
            f"zenith {ch} shipped as byte {got}, asked for {want} — the "
            f"bake wrote {ch} in the linear working space into a texture "
            "that is decoded as sRGB"
        )


def test_the_baked_environment_carries_a_city_not_a_gradient(probe):
    """The cheap route only beats a plain sky gradient if the bake has
    neighbours in it: a dark band of towers above the horizon is what a glass
    facade actually mirrors. Deterministic, or a fix round re-rolls the
    skyline; LINEAR filtered, or a DataTexture's Nearest default prints the
    bake's texel grid across every facade.

    PORT LAW 3 adds the two floors under it. The band must not bottom out
    near black — aerial perspective is what a city actually does to its own
    contrast, and without it the darkest facades were the ones a mirror
    turned into a hole. And it must carry more than one hue: the reference
    scaled a single wall colour by one random number per building, which is
    the "stamped asset" verdict in swatch form."""
    m = probe
    assert m["sameBake"], "two bakes of one seed differ"
    assert m["equirect"] and m["srgb"], m
    assert m["linearFiltered"], (
        "environment left on the DataTexture Nearest default"
    )
    assert (m["envW"], m["envH"]) == (512, 256), m
    assert m["bandSkyline"] < 0.7 * m["bandOpenSky"], (
        f"the horizon band reads {m['bandSkyline']:.1f} with a skyline "
        f"vs {m['bandOpenSky']:.1f} without — no neighbours were baked"
    )
    assert m["zenith"] > m["bandSkyline"], "the sky is darker than the city"
    assert m["warmPx"] > 200, (
        f"lit: 0.6 lit only {m['warmPx']} window pixels"
    )
    assert m["bandMin"] >= 40, (
        f"darkest neighbour in the band is byte {m['bandMin']:.0f} — a "
        "facade that mirrors it renders as a hole"
    )
    assert m["bandHueStd"] > 2.5 * max(m["openHueStd"], 0.8), (
        f"the skyline's hue spread is {m['bandHueStd']:.2f} deg against "
        f"{m['openHueStd']:.2f} for bare sky — every neighbour was baked "
        "from one swatch"
    )


def test_the_bake_knows_what_time_it_is(probe):
    """PORT LAW 3. `sunDir` already carries the hour, so a bake that keeps
    noon hexes while the scene hands it a set sun mirrors a noon sky off
    every pane at midnight — the one hardcoded-light-colour failure this
    module could still commit. Below the horizon the palette flips to the
    night mood the rest of the library uses, the glow moves to where
    lib/environment.js puts the MOON (opposite the sun, 30-55 deg up, not
    underground), and the reflected city has its lights on without being
    asked. The lower half stays off the floor on purpose: a night horizon
    carries a city's skyglow and a night street carries its own lamps, and
    neither is a fraction of the zenith."""
    m = probe
    assert m["nightZenith"] < 0.45 * m["dayZenith"], (
        f"night zenith {m['nightZenith']:.1f} vs day "
        f"{m['dayZenith']:.1f} — the palette did not follow sunDir down"
    )
    assert m["nightMoonAboveHorizon"], (
        "the brightest pixel of the night sky is below the horizon — the "
        "glow is still sitting on the set sun instead of the moon"
    )
    assert m["nightWarmPx"] > 150, (
        f"only {m['nightWarmPx']} warm window pixels after dark — a city "
        "with every light out is a ruin, not a night"
    )
    assert m["nightBandMin"] >= 18, (
        f"darkest night neighbour is byte {m['nightBandMin']:.0f}"
    )


def test_a_building_facade_is_retargeted_without_losing_its_lit_windows(
    probe,
):
    """The whole point of the cheap route: one call makes a lib/building.js
    tower mirror the sky. Lit windows live on the Reveals mesh and must keep
    glowing — glazing every mesh would put out the lights that make a dusk
    city read as inhabited.

    PORT LAW 2 and its corollary. The mirror a curtain wall shows lives in
    an UNTINTED coating, not in metalness: a metal's F0 is its colour, and
    this tint is linear (0.031,0.058,0.088), so the reference multiplied a
    third of every reflection by a near-black blue and got the flat navy
    hole its own docstring warns about. And building.js already splits its
    panes into two batches precisely because "real glazing is never one
    value" — retargeting both to a single material threw that away."""
    m = probe
    assert m["glazed"] >= 2, "no Glazing/Pane mesh was retargeted"
    assert m["glazeIsPhysical"] and m["glazeEnv"], m
    assert m["glazeIor"] > 1.5, (
        f"ior {m['glazeIor']:.2f}: uncoated glass reflects ~4% head-on "
        "and renders as a black hole"
    )
    assert m["glazeClearcoat"] >= 0.5, (
        f"clearcoat {m['glazeClearcoat']} — without an untinted coat the "
        "sky arrives on the glass multiplied by the glass's own near-black"
    )
    assert m["glazeMetalness"] <= 0.25, (
        f"metalness {m['glazeMetalness']} tints and dims that much of the "
        "reflection with the pane's own colour"
    )
    assert not m["glazeTransparent"], (
        "opaque by default — transparent glazing stops writing depth"
    )
    assert m["batchesDiffer"] and m["batchEnvBoth"], (
        "both pane batches got one material: building.js splits Glazing "
        "and Glazing2 to keep a facade off one flat value, and glazeFacade "
        "collapsed the split"
    )
    assert m["forcedOneMaterial"], (
        "opts.material must be applied as-is — no variants behind the "
        "caller's back"
    )
    assert m["revealsUntouched"], "glazeFacade put out the lit windows"
    assert m["chromeMetal"] == 1 and m["chromeRough"] > 0, m
    assert m["transFlag"] and abs(m["transOpacity"] - 0.6) < 1e-9, m


def test_the_reflection_shader_is_the_addons_and_never_a_replacement():
    """Retargeted, not dropped. Our renderer.js runs
    `logarithmicDepthBuffer` OFF by default, but `--log-depth` is still a
    host option, and a custom ShaderMaterial without the logdepthbuf chunks
    is erased by any opaque geometry behind it under that flag. The addon
    Reflector carries all four; this module must never hand it a
    replacement shader, which is also why the glass tint below is a
    material parameter and not a patched fragment."""
    src = (LIB_DIR / "mirror.js").read_text(encoding="utf-8")
    for banned in ("fragmentShader", "vertexShader", "onBeforeCompile",
                   "ShaderMaterial"):
        assert banned not in src, (
            f"mirror.js touches {banned} — a custom reflection shader "
            "must include logdepthbuf_pars_vertex / logdepthbuf_vertex "
            "/ logdepthbuf_pars_fragment / logdepthbuf_fragment"
        )
    addon = (NODE_MODULES / "three" / "examples" / "jsm" / "objects"
             / "Reflector.js")
    if not addon.is_file():
        pytest.skip("no three.js addon install to read the Reflector from")
    text = addon.read_text(encoding="utf-8")
    for chunk in ("logdepthbuf_pars_vertex", "logdepthbuf_vertex",
                  "logdepthbuf_pars_fragment", "logdepthbuf_fragment"):
        assert f"#include <{chunk}>" in text, (
            f"the shipped Reflector lost <{chunk}> — every mirror will "
            "vanish behind opaque geometry under --log-depth"
        )


def test_the_module_stays_dom_free_and_seeded():
    """Asset code runs in a headless page with no canvas and must re-render
    identically every round: Math.random and any DOM API are banned outright
    (mulberry32 from noise.js is the one PRNG)."""
    src = (LIB_DIR / "mirror.js").read_text(encoding="utf-8")
    for banned in ("Math.random", "document.", "createElement",
                   "new Image", "canvas"):
        assert banned not in src, f"mirror.js uses {banned}"


def test_the_transfer_function_is_the_real_srgb_curve():
    """A guard on the fix itself, cheap enough to keep. The encode has to be
    the piecewise sRGB OETF, not a 1/2.2 power approximation: the two differ
    by up to 4 bytes in the darks, which is exactly where the reflected city
    lives."""
    src = (LIB_DIR / "mirror.js").read_text(encoding="utf-8")
    assert "0.0031308" in src and "1 / 2.4" in src, (
        "mirror.js is not encoding with the piecewise sRGB transfer"
    )
    # and the curve it names is the right one, at the two ends and the knee
    for lin, want in ((0.0, 0.0), (0.0031308, 0.04045), (1.0, 1.0)):
        got = (lin * 12.92 if lin <= 0.0031308
               else 1.055 * math.pow(lin, 1 / 2.4) - 0.055)
        assert abs(got - want) < 1e-3, (lin, got, want)
