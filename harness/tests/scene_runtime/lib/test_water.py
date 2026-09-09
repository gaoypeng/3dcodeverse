"""Water ships as the addon shader, a procedural normal map, and a REGRADE.

Ported 2026-09-01 from the scene_multifile_graphics reference test.  Its
geometry half survives intact: npm three ships no waternormals.jpg, so
``makeWaterNormals()`` IS the waves, and it has to tile, stay wavy and
stay deterministic or a fix round re-renders a different ocean.

The shading half is OURS.  The addon's own fragment tail hardcodes
``rf0 = 0.3`` and adds a flat ``vec3(0.1)`` veil to the mirror, i.e. it
has no Fresnel: measured on this harness's showcase pool (fx/out/water/
before), the surface came back at mean_lum 0.732 looked steeply INTO and
0.732 at a grazing angle — one pale value, the same milk at every angle,
and at night a slab of mean_lum 0.40 lying in a frame whose mean was
0.196.  ``_regrade`` splices a physical Fresnel, a scene-lit body with a
turbidity hue field, an additive glitter track and a distortion ceiling
over that tail; ``_readScene`` then points the whole thing at the key
light the scene actually has, at the first render.  The tests below pin
that as hard as they pin the wave map.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("water.js",)


# A stub renderer is enough to drive `onBeforeRender`: everything the
# addon touches on it is a setter or a no-op, and the scene sniff runs
# BEFORE the mirror pass.  This is what lets the auto-wiring be tested
# off the GPU, in the same probe as everything else.
_STUB_RENDERER = """
const stubRenderer = () => ({
  extensions: { has: () => true },
  capabilities: { isWebGL2: true },
  xr: { enabled: false },
  shadowMap: { autoUpdate: true },
  autoClear: true,
  getRenderTarget: () => null,
  setRenderTarget() {},
  state: { buffers: { depth: { setMask() {} } }, viewport() {} },
  clear() {}, render() {},
});
"""

_PROBE = _STUB_RENDERER + """
import * as THREE from 'three';
import { makeOcean, makeWaterNormals } from './lib/water.js';

// The normal map: tiling, wavy, deterministic.
const size = 64;
const t1 = makeWaterNormals(size), t2 = makeWaterNormals(size);
const d = t1.image.data;
let identical = true;
for (let i = 0; i < d.length; i++) {
  if (d[i] !== t2.image.data[i]) { identical = false; break; }
}
// Per-channel stats + seam vs interior adjacency deltas (R channel).
let rSum = 0, r2Sum = 0, bMin = 255;
let maxSeam = 0, maxInterior = 0;
const px = (x, y) => d[(y * size + x) * 4];
for (let y = 0; y < size; y++) {
  for (let x = 0; x < size; x++) {
    const r = px(x, y);
    rSum += r; r2Sum += r * r;
    bMin = Math.min(bMin, d[(y * size + x) * 4 + 2]);
    if (x > 0) maxInterior = Math.max(maxInterior, Math.abs(r - px(x - 1, y)));
    if (y > 0) maxInterior = Math.max(maxInterior, Math.abs(r - px(x, y - 1)));
  }
  maxSeam = Math.max(maxSeam, Math.abs(px(0, y) - px(size - 1, y)));
}
for (let x = 0; x < size; x++) {
  maxSeam = Math.max(maxSeam, Math.abs(px(x, 0) - px(x, size - 1)));
}
const n = size * size;
const rStd = Math.sqrt(r2Sum / n - (rSum / n) * (rSum / n));

// The ocean mesh: graded defaults must reach the shader.
const o = makeOcean(120, 80, {});
const u = o.material.uniforms;
o.geometry.computeBoundingBox();
const bb = o.geometry.boundingBox;
const t0 = u.time.value;
o.userData.update(2.5);
const tAfter = u.time.value;

