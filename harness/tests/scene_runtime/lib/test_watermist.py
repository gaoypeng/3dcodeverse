"""watermist.js: the air over moving water, and the drops it throws.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_watermist_lib.py).  Their laws are kept as they stood — mist has
to read as AIR and spray as WATER IN FLIGHT, and both fail the same way: the
mist becomes a stack of visible cards and the spray a static puff of dots.
Many faint overlapping cards no wider than the bank is deep, a launch solved
from gravity rather than animated by hand, one seed one field.  Their GPU
compile runs through `_probe.compile_scene`, which stages the fixture as a
workspace `src/scene.js` — and this module needs a real scene anyway,
because both shaders read `fogColor` behind `#ifdef USE_FOG` and that
branch only exists in a fogged one.

THE PORT'S OWN LAWS — each one a frame rendered on our host under
fx/out/watermist/, looked at, and measured against a control render of the
same weir with the mist group removed (fx/out/watermist/nomist):

1. MIST IS NOT A COLOUR, IT IS AN ALBEDO TIMES WHAT REACHES IT.  Both
   materials emitted a fixed pale blue-grey.  In daylight that is nearly
   right by luck; at night it made the bank a GLOWING white slab over a
   black weir and the droplets white confetti (close view mean_lum 0.197
   against a 0.124 base scene).  `uColor` is now multiplied by the scene's
   own `fogColor` — which IS the skylight at ground level — plus a small
   floor, so the same call reads pale at noon and a dim blue-grey at night
   with nothing said by the caller: 0.130 close, dark_frac 0.10, nothing
   blown.  A scene with no fog keeps the old look through the white
   fallback the `#ifdef` leaves behind.

2. IT SCATTERS THE SUN FORWARD.  Nothing in the module knew where the light
   was.  The lobe is Henyey-Greenstein (g = 0.55) and not a power of the
   cosine, because the power form only fires within ~30 degrees of the sun:
   with the showcase's own rig (mu = +0.64 mid, +0.68 close) `pow(mu, 3)`
   moved the veil 2% against a control with the sun mirrored across the
   scene, which is no coupling at all.  The lobe moves it 7.7% brighter and
   20% warmer (R-B 2.82 against 2.36) at the same angles.

3. NO STRAIGHT EDGE, NO LID.  The card envelope faded late (0.62 of the
   half-height), so on a camera near the top of the bank every card's upper
   rim landed in one screen band — visible as horizontal ribbons right
   across the night frame, which is the one failure this effect's own
   header says must never happen.  The fade starts at 0.16 now, and the
   per-card alpha constant went 9 -> 16 to keep `density` meaning the same
   thickness (veil contribution over the wall 5.3/255 against the old
   5.9).  The top edge's steepest row-to-row step fell 18.7 -> 15.3.

4. STRUCTURE HAS TO BE METRES ACROSS TO SURVIVE THE RAY.  A view ray
   crosses ~85 cards spread over metres of xz, so the integral averages any
   pattern shorter than itself down to its mean: the wisp scale lives
   inside one card and vanishes from the sum, and the first render came out
   a flat-topped slab.  A second, coarse octave of thickness carries most
   of the swing now — along-bank strength variation 0.203 -> 0.260.

5. DITHER PER CARD, NOT PER FRAGMENT.  A ramp this smooth over this many
   pixels bands in 8 bits and a dozen cards stack the SAME ramp.  A flat
   0.004 of alpha put a coarse speckle across the whole bank, because a
   dozen independent draws sum as a random walk; the amplitude is divided
   by the root of the stack depth.
"""

from __future__ import annotations

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("shader.js", "watermist.js")

# The scene our check_shaders.mjs boots.  Fog, because both shaders read
# `fogColor` under `#ifdef USE_FOG` and that branch is only compiled when
# the scene has some; a camera, because a scene with none is reported as
# not booted and never reaches the compile stage at all.
_SCENE = """
import * as THREE from 'three';
import { makeWaterMist, makeSpray } from './lib/watermist.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 20, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const mist = makeWaterMist({ extent: 8, height: 2, density: 0.6,
      sunDir: [-0.65, 0.62, -0.45], seed: 5 });
  const drops = makeSpray({ origin: [0, 0, 0], radius: 0.7,
      sunDir: [-0.65, 0.62, -0.45], seed: 3 });
  scene.add(mist);
  scene.add(drops);
  return {
    scene,
    cameras: [{ name: 'a', position: [6, 2, 8], lookAt: [0, 1, 0],
                fov: 45 }],
    update(t) { mist.userData.tick(t); drops.userData.tick(t); },
  };
}
"""


