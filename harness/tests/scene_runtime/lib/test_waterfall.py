"""A waterfall has to read as one from angles nobody framed for it.

Every model attempt at this brief built a flat plane, which is a line
edge-on; the reference build that convinced was the one with a swept
closed section, a projectile path, and threads that accelerate.  Those
four properties are the reference's, ported 2026-09-01 from
scene_multifile_graphics (tests/test_waterfall_lib.py), and they are held
here as they stood.

THE PORT'S OWN LAWS — each one a frame rendered on our host under
fx/out/waterfall/, looked at, and measured against the same frame with
the reference module in place:

1. A FALL IS NOT A WHITE CURTAIN.  Every pixel of the sheet was mixed
   toward one near-white value: measured on the close camera, mean
   saturation 0.099 with 24.2% of the sheet's pixels above 0.85
   luminance.  Everything in the shader now rides one AERATION ramp —
   coherent green-blue glass at the lip, torn spray by the foot — which
   takes that to 0.118 saturation and 0.14% near-white, with the lip
   reading as water rather than as frosted glass.

2. THE LIGHT IS THE SCENE'S.  All three materials are unlit, so every
   photon in them is a decision, and the reference's were daylight
   constants.  Measured on this harness's night rig, the old sheet came
   back at luminance 0.344 over a frame whose mean was 0.168 — a sheet
   of white plastic hanging in the dark.  It reads the key light, the
   hemisphere fill and the fog at the first render (0.174 over 0.144
   now), and `keepOutOfDepthPasses` has already claimed `onBeforeRender`
   there, so the adoption CHAINS onto that guard rather than replacing
   it.

3. THE PLUNGE POOL IS WATER, NOT MILK.  The disc mixed 85% of its noise
   straight to foam over its whole radius — a lid of grey milk on the
   dirt, with the deep colour never appearing at all.  Foam now belongs
   to the boil and travels outward as streaks in (angle, radius - time).
"""

from __future__ import annotations

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("shader.js", "waterfall.js")

# A scene the way `sunRig` builds one: a key directional light, a
# hemisphere fill and fog.  `adoptSceneLight` runs off `onBeforeRender`,
# which a probe can call directly — no GPU needed to prove what it read.
# The guard `keepOutOfDepthPasses` installed runs FIRST in that chain and
# touches the geometry, so the call carries the real geometry and
# material three would hand it.
_LIT_SCENE = """
function litScene(THREE, opts) {
  const o = opts || {};
  const scene = new THREE.Scene();
  const key = new THREE.DirectionalLight(o.sunHex || 0xfff0d8,
      o.sunI === undefined ? 5.4 : o.sunI);
  // Direction is position -> target, so a light at +y+x shines DOWN and
  // the adopted uSun must point back up at it.
  key.position.set(30, 40, -10);
  scene.add(key);
  const hemi = new THREE.HemisphereLight(o.skyHex || 0x9db8e8, 0x8a7f6a,
      o.fillI === undefined ? 1.4 : o.fillI);
  scene.add(hemi);
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  return scene;
}
const adopt = (g, scene) => {
  const m = g.getObjectByName('Sheet');
  m.onBeforeRender(null, scene, null, m.geometry, m.material);
  return m.material.uniforms;
};
const rgb = (c) => [+c.r.toFixed(4), +c.g.toFixed(4), +c.b.toFixed(4)];
const lum = (c) => +(0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b).toFixed(4);
"""