// sunDir passthrough: what the caller aims is what the shader gets.
const dir = new THREE.Vector3(3, 4, 0);
const o2 = makeOcean(10, 10, { sunDir: dir, rttSize: 128 });

// Wave tiling vs plane extent: pool, pond, harbor, explicit override.
const oPool = makeOcean(18, 14, { rttSize: 64 });
const oPond = makeOcean(150, 150, { rttSize: 64 });
const nPond = makeWaterNormals(32, { broadband: false });
const nWide = makeWaterNormals(32, { broadband: true });
let spectrumDiffers = false;
for (let i = 0; i < nPond.image.data.length; i++) {
  if (nPond.image.data[i] !== nWide.image.data[i]) {
    spectrumDiffers = true; break;
  }
}
const oHarbor = makeOcean(2800, 2800, { rttSize: 64 });
const oForced = makeOcean(2800, 2800, { rttSize: 64, size: 5 });

// The regrade, and the addon tail it replaced.
const fs = o.material.fragmentShader;
const oLegacy = makeOcean(20, 20, { rttSize: 64, legacyShader: true });

console.log(JSON.stringify({
  identical,
  wraps: t1.wrapS === THREE.RepeatWrapping
      && t1.wrapT === THREE.RepeatWrapping,
  mipmapped: t1.generateMipmaps === true
      && t1.minFilter === THREE.LinearMipmapLinearFilter,
  aniso: t1.anisotropy,
  rStd, bMin, maxSeam, maxInterior,
  rotX: o.rotation.x,
  time: t0, timeAfter: tAfter,
  distortion: u.distortionScale.value,
  waterColor: u.waterColor.value.getHex(),
  sunLen: u.sunDirection.value.length(),
  rtt: u.mirrorSampler.value.image.width,
  spanX: bb.max.x - bb.min.x, spanY: bb.max.y - bb.min.y,
  sun2Custom: [o2.material.uniforms.sunDirection.value.x,
               o2.material.uniforms.sunDirection.value.y],
  rtt2: o2.material.uniforms.mirrorSampler.value.image.width,
  sizePool: oPool.material.uniforms.size.value,
  sizePond: oPond.material.uniforms.size.value,
  sizeHarbor: oHarbor.material.uniforms.size.value,
  sizeForced: oForced.material.uniforms.size.value,
  turbPool: oPool.material.uniforms.turbScale.value,
  turbHarbor: oHarbor.material.uniforms.turbScale.value,
  spectrumDiffers,
  poolUsesNarrow: oPool.material.uniforms.normalSampler.value
      .image.data[0] === makeWaterNormals(256,
          { broadband: false }).image.data[0],
  rf0: u.rf0.value,
  glitterScale: u.glitterScale.value,
  hasVeil: fs.indexOf('vec3( 0.1 ) + reflectionSample') >= 0,
  hasFixedRf0: fs.indexOf('float rf0 = 0.3') >= 0,
  declaresRf0: fs.indexOf('uniform float rf0') >= 0,
  keepsToneMap: fs.indexOf('tonemapping_fragment') >= 0
      && fs.indexOf('colorspace_fragment') >= 0,
  gatedGlitter: fs.indexOf('reflectionSample * specularLight') >= 0,
  legacyVeil: oLegacy.material.fragmentShader.indexOf(
      'vec3( 0.1 ) + reflectionSample') >= 0,
  legacyExtras: oLegacy.material.uniforms.rf0 !== undefined,
}));
"""

_SNIFF = _STUB_RENDERER + """
import * as THREE from 'three';
import { makeOcean } from './lib/water.js';