def test_both_effects_compile_on_the_real_renderer():
    """Two custom shaders, both billboarded, both transparent, both reading
    the scene's fog colour out of a preprocessor branch.  The fog and depth
    chunks decide whether they are drawn at all and whether they recede with
    the scene; only the GPU can say."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "OK every program compiled" in out, out
    # A WARN here is the fog trap or the override-pass trap, either of
    # which ships an effect that looks wrong rather than one that errors.
    assert "WARN" not in out, out


def test_billboards_keep_position_at_the_origin():
    """A pass that redraws with an override material ignores custom vertex
    shaders, so a billboard field that keeps its quad in `position` burns a
    black rectangle at the world origin."""
    out = measure("""
import { makeWaterMist, makeSpray } from './lib/watermist.js';
const mist = makeWaterMist({ extent: 10, height: 2, seed: 4 })
    .getObjectByName('MistCards');
const drops = makeSpray({ radius: 0.6, rate: 90, seed: 7 })
    .getObjectByName('SprayDroplets');
const report = (m) => {
  const g = m.geometry;
  return {
    posAllZero: Array.from(g.attributes.position.array)
        .every((v) => v === 0),
    hasCorner: !!g.attributes.aCorner,
    instances: g.instanceCount,
  };
};
console.log(JSON.stringify({
  mist: report(mist),
  spray: report(drops),
  mistPerCard: mist.geometry.attributes.aPuff.count,
  sprayPerDrop: drops.geometry.attributes.aVel.count,
}));
""", _LIBS)
    for part in ("mist", "spray"):
        assert out[part]["posAllZero"], out
        assert out[part]["hasCorner"], out
    assert out["mist"]["instances"] == out["mistPerCard"]
    assert out["spray"]["instances"] == out["sprayPerDrop"]


def test_the_stated_bounding_sphere_covers_both_fields():
    """`position` is all zeros, so the sphere is the only thing three can
    cull by: state it too small and the whole field disappears the moment
    the origin leaves frame."""
    out = measure("""
import { makeWaterMist, makeSpray } from './lib/watermist.js';

const mist = makeWaterMist({ extent: 12, height: 2.2, seed: 4 })
    .getObjectByName('MistCards');
const mg = mist.geometry;
const puff = mg.attributes.aPuff.array;
const card = mg.attributes.aCard.array;
const size = mist.material.uniforms.uSize.value;
let needMist = 0;
for (let i = 0; i < mg.attributes.aPuff.count; i++) {
  const c = Math.hypot(puff[i * 4], puff[i * 4 + 3], puff[i * 4 + 1]);
  // The card turns to face the camera, so it sweeps its own diagonal.
  const half = 0.5 * Math.hypot(size.x * card[i * 2],
                                size.y * card[i * 2 + 1]);
  needMist = Math.max(needMist, c + half);
}

const spray = makeSpray({ origin: [3, 1, -2], radius: 0.8, speed: 5,
    size: 0.06, rate: 100, seed: 7 });