def test_the_sheet_is_a_body_not_a_plane():
    """A flat sheet vanishes edge-on: the section must have real depth,
    and it must follow a parabola rather than a straight drop."""
    out = measure("""
import { makeWaterfall } from './lib/waterfall.js';
const g = makeWaterfall({ height: 8, width: 3.2 });
const sheet = g.getObjectByName('Sheet');
const p = sheet.geometry.attributes.position.array;
let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9, minZ = 1e9, maxZ = -1e9;
// The mid-height row tells a parabola from a ramp: half the drop is
// reached well before half the throw.
let zAtHalfDrop = 0, bestDy = 1e9;
for (let i = 0; i < p.length; i += 3) {
  minX = Math.min(minX, p[i]); maxX = Math.max(maxX, p[i]);
  minY = Math.min(minY, p[i + 1]); maxY = Math.max(maxY, p[i + 1]);
  minZ = Math.min(minZ, p[i + 2]); maxZ = Math.max(maxZ, p[i + 2]);
  const dy = Math.abs(p[i + 1] - 4.0);   // nearest sample to half drop
  if (dy < bestDy) { bestDy = dy; zAtHalfDrop = p[i + 2]; }
}
console.log(JSON.stringify({
  width: +(maxX - minX).toFixed(2),
  drop: +(maxY - minY).toFixed(2),
  depth: +(maxZ - minZ).toFixed(2),
  throwTotal: +maxZ.toFixed(2),
  zAtHalfDrop: +zAtHalfDrop.toFixed(2),
  names: g.children.map((c) => c.name).sort(),
  hasTick: typeof g.userData.tick === 'function',
}));
""", _LIBS)
    assert out["drop"] == 8.0 and 3.0 <= out["width"] <= 3.3
    # Depth is the whole point: a plane would measure ~0 here.
    assert out["depth"] > 1.2, out
    # Free fall: half the height is gone by ~70% of the way down, so the
    # sheet is still well short of the landing point there.
    assert out["zAtHalfDrop"] < out["throwTotal"] * 0.75, out
    assert out["names"] == ["Mist", "PlungePool", "Sheet"]
    assert out["hasTick"]


def test_the_mist_keeps_position_at_the_origin():
    """GTAOPass redraws with an override material that ignores custom
    vertex shaders, so a billboard that keeps its quad in `position`
    burns a black rectangle at the world origin."""
    out = measure("""
import { makeWaterfall } from './lib/waterfall.js';
const mist = makeWaterfall({ mist: 40 }).getObjectByName('Mist');
const pos = Array.from(mist.geometry.attributes.position.array);
console.log(JSON.stringify({
  posAllZero: pos.every((v) => v === 0),
  hasCorner: !!mist.geometry.attributes.aCorner,
  instances: mist.geometry.instanceCount,
  seeded: mist.geometry.attributes.iSeed.count,
}));
""", _LIBS)
    assert out["posAllZero"] and out["hasCorner"]
    assert out["instances"] == 40 and out["seeded"] == 40


def test_two_waterfalls_with_one_seed_are_identical():
    """Determinism is the contract for every shipped factory: a re-run
    that reshuffles the mist is a re-run nobody can compare against."""
    out = measure("""
import { makeWaterfall } from './lib/waterfall.js';
const seedOf = (s) => Array.from(
    makeWaterfall({ seed: s, mist: 24 })
        .getObjectByName('Mist').geometry.attributes.iSeed.array);
const a = seedOf(5), b = seedOf(5), c = seedOf(9);
console.log(JSON.stringify({
  same: JSON.stringify(a) === JSON.stringify(b),
  differs: JSON.stringify(a) !== JSON.stringify(c),
}));
""", _LIBS)
    assert out["same"] and out["differs"]


def test_tick_advances_every_material_in_the_group():
    out = measure("""
import { makeWaterfall } from './lib/waterfall.js';
const g = makeWaterfall({ mist: 12 });
g.userData.tick(2.5);
const times = [];
g.traverse((o) => {
  for (const m of [].concat(o.material || [])) {
    if (m && m.uniforms && m.uniforms.uTime) times.push(m.uniforms.uTime.value);
  }
});
console.log(JSON.stringify({ times }));
""", _LIBS)
    assert out["times"] and all(v == 2.5 for v in out["times"])


def test_the_sheet_normal_is_world_space_like_everything_it_meets():
    """three's `normalMatrix` is the inverse transpose of the modelVIEW
    matrix, so it yields a VIEW-space normal.  Both consumers here —
    astraFresnel-style rim work and astraFacing for the silhouette fade —
    are handed `cameraPosition - vW`, which is world space.  Mixing the
    two keys the rim and the fade to where the camera is POINTING rather
    than to how the sheet is turned, so they swim as the camera orbits a
    stationary waterfall."""
    out = measure(r"""
import { makeWaterfall } from './lib/waterfall.js';
const g = makeWaterfall({ height: 6, width: 3, seed: 2 });
const mats = [];
g.traverse((o) => { if (o.material && o.material.vertexShader) mats.push(o.material); });
const bad = mats.filter((m) =>
  /normalMatrix\s*\*\s*normal/.test(m.vertexShader)
  && /cameraPosition/.test(m.fragmentShader));
console.log(JSON.stringify({
  materials: mats.length,
  mixedSpaces: bad.length,
  worldNormals: mats.filter((m) =>
    /mat3\(modelMatrix\)\s*\*\s*normal/.test(m.vertexShader)).length,
}));
""", _LIBS)
    assert out["materials"] >= 1
    assert out["mixedSpaces"] == 0, (
        "a view-space normal is being dotted with a world-space view "
        "vector")
    assert out["worldNormals"] >= 1