// A night rig: the sun is BELOW the horizon, the moon is what casts, and
// the hemisphere is a dim blue.  Water built before the scene existed
// must end up lit by the moon, not by the noon default it shipped with.
const scene = new THREE.Scene();
const moon = new THREE.DirectionalLight(0xb5c7e8, 2.2);
// A moved TARGET: the light aims from its position at this, so a sniff
// that read `position` alone would hand the glitter a direction the
// shadows do not use.
moon.position.set(-40, 60, 30);
moon.target.position.set(0, 20, 0);
scene.add(moon);
scene.add(moon.target);
const stray = new THREE.DirectionalLight(0xff0000, 0.2);
stray.position.set(10, 1, 0);
scene.add(stray);
const hemi = new THREE.HemisphereLight(0x3f5378, 0x2a2a33, 1.0);
scene.add(hemi);
scene.fog = new THREE.FogExp2(0x0b0f1a, 0.0035);

const auto = makeOcean(40, 40, { rttSize: 64 });
const before = auto.material.uniforms.sunDirection.value.clone();
const pinnedDir = new THREE.Vector3(1, 0, 0);
const pinned = makeOcean(40, 40, {
  rttSize: 64, sunDir: pinnedDir, sunColor: 0x00ff00, ambient: 0x101010 });

const r = stubRenderer(), cam = new THREE.PerspectiveCamera();
scene.updateMatrixWorld(true);   // what a renderer does before the frame
cam.position.set(0, 10, 30); cam.lookAt(0, 0, 0); cam.updateMatrixWorld();
auto.updateMatrixWorld(); pinned.updateMatrixWorld();
auto.onBeforeRender(r, scene, cam);
pinned.onBeforeRender(r, scene, cam);
// a second frame must not re-sniff (and must not throw)
auto.onBeforeRender(r, scene, cam);