const drops = spray.getObjectByName('SprayDroplets');
const dg = drops.geometry;
const vel = dg.attributes.aVel.array;
const ph = dg.attributes.aPhase.array;
let needSpray = 0;
for (let i = 0; i < dg.attributes.aVel.count; i++) {
  const flight = (2 * vel[i * 3 + 1]) / 9.81;
  for (let k = 0; k <= 16; k++) {
    const p = spray.userData.sample(i, (k / 16) * flight);
    needSpray = Math.max(needSpray, p.length() + 0.06 * ph[i * 2 + 1]);
  }
}
console.log(JSON.stringify({
  mistRadius: mg.boundingSphere.radius,
  mistNeed: needMist,
  mistCentre: mg.boundingSphere.center.toArray(),
  sprayRadius: dg.boundingSphere.radius,
  sprayNeed: needSpray,
  sprayGroupAt: spray.position.toArray(),
}));
""", _LIBS)
    # Local, because the group carries the placement: a sphere centred
    # anywhere else is culled against the wrong point.
    assert out["mistCentre"] == [0, 0, 0]
    assert out["sprayGroupAt"] == [3, 1, -2]
    assert out["mistRadius"] >= out["mistNeed"], out
    assert out["sprayRadius"] >= out["sprayNeed"], out
    # And not a made-up huge number, which would keep a field that has
    # long left the frame in every draw call.
    assert out["mistRadius"] <= out["mistNeed"] * 1.5, out
    assert out["sprayRadius"] <= out["sprayNeed"] * 2.5, out


def test_spray_droplets_fly_a_ballistic_arc():
    """The one property that separates spray from a puff of dust: a droplet
    is THROWN, so its height rises, stops and falls, and the curve is
    gravity's, not an animator's ease."""
    out = measure("""
import { makeSpray } from './lib/watermist.js';
const g = makeSpray({ radius: 0.5, speed: 4.5, rate: 80, seed: 11 });
const drops = g.getObjectByName('SprayDroplets');
const vel = drops.geometry.attributes.aVel.array;
const ph = drops.geometry.attributes.aPhase.array;
const N = 24;
const arcs = [];
for (const i of [0, 5, 17]) {
  const flight = (2 * vel[i * 3 + 1]) / 9.81;
  // Start each droplet at its own launch, so one whole arc is sampled.
  const t0 = (1 - ph[i * 2]) * flight;
  const ys = [], xs = [];
  for (let k = 0; k <= N; k++) {
    const p = g.userData.sample(i, t0 + (k / N) * flight * 0.999);
    ys.push(p.y);
    xs.push(Math.hypot(p.x, p.z));
  }
  let rises = 0, falls = 0, peak = 0;
  for (let k = 1; k <= N; k++) {
    if (ys[k] > ys[k - 1]) rises++; else falls++;
    if (ys[k] > ys[peak]) peak = k;
  }
  // Second difference of a free fall is -g*dt^2, constant.
  const dt = flight / N;
  const dd = [];
  for (let k = 1; k < N; k++) {
    dd.push((ys[k + 1] - 2 * ys[k] + ys[k - 1]) / (dt * dt));
  }
  arcs.push({
    rises, falls, peakAt: peak / N,
    apex: ys[peak] - ys[0],
    endY: ys[N] - ys[0],
    gMin: Math.min(...dd), gMax: Math.max(...dd),
    outward: xs[N] - xs[0],
  });
}
console.log(JSON.stringify({ arcs }));
""", _LIBS)
    for arc in out["arcs"]:
        assert arc["rises"] > 0 and arc["falls"] > 0, arc
        # Up then down, once: the peak sits at mid-flight.
        assert 0.40 <= arc["peakAt"] <= 0.60, arc
        assert arc["apex"] > 0.1, arc
        # Back to where it was launched by the end of its own flight.
        assert abs(arc["endY"]) < arc["apex"] * 0.06, arc
        # Gravity, not an ease: the curvature is constant at -g.
        assert -10.0 < arc["gMin"] <= arc["gMax"] < -9.6, arc
        # Thrown out of the impact, not straight up.
        assert arc["outward"] > 0.05, arc


def test_the_shader_flies_the_same_arc_the_cpu_mirror_does():
    """`userData.sample` is only worth having if it is the same ballistics
    the vertex shader runs; the constant and the closed form tie them
    together."""
    out = measure("""
import { makeSpray } from './lib/watermist.js';
const m = makeSpray({ seed: 2 })
    .getObjectByName('SprayDroplets').material;
const vs = m.vertexShader;
console.log(JSON.stringify({
  g: m.uniforms.uG.value,
  flight: vs.includes('float flight = 2.0 * aVel.y / uG;'),
  drop: vs.includes('c.y += aVel.y * age - 0.5 * uG * age * age;'),
  relaunch: vs.includes('fract(aPhase.x + uTime / flight)'),
}));
""", _LIBS)
    assert out["g"] == 9.81
    assert out["flight"] and out["drop"] and out["relaunch"], out


def test_no_single_card_can_be_seen_in_the_mist():
    """A card is only invisible while it is faint and while several of them
    are stacked along every ray.  One card wide enough to cover the bank
    paints its own noise raw, and that ribbon IS the card."""
    out = measure("""