def test_the_light_is_the_scene_s_and_one_bundle_feeds_all_three():
    """LAW 2.  The sheet, the pool and the mist must agree about where
    the sun is and what colour it is — three unlit materials with three
    private sets of daylight constants are three separate objects
    standing in the same place — so they share ONE uniform bundle, and
    one adoption off the sheet updates every one of them."""
    out = measure(_LIT_SCENE + """
import * as THREE from 'three';
import { makeWaterfall } from './lib/waterfall.js';
const g = makeWaterfall({ mist: 8 });
const before = rgb(g.getObjectByName('Sheet').material.uniforms.uSunCol.value);
const u = adopt(g, litScene(THREE, { sunHex: 0xff9060 }));
const pool = g.getObjectByName('PlungePool').material.uniforms;
const mist = g.getObjectByName('Mist').material.uniforms;
console.log(JSON.stringify({
  before,
  sun: [+u.uSun.value.x.toFixed(3), +u.uSun.value.y.toFixed(3),
        +u.uSun.value.z.toFixed(3)],
  sunCol: rgb(u.uSunCol.value),
  // One bundle: the pool's and the mist's uniforms are the SAME objects.
  poolShares: pool.uSunCol === u.uSunCol && pool.uSun === u.uSun,
  mistShares: mist.uSunCol === u.uSunCol && mist.uAmb === u.uAmb,
  // ... and every material still has its OWN uTime, or one tick would
  // be three writes to one number.
  ownTime: u.uTime !== pool.uTime && pool.uTime !== mist.uTime,
}));
""", _LIBS)
    # The key is up and to the +x/-z side; the adopted vector points at it.
    assert out["sun"][1] > 0.7 and out["sun"][0] > 0.4 and out["sun"][2] < 0
    # A warm key really arrives: red climbs away from green and blue.
    assert out["sunCol"][0] > out["sunCol"][2] * 2.5, out
    assert out["sunCol"] != out["before"]
    assert out["poolShares"] and out["mistShares"] and out["ownTime"], out


def test_a_caller_who_pins_the_light_keeps_it():
    """Adoption is a default, not a policy: a scene that hands the fall
    its own key must not have it taken away at the first render."""
    out = measure(_LIT_SCENE + """
import * as THREE from 'three';
import { makeWaterfall } from './lib/waterfall.js';
const g = makeWaterfall({
  mist: 0, sunDir: new THREE.Vector3(0, 1, 0), sunColor: 0x00ff00,
  sky: 0xff0000, ambient: 0x0000ff,
});
const u = adopt(g, litScene(THREE));
console.log(JSON.stringify({
  sun: [+u.uSun.value.x.toFixed(3), +u.uSun.value.y.toFixed(3)],
  sunCol: rgb(u.uSunCol.value),
  sky: rgb(u.uSky.value),
  amb: rgb(u.uAmb.value),
}));
""", _LIBS)
    assert out["sun"] == [0.0, 1.0]
    assert out["sunCol"][1] > 0.9 and out["sunCol"][0] < 0.01
    assert out["sky"][0] > 0.9 and out["amb"][2] > 0.9