const ua = auto.material.uniforms, up = pinned.material.uniforms;
const moonDir = moon.position.clone().sub(moon.target.position).normalize();
console.log(JSON.stringify({
  beforeY: before.y,
  autoDir: [ua.sunDirection.value.x, ua.sunDirection.value.y,
            ua.sunDirection.value.z],
  moonDir: [moonDir.x, moonDir.y, moonDir.z],
  autoSun: [ua.sunColor.value.r, ua.sunColor.value.g, ua.sunColor.value.b],
  moonColor: [moon.color.r, moon.color.g, moon.color.b],
  autoKey: [ua.keyColor.value.r, ua.keyColor.value.g, ua.keyColor.value.b],
  autoAmb: [ua.ambientColor.value.r, ua.ambientColor.value.g,
            ua.ambientColor.value.b],
  hemiCol: [hemi.color.r, hemi.color.g, hemi.color.b],
  mirrorType: ua.mirrorSampler.value.type,
  halfFloat: THREE.HalfFloatType,
  pinnedDir: [up.sunDirection.value.x, up.sunDirection.value.y,
              up.sunDirection.value.z],
  pinnedSun: [up.sunColor.value.r, up.sunColor.value.g, up.sunColor.value.b],
  pinnedAmb: [up.ambientColor.value.r, up.ambientColor.value.g,
              up.ambientColor.value.b],
}));
"""

# One ocean, one lit bank, real fog: the whole `lights: true` +
# shadowmap + fog permutation of the regraded program, on the GPU.
_SCENE = """
import * as THREE from 'three';
import { makeOcean } from './lib/water.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 8, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  const sun = new THREE.DirectionalLight(0xfff0d8, 5.4);
  sun.position.set(-40, 40, -28);
  sun.castShadow = true;
  scene.add(sun);
  scene.add(new THREE.HemisphereLight(0x9db8e8, 0x8a7f6a, 1.4));
  const kerb = new THREE.Mesh(
    new THREE.BoxGeometry(24, 1.2, 2),
    new THREE.MeshStandardMaterial({ color: 0x8d8578, roughness: 0.86 }));
  kerb.position.set(0, 0.6, -9);
  kerb.castShadow = kerb.receiveShadow = true;
  scene.add(kerb);
  const sea = makeOcean(18, 14, { sunDir: sun.position.clone().normalize() });
  sea.position.y = 0.95;
  scene.add(sea);
  const cameras = [
    { name: 'graze', position: [5, 2.2, 7], lookAt: [0, 1.4, 0], fov: 40 },
  ];
  return { scene, cameras, update(t) { sea.userData.update(t); } };
}
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch, shared by every test that reads it."""
    return measure(_PROBE, _LIBS)


@pytest.fixture(scope="module")
def sniff() -> dict:
    """One node launch of the first-render scene sniff."""
    return measure(_SNIFF, _LIBS)


def test_normal_map_is_wavy_tiling_and_deterministic(probe) -> None:
    """No image assets ship with npm three, so this synthesized map IS
    the waves: a flat map gives a mirror-still ocean, a seam prints a
    grid across it, and non-determinism breaks fix-round re-renders."""
    m = probe
    assert m["identical"], "two builds of the normal map differ"
    assert m["wraps"], "not RepeatWrapping"
    assert m["mipmapped"], (
        "wave normals unfiltered — a DataTexture defaults to Nearest "
        "with no mipmaps and dithers across any water seen at distance")
    assert m["aniso"] >= 8, m["aniso"]
    assert m["rStd"] > 8, f"normal map nearly flat: rStd {m['rStd']}"
    assert m["bMin"] > 127, f"a normal points below horizon: {m['bMin']}"
    assert m["maxSeam"] <= m["maxInterior"] * 1.5 + 2, (
        f"wrap seam ({m['maxSeam']}) jumps harder than the interior "
        f"({m['maxInterior']}) — the ocean would show tile lines")


def test_ocean_ships_the_graded_defaults(probe) -> None:
    """Graded defaults (rtt 512, distortion 2.8, colour 0x0e3f5c, time
    frozen mid-wave, plane flat) must reach the shader — dropping one
    silently re-runs the experiment on every scene.

    rtt was 256 until the reference's 2026-08-04 gallery audit found the
    mirror tearing into stair-stepped blocks in 9 frames across six
    scenes; it is one extra scene pass and still the cheapest thing in
    the frame.
    """
    m = probe
    assert abs(m["rotX"] + 1.5707963) < 1e-6, f"not flat: {m['rotX']}"
    assert 6.5 < m["time"] < 7.6, f"time not frozen mid-wave: {m['time']}"
    assert abs(m["distortion"] - 2.8) < 1e-9, m["distortion"]
    assert m["waterColor"] == 0x0E3F5C, hex(m["waterColor"])
    assert m["rtt"] == 512, f"rtt default drifted: {m['rtt']}"
    assert m["rtt2"] == 128, "rttSize override ignored"
    assert (m["spanX"], m["spanY"]) == (120, 80), "w/d not honored"


def test_wave_tiling_scales_with_the_plane(probe) -> None:
    """One wave period is ~103/size metres, so a size graded on a pond
    tiles hundreds of times across a harbor and moires into radial
    stripes.  Measured by the reference on a 2800 m harbor at golden
    hour: 1.2 unusable, 0.70 striped, 0.44 clean.

    The law used to be capped at 6, which froze it for every plane under
    206 m — an 18 m pool got ONE wave period across the whole basin, an
    ocean swell in a bathtub.  Measured here on the showcase pool
    (fx/out/water/sz*): ripple local contrast 1.22 at size 6, 1.73 at 14,
    2.51 at 30, 3.11 at 68 — and 68 speckles the far band into per-pixel
    sparkle at a 1024 px grazing close-up.  Cap 30, law unchanged.
    """
    m = probe
    assert m["sizePool"] == 30, (
        f"an 18 m pool got wave size {m['sizePool']} — the cap is what "
        "keeps a basin from carrying one 17 m swell")
    assert abs(m["sizePond"] - 8.24) < 0.05, (
        f"a 150 m pond got {m['sizePond']}, not the law's extent/12")
    assert abs(m["sizeHarbor"] - 0.441) < 0.01, (
        f"2800 m harbor wave size {m['sizeHarbor']} — measured clean at "
        "0.44, striped from 0.70 up")
    assert m["sizeForced"] == 5, "an explicit size must win over the law"


def test_the_turbidity_field_is_sized_in_metres_not_texels(probe) -> None:
    """The body's hue field is a slow read of the wave map, and its
    period has to be METRES of the plane.  Pinned to a constant it was
    the same texel everywhere on a small pool and the whole basin came
    out one colour — the exact flatness the term exists to break."""
    m = probe
    assert abs(m["turbPool"] - 8.0) < 1e-6, (
        f"an 18 m pool's turbidity period is {m['turbPool']} m — under "
        "the 8 m floor the colour field competes with the waves")
    assert abs(m["turbHarbor"] - 1120.0) < 1e-6, m["turbHarbor"]


def test_wide_water_gets_the_broadband_wave_spectrum(probe) -> None:
    """Eight pure tones read as corduroy banding at a grazing angle —
    it is the periodicity of the SET that shows, and no wave size hides
    it.  Spreading 28 directions at 1/f^2 kills it but calms the
    surface, so only water wide enough to be seen edge-on pays for it."""
    m = probe
    assert m["spectrumDiffers"], (
        "broadband:true returned the same normal map — the option is "
        "not reaching the wave table")
    assert m["poolUsesNarrow"], "a pool paid for the broadband spectrum"
    # a 380 m storm sea is wide enough to be seen edge-on: two lighthouse runs (2026-09-09)
    # read the eight-tone set as "a checkerboard of white crescents" — the threshold is 300 m
    from codeverse.config import get_settings
    src = (get_settings().runtime_js_dir().parent / "codeverse" / "languages" / "scene_threejs" / "starter" / "src" / "lib" / "water.js").read_text()
    assert "BROADBAND_EXTENT_M = 300" in src and "_extent > BROADBAND_EXTENT_M" in src


def test_update_drives_the_wave_phase_and_sundir_reaches_the_shader(
        probe) -> None:
    """tick animates water ONLY through userData.update(t); and the
    glitter track must sit where the caller's key light is, or sun and
    specular disagree on screen."""
    m = probe
    assert abs(m["timeAfter"] - (m["time"] + 2.5)) < 1e-9, (
        f"update(t) did not advance the phase: {m['time']} -> "
        f"{m['timeAfter']}")
    assert abs(m["sunLen"] - 1) < 1e-6, "default sunDir not normalized"
    x, y = m["sun2Custom"]
    assert abs(x - 0.6) < 1e-6 and abs(y - 0.8) < 1e-6, (
        f"passed sunDir was not normalized through: {m['sun2Custom']}")


def test_the_regrade_replaces_the_addon_tail(probe) -> None:
    """The addon's tail is what makes stock Water read as milk: a
    hardcoded rf0 = 0.3 (30% mirror looked straight down into), a flat
    +0.1 veil over the reflection, and a specular multiplied BY the
    reflection so a sun track cannot appear on dark water.  Measured on
    the showcase pool, that tail gave mean_lum 0.732 steeply AND 0.732
    at grazing — one value at every angle, which is no Fresnel at all.
    """
    m = probe
    assert not m["hasFixedRf0"], "the addon's rf0 = 0.3 survived"
    assert not m["hasVeil"], "the flat +0.1 milk veil survived"
    assert not m["gatedGlitter"], (
        "the specular is still gated behind the mirror sample")
    assert m["declaresRf0"], "rf0 is no longer a uniform"
    assert abs(m["rf0"] - 0.02) < 1e-9, (
        f"F0 {m['rf0']} is not dielectric water")
    assert m["glitterScale"] == 1


def test_the_regrade_keeps_our_pipelines_fragment_tail(probe) -> None:
    """We have no post chain: ACES and the sRGB encode happen in the
    fragment tail, so a splice that dropped three's two closing chunks
    would render the water dark next to every built-in material."""
    assert probe["keepsToneMap"], "tonemapping/colorspace chunks lost"


def test_legacy_shader_opts_all_the_way_out(probe) -> None:
    """An escape hatch has to be a real one: `legacyShader` must ship the
    addon's own tail, uniforms and all, for a scene graded against it."""
    m = probe
    assert m["legacyVeil"], "legacyShader did not restore the addon tail"
    assert not m["legacyExtras"], (
        "legacyShader still declared the regrade's uniforms")