import { makeWaterMist } from './lib/watermist.js';
const probe = (o) => {
  const mesh = makeWaterMist(o).getObjectByName('MistCards');
  const u = mesh.material.uniforms;
  const n = mesh.geometry.instanceCount;
  return {
    alpha: u.uAlpha.value,
    cardW: u.uSize.value.x,
    height: u.uTop.value,
    dither: u.uDither.value,
    // Cards a horizontal ray across the bank meets.
    hits: (n * u.uSize.value.x) / o.extent,
    count: n,
  };
};
console.log(JSON.stringify({
  small: probe({ extent: 8, height: 2, density: 0.6 }),
  wide: probe({ extent: 40, height: 2.5, density: 0.6 }),
  shallow: probe({ extent: 14, height: 0.8, density: 0.9 }),
}));
""", _LIBS)
    for name, p in out.items():
        assert p["alpha"] <= 0.6, (name, p)
        assert p["hits"] >= 8, (name, p)
        assert p["cardW"] <= max(1.0, p["height"] * 1.2) + 1e-6, (name, p)
        # LAW 5.  The dither is drawn once per CARD and the cards stack, so
        # a flat amplitude sums as a random walk of sqrt(hits) times it —
        # 0.004 flat was a visible speckle across the whole bank at 85 deep.
        # What must stay constant is the amplitude the SUM carries.
        assert abs(p["dither"] * (p["hits"] ** 0.5) - 0.010) < 1e-6, (name, p)


def test_the_mist_lies_along_the_current_and_follows_the_water():
    """This is the MOVING-water bank, not `makeHeightFog`: the drift sets
    the direction the wisps lie in and the speed they travel, and
    `heightAt` puts the foot of the bank on the water, not on y = 0."""
    out = measure("""
import { makeWaterMist } from './lib/watermist.js';
const heightAt = (x, z) => 0.4 + Math.sin(x * 0.3) * 0.25 - z * 0.05;
const g = makeWaterMist({ extent: 10, height: 2, drift: [0, 0.6],
    heightAt, seed: 6 });
const mesh = g.getObjectByName('MistCards');
const u = mesh.material.uniforms;
const p = mesh.geometry.attributes.aPuff.array;
let baseErr = 0, aboveBase = 0, n = mesh.geometry.attributes.aPuff.count;
for (let i = 0; i < n; i++) {
  const want = heightAt(p[i * 4], p[i * 4 + 1]);
  baseErr = Math.max(baseErr, Math.abs(p[i * 4 + 2] - want));
  if (p[i * 4 + 3] >= p[i * 4 + 2] - 1e-6) aboveBase++;
}
const still = makeWaterMist({ drift: 0 }).getObjectByName('MistCards');
console.log(JSON.stringify({
  flow: u.uFlow.value.toArray(),
  speed: u.uSpeed.value,
  baseErr, aboveBase, n,
  stillSpeed: still.material.uniforms.uSpeed.value,
  name: g.name,
  hasTick: typeof g.userData.tick === 'function',
}));
""", _LIBS)
    assert out["flow"] == [0, 1] and abs(out["speed"] - 0.6) < 1e-6
    assert out["baseErr"] < 1e-6, out
    assert out["aboveBase"] == out["n"], out
    assert out["stillSpeed"] == 0
    assert out["name"] == "WaterMist" and out["hasTick"]


def test_one_seed_gives_one_field():
    """A re-run that reshuffles the mist or the droplets is a re-run nobody
    can compare a render against."""
    out = measure("""
import { makeWaterMist, makeSpray } from './lib/watermist.js';
const mistOf = (s) => Array.from(makeWaterMist({ extent: 8, seed: s })
    .getObjectByName('MistCards').geometry.attributes.aPuff.array);