def test_a_moonlit_fall_is_not_a_daylight_one():
    """LAW 2, the half a render caught: what is adopted is the key's
    strength RELATIVE to daylight, so the night rig's 2.2 moon arrives
    as ~0.41 of the 5.4 sun instead of as the same white sheet."""
    out = measure(_LIT_SCENE + """
import * as THREE from 'three';
import { makeWaterfall } from './lib/waterfall.js';
const day = adopt(makeWaterfall({ mist: 0 }), litScene(THREE));
const dayL = lum(day.uSunCol.value), dayA = lum(day.uAmb.value);
const night = adopt(makeWaterfall({ mist: 0 }), litScene(THREE, {
  sunHex: 0xb5c7e8, sunI: 2.2, skyHex: 0x3f5378, fillI: 1.0 }));
console.log(JSON.stringify({
  dayKey: dayL, nightKey: lum(night.uSunCol.value),
  dayAmb: dayA, nightAmb: lum(night.uAmb.value),
}));
""", _LIBS)
    assert out["nightKey"] < out["dayKey"] * 0.5, out
    assert out["nightAmb"] < out["dayAmb"] * 0.5, out
    # Dark, but never off: an unlit material at zero is a black hole.
    assert out["nightKey"] > 0.02 and out["nightAmb"] > 0.005, out


def test_adopting_the_light_does_not_disarm_the_override_guard():
    """`keepOutOfDepthPasses` guards the sheet by owning
    `onBeforeRender`, and `adoptSceneLight` wants the same hook: the
    adoption CHAINS.  Replacing it would put a double-sided transparent
    shell back into the ambient-occlusion pass as a solid wall — 50/255
    of darkening on ground it never touched, which is the measurement
    that put the guard there in the first place."""
    out = measure(_LIT_SCENE + """
import * as THREE from 'three';
import { makeWaterfall } from './lib/waterfall.js';
const g = makeWaterfall({ mist: 4 });
const scene = litScene(THREE);
const sheet = g.getObjectByName('Sheet');
const own = sheet.material;
const override = new THREE.MeshDepthMaterial();
// three's own call shape, once with the mesh's material and once with
// the override an AO or shadow pass substitutes.
sheet.onBeforeRender(null, scene, null, sheet.geometry, own);
const drawnNormally = sheet.geometry.drawRange.count;
sheet.onBeforeRender(null, scene, null, sheet.geometry, override);
const drawnInOverride = sheet.geometry.drawRange.count;
sheet.onAfterRender(null, scene, null, sheet.geometry);
console.log(JSON.stringify({
  drawnNormally: drawnNormally === Infinity ? -1 : drawnNormally,
  drawnInOverride,
  restored: sheet.geometry.drawRange.count === Infinity,
  casts: sheet.castShadow,
  adopted: +sheet.material.uniforms.uSun.value.y.toFixed(3),
}));
""", _LIBS)
    assert out["drawnNormally"] == -1        # Infinity: drawn in full
    assert out["drawnInOverride"] == 0       # and skipped by the AO pass
    assert out["restored"] and not out["casts"]
    assert out["adopted"] > 0.7              # the chain still adopted


_SCENE = """
import * as THREE from 'three';
import { tickShaders } from './lib/shader.js';
import { makeWaterfall } from './lib/waterfall.js';

export async function createScene() {
  const scene = new THREE.Scene();
  // Fogged, because the mist is one custom shader on the RAW route and
  // the fog chunk is exactly what a hand-written shader forgets.
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  const key = new THREE.DirectionalLight(0xfff0d8, 5.4);
  key.position.set(30, 40, -10);
  scene.add(key);
  scene.add(new THREE.HemisphereLight(0x9db8e8, 0x8a7f6a, 1.4));
  const fall = makeWaterfall({ height: 8, width: 3.2, seed: 3, mist: 40 });
  scene.add(fall);
  // A camera, because the host reports a scene with none as not booted
  // and never reaches the compile stage at all.
  return {
    scene,
    cameras: [{ name: 'a', position: [9, 4, 12], lookAt: [0, 3, 0],
                fov: 45 }],
    update(t) { tickShaders(scene, t); fall.userData.tick(t); },
  };
}
"""


def test_the_whole_waterfall_compiles_on_our_gpu():
    """Three custom shaders, one of them on the raw route with its own
    billboard projection — the depth, fog and tone-map chunks are
    hand-carried there, and only the GPU can say whether that was done
    right.  The raw route also gets no GLSL_UTIL, which is why the mist's
    value noise is written out inside it."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "every program compiled" in out
    # Sheet, pool and mist: three, or one of them silently never built.
    assert '"custom_materials":3' in out.replace(" ", ""), out
    assert "WARN" not in out, out