def test_first_render_points_the_water_at_the_scenes_own_key(sniff) -> None:
    """Water is built before the scene exists, so its light is a guess
    until the first frame.  Left as the shipped guess, a night pool
    glitters for a noon sun in a colour nothing else in the frame is lit
    by — and it picks the BRIGHTEST directional, not the first one.

    The probe's moon aims at a MOVED target, so a sniff that read the
    light's `position` alone fails here.
    """
    m = sniff
    assert m["beforeY"] > 0.5, "the shipped default is not the day axis"
    for got, want in zip(m["autoDir"], m["moonDir"], strict=True):
        assert abs(got - want) < 1e-6, (
            f"key direction {m['autoDir']} is not the moon's "
            f"{m['moonDir']}")
    # hue only: the glitter lobes are graded values, not radiometry, so
    # they must be tinted by the key and never scaled by its watts.
    peak = max(m["moonColor"])
    for got, want in zip(m["autoSun"], m["moonColor"], strict=True):
        assert abs(got - want / peak) < 1e-6, (
            f"glitter colour {m['autoSun']} is not the moon's hue")
    assert max(m["autoSun"]) == pytest.approx(1.0, abs=1e-6)


def test_first_render_reads_irradiance_the_way_a_standard_material_does(
        sniff) -> None:
    """The body is a Lambert term, so it has to sit on the same 1/PI
    scale as every MeshStandardMaterial around it — otherwise the water
    is lit by a different sun than the bank it touches."""
    m = sniff
    inv_pi = 1.0 / 3.141592653589793
    for got, want in zip(m["autoKey"], m["moonColor"], strict=True):
        assert abs(got - want * 2.2 * inv_pi) < 1e-5, (
            f"key irradiance {m['autoKey']} is not colour*intensity/PI")
    for got, want in zip(m["autoAmb"], m["hemiCol"], strict=True):
        assert abs(got - want * 1.0 * inv_pi) < 1e-5, (
            f"ambient {m['autoAmb']} is not the hemisphere's sky/PI")