const sprayOf = (s) => Array.from(makeSpray({ rate: 60, seed: s })
    .getObjectByName('SprayDroplets').geometry.attributes.aVel.array);
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
console.log(JSON.stringify({
  mistSame: eq(mistOf(5), mistOf(5)),
  mistDiffers: !eq(mistOf(5), mistOf(9)),
  spraySame: eq(sprayOf(3), sprayOf(3)),
  sprayDiffers: !eq(sprayOf(3), sprayOf(8)),
}));
""", _LIBS)
    assert all(out.values()), out


def test_tick_advances_both_materials():
    out = measure("""
import { makeWaterMist, makeSpray } from './lib/watermist.js';
const mist = makeWaterMist({ extent: 6 });
const spray = makeSpray({ rate: 40 });
mist.userData.tick(2.5);
spray.userData.tick(2.5);
const times = [];
for (const g of [mist, spray]) {
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (m && m.uniforms && m.uniforms.uTime) {
        times.push([m.name, m.uniforms.uTime.value]);
      }
    }
  });
}
console.log(JSON.stringify({ times }));
""", _LIBS)
    assert len(out["times"]) == 2, out
    assert all(v == 2.5 for _, v in out["times"]), out
    assert sorted(n for n, _ in out["times"]) == [
        "SprayDroplets", "WaterMistCards"]


def test_neither_effect_paints_its_own_colour():
    """LAW 1.  Both emitted a fixed pale blue-grey, which made the bank a
    glowing white slab and the droplets white confetti over a black weir at
    night.  What a scattering medium shows is its albedo times what reaches
    it, and `fogColor` — the ONE light the renderer hands a raw
    ShaderMaterial — is the skylight at ground level."""
    out = measure("""
import { makeWaterMist, makeSpray } from './lib/watermist.js';
const mist = makeWaterMist({ extent: 8 })
    .getObjectByName('MistCards').material;
const drops = makeSpray({ rate: 60 })
    .getObjectByName('SprayDroplets').material;
const reads = (m) => {
  const fs = m.fragmentShader;
  // The read has to sit inside the guard: `fogColor` is only DECLARED
  // under USE_FOG, so an unguarded one fails to compile in a fogless
  // scene — and the fallback is what keeps that scene looking as before.
  const at = fs.indexOf('sky = fogColor;');
  const guard = fs.lastIndexOf('#ifdef USE_FOG', at);
  const end = fs.indexOf('#endif', at);
  return {
    fogGuarded: at > 0 && guard > 0 && end > at,
    fallbackWhite: fs.includes('vec3 sky = vec3(1.0);'),
    // Multiplied, never emitted.
    litByColor: fs.includes('uColor * (sky * uSkyGain'),
    fogEnabled: m.fog === true && !!m.uniforms.fogColor,
  };
};
console.log(JSON.stringify({ mist: reads(mist), spray: reads(drops) }));
""", _LIBS)
    for part in ("mist", "spray"):
        assert out[part]["fogGuarded"], (part, out)
        assert out[part]["fallbackWhite"], (part, out)
        assert out[part]["litByColor"], (part, out)
        assert out[part]["fogEnabled"], (part, out)


def test_the_sun_below_the_horizon_delivers_nothing():
    """LAW 1 again, from the other side.  A sun gain that ignores elevation
    is what put a lit bank into a night frame; `sunRig` keeps reporting the
    authored sun after it has set, so the module has to read the height."""
    out = measure("""
import { makeWaterMist, makeSpray } from './lib/watermist.js';
const gain = (dir) => [
  makeWaterMist({ sunDir: dir }).getObjectByName('MistCards')
      .material.uniforms.uSunGain.value,
  makeSpray({ rate: 60, sunDir: dir }).getObjectByName('SprayDroplets')
      .material.uniforms.uSunGain.value,
];
const dirOf = (o) => o.getObjectByName('MistCards')
    .material.uniforms.uSunDir.value.toArray();
console.log(JSON.stringify({
  high: gain([-0.65, 0.62, -0.45]),
  grazing: gain([-0.8, 0.06, -0.6]),
  set: gain([-0.6, -0.34, -0.72]),
  none: gain(undefined),
  noneDir: dirOf(makeWaterMist({})),
  unit: dirOf(makeWaterMist({ sunDir: [0, 4, 3] })),
}));
""", _LIBS)
    assert out["set"] == [0, 0], out
    assert all(g > 0.5 for g in out["high"]), out
    # A grazing sun delivers a fraction, not all of it and not none.
    for a, b in zip(out["grazing"], out["high"], strict=True):
        assert 0 < a < b, out
    # No rig to hand: top lit at full strength, which is the old look.
    assert out["none"] == out["high"], out
    assert out["noneDir"] == [0, 1, 0], out
    assert abs(sum(v * v for v in out["unit"]) - 1.0) < 1e-6, out


def test_the_forward_lobe_is_broad_enough_to_matter():
    """LAW 2.  `pow(mu, 3)` only fires within ~30 degrees of the sun: at the
    showcase rig's own angles (mu = +0.64 and +0.68) it moved the veil 2%
    against a sun-mirrored control, which is no coupling at all.  The
    Henyey-Greenstein lobe is what makes an ordinary framing read."""
    out = measure("""
import { makeWaterMist } from './lib/watermist.js';
const fs = makeWaterMist({ extent: 8 })
    .getObjectByName('MistCards').material.fragmentShader;
// The same lobe the fragment runs, evaluated here: 0.2929 / den^1.5 with
// den = 1 + g*g - 2*g*mu at g = 0.55, clamped the way the shader clamps.
const hg = (mu) => Math.min(2.0, Math.max(0.14,
    0.2929 / Math.max(Math.pow(1.3025 - 1.10 * mu, 1.5), 1e-3)));
console.log(JSON.stringify({
  hasLobe: fs.includes('float den = 1.3025 - 1.10 * mu;'),
  usesView: fs.includes('normalize(vP - cameraPosition)'),
  back: hg(-0.85), side: hg(0.0), showcase: hg(0.64), sun: hg(1.0),
}));
""", _LIBS)
    assert out["hasLobe"] and out["usesView"], out
    # Monotone from back-scatter to the sun, and normalised so side-scatter
    # sits where the old power form's floor was.
    assert out["back"] < out["side"] < out["showcase"] < out["sun"], out
    assert abs(out["side"] - 0.20) < 0.01, out
    # The point of the lobe: at the angle a camera actually stands at, the
    # sun term is already worth twice its side-scatter value.
    assert out["showcase"] > 2.0 * out["side"], out
    # And bounded, so looking straight into the sun through a thick bank
    # is a bright veil rather than a white hole.
    assert out["sun"] <= 2.0, out


def test_the_bank_has_no_lid_and_no_straight_edge():
    """LAWS 3 and 4.  A late card fade landed every card's upper rim in one
    screen band — horizontal ribbons across the night frame — and a single
    noise scale shorter than the view ray integrated away to a flat top."""
    out = measure("""
import { makeWaterMist } from './lib/watermist.js';
const fs = makeWaterMist({ extent: 16, height: 2.4, density: 0.62 })
    .getObjectByName('MistCards').material.fragmentShader;
const num = (re) => {
  const m = fs.match(re);
  return m ? parseFloat(m[1]) : null;
};
console.log(JSON.stringify({
  fadeX: num(/smoothstep\\(([0-9.]+), 1\\.0, e\\.x\\)/),
  fadeY: num(/smoothstep\\(([0-9.]+), 1\\.0, e\\.y\\)/),
  coarse: num(/astraFbm2\\(q0 \\* ([0-9.]+) \\+ vec2\\(11\\.3/),
  // The thickness field: a floor plus a fine octave plus a coarse one.
  lift: fs.match(/float lift = ([^;]+);/)[1],
}));
""", _LIBS)
    # LAW 3.  Both axes fade from inside the middle of the card, so no two
    # rims can agree on a screen row.
    assert 0 < out["fadeY"] <= 0.30, out
    assert 0 < out["fadeX"] <= 0.40, out
    # LAW 4.  The coarse octave is read at a fraction of the wisp
    # frequency — structure metres across is the only kind a ray integral
    # of a dozen cards leaves standing.
    assert 0 < out["coarse"] <= 0.35, out
    lift = out["lift"]
    assert "astraFbm2(q0, 3)" in lift and "coarse" in lift, out