def test_the_mirror_target_goes_half_float(sniff) -> None:
    """The mirror target is 8-bit LINEAR — three turns tone mapping and
    the sRGB encode off when rendering to a non-sRGB target — so the
    bottom of its range quantises to a handful of steps and a night sky
    at ~0.002 linear reflects as two flat bands.  Half-float costs two
    bytes a texel on ONE 512 px target."""
    m = sniff
    assert m["mirrorType"] == m["halfFloat"], (
        f"mirror target type {m['mirrorType']} is not HalfFloat")


def test_pinned_options_are_never_overwritten_by_the_sniff(sniff) -> None:
    """A caller who names a sun direction, a sun colour or an ambient has
    made a decision; the first frame must not quietly undo it."""
    m = sniff
    assert m["pinnedDir"] == [1, 0, 0], m["pinnedDir"]
    assert m["pinnedSun"] == [0, 1, 0], m["pinnedSun"]
    r, g, b = m["pinnedAmb"]
    assert r == g == b and 0 < r < 0.02, m["pinnedAmb"]


def test_every_regraded_program_compiles_on_the_gpu() -> None:
    """The regrade is a string splice into an addon shader: it either
    compiles with `lights: true`, the shadow-mask chunks and fog, or the
    pool renders as a black hole.  Nothing but a real compile proves it.
    """
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
