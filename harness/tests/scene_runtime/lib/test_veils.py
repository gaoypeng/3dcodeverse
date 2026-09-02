"""veils.js: weather seen AT DISTANCE — rain sheets, snowfall, dust motes.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_veils_lib.py).  Its physical laws are kept exactly as they
stood, because each one is about the effect rather than about their
renderer: the rain veil must slant with the wind it is given and travel in
a straight line, the snow must WANDER (that is the whole difference
between snow and white rain), the motes must hang inside the box the
caller was promised, every field must replay from its seed, and one tick
must reach every material.  Their two renderer-contract tests are folded
into one compile check here — our renderer runs logarithmicDepthBuffer OFF
so the logdepth chunks are no-ops, but the FOG branch trap they were
written for is real on any host, so both the fogged and the fog-less scene
are still compiled on the GPU.

THE PORT'S OWN LAW — measured on our host at fx/out/veils/, each frame
looked at and diffed against a control render of the same scene with the
three fields removed (fx/out/veils_none/):

  A VEIL IS NOT A LAMP.  All three materials are unlit billboards at a
  constant colour, and an unlit pale card is the brightest thing in any
  frame that is not daylight.  On the moonlit rig the shipped rain
  curtain added +0.189 luminance to the sky band it crossed, with peaks
  at 0.523 over a scene whose own mean was 0.170 — a sheet GLOWING in
  front of a night sky (fx/out/veils/before_night/frames/mid_t1p5.png) —
  and the motes stayed white inside a shadow.  Nothing here can be lit
  properly, because a veil is a volume and not a surface; but what it
  SCATTERS is the irradiance where it hangs, and that is one vec3 the
  scene can be asked for.  Each field now probes its own scene's lights
  every few frames and multiplies its colour by the answer, normalised so
  the library's own day rig lands on 1.0 — so daylight is unchanged
  (mid mean_lum 0.6121 -> 0.6125) and the same night curtain adds +0.081
  at a peak of 0.249, in the night's own blue (saturation 0.31 -> 0.50)
  rather than in grey.  A lamp is included with its falloff at the
  field's own origin, which is what puts a mote volume standing in a
  lamp's pool into that pool.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("shader.js", "veils.js")
# The light probe is calibrated against the library's OWN day rig, so the
# test that holds the calibration has to build one.
_RIG_LIBS = ("shader.js", "veils.js", "environment.js", "sky.js")

_SCENE = """
import * as THREE from 'three';
import { makeRainVeil, makeSnowfall, makeMotes } from './lib/veils.js';

export const BOUNDS = { min: [-60, 0, -60], max: [60, 45, 60] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  __FOG__
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const veil = makeRainVeil({ extent: 90, height: 40, direction: [4, 2] });
  const snow = makeSnowfall({ extent: 30, height: 14, seed: 4 });
  const motes = makeMotes({ extent: 5, height: 3, seed: 6 });
  scene.add(veil); scene.add(snow); scene.add(motes);
  return {
    scene,
    cameras: [{ name: 'a', position: [20, 6, 24], lookAt: [0, 8, 0],
                fov: 45 }],
    update(t) {
      veil.userData.tick(t);
      snow.userData.tick(t);
      motes.userData.tick(t);
    },
  };
}
"""

# Perpendicular deviation of a horizontal path from its own chord: the one
# number that separates a wandering flake from a drifting sheet.
_DEVIATE = """
const deviate = (pts) => {
  const a = pts[0], b = pts[pts.length - 1];
  const dx = b[0] - a[0], dz = b[1] - a[1];
  const len = Math.hypot(dx, dz);
  if (len < 1e-9) return 0;
  let m = 0;
  for (const p of pts) {
    m = Math.max(m, Math.abs((p[0] - a[0]) * dz - (p[1] - a[1]) * dx) / len);
  }
  return m;
};
const jumped = (pts, lim) => {
  for (let i = 1; i < pts.length; i++) {
    if (Math.hypot(pts[i][0] - pts[i - 1][0],
                   pts[i][1] - pts[i - 1][1]) > lim) return true;
  }
  return false;
};
"""

_PROBE = """
import * as THREE from 'three';
import { makeRainVeil, makeSnowfall, makeMotes } from './lib/veils.js';

const fields = {
  veil: [makeRainVeil({ extent: 90, height: 40, direction: [4, 2],
      seed: 3 }), 'RainSheets'],
  snow: [makeSnowfall({ extent: 36, height: 16, seed: 4 }), 'Snowflakes'],
  mote: [makeMotes({ extent: 5, height: 3, count: 400, seed: 6 }),
      'DustMotes'],
};

const out = { fromHelper: true, outChunks: true, blending: {}, guard: {},
    tick: {}, radius: {}, centre: {}, uniforms: {} };
for (const [k, [g, name]] of Object.entries(fields)) {
  const m = g.getObjectByName(name);
  if (!m.material.userData.astraShader) out.fromHelper = false;
  const fs = m.material.fragmentShader;
  if (!fs.includes('tonemapping_fragment')
      || !fs.includes('colorspace_fragment')) out.outChunks = false;
  out.blending[k] = [m.material.blending === THREE.NormalBlending,
      !!m.material.transparent, !!m.material.depthWrite];
  out.guard[k] = [!!m.userData.astraNoOverride, m.castShadow === true];
  out.radius[k] = m.geometry.boundingSphere.radius;
  out.centre[k] = m.geometry.boundingSphere.center.toArray();
  out.uniforms[k] = Object.keys(m.material.uniforms).sort();
  const u = m.material.uniforms;
  const at0 = u.uTime.value;
  const n = g.userData.tick(4.25);
  out.tick[k] = { at0, n, after: u.uTime.value };
}
console.log(JSON.stringify(out));
"""

# One fake renderer frame per case: the light probe hangs off
# onBeforeRender, which is the only place a Mesh is handed its scene.
_LIGHT = """
import * as THREE from 'three';
import { makeRainVeil, makeSnowfall, makeMotes } from './lib/veils.js';
import { sunRig } from './lib/environment.js';

const R = { info: { render: { frame: 0 } } };
const draw = (obj, scene, frame) => {
  R.info.render.frame = frame;
  obj.traverse((o) => {
    if (o.isMesh && o.onBeforeRender) {
      o.onBeforeRender(R, scene, null, o.geometry, o.material, null);
    }
  });
};
const rigScene = (elev, fog) => {
  const s = new THREE.Scene();
  const rig = sunRig({ azimuth: 215, elevation: elev });
  s.add(rig.sun); s.add(rig.fill);
  s.fog = new THREE.FogExp2(fog, 0.0035);
  return s;
};
const lit = (mesh) => mesh.material.uniforms.uLight.value.toArray();
const col = (mesh) => mesh.material.uniforms.uColor.value.toArray();
const hsl = (mesh) => {
  const o = { h: 0, s: 0, l: 0 };
  mesh.material.uniforms.uColor.value.getHSL(o);
  return [o.h, o.s, o.l];
};

const out = {};

// 1. daylight, the rig the normalisation is calibrated against
const day = rigScene(38, 0xcfd8e6);
const dayVeil = makeRainVeil({ extent: 70 });
day.add(dayVeil);
draw(dayVeil, day, 100);
out.dayVeil = lit(dayVeil.children[0]);

// 2. the same field under the moon
const night = rigScene(-20, 0x0b0f1a);
const nightVeil = makeRainVeil({ extent: 70 });
const nightSnow = makeSnowfall({ extent: 30, height: 14 });
const nightMote = makeMotes({ extent: 5, height: 3 });
nightMote.position.set(1.2, 0, 2.4);
night.add(nightVeil); night.add(nightSnow); night.add(nightMote);
night.updateMatrixWorld(true);
draw(nightVeil, night, 200);
draw(nightSnow, night, 200);
draw(nightMote, night, 200);
out.nightVeil = lit(nightVeil.children[0]);
out.nightSnow = lit(nightSnow.children[0]);
out.nightMote = lit(nightMote.children[0]);
// and the fog hue it adopted, against the untouched default
out.nightVeilHsl = hsl(nightVeil.children[0]);
out.dayVeilHsl = hsl(dayVeil.children[0]);
out.plainHsl = (() => {
  const o = { h: 0, s: 0, l: 0 };
  new THREE.Color(0xb9c6d4).getHSL(o);
  return [o.h, o.s, o.l];
})();
out.fogHue = (() => {
  const o = { h: 0, s: 0, l: 0 };
  night.fog.color.getHSL(o);
  return o.h;
})();

// 3. the same night, with a lamp two metres from the mote volume
const lampScene = rigScene(-20, 0x0b0f1a);
const lamp = new THREE.PointLight(0xffd2a0, 90, 34, 2);
lamp.position.set(3.6, 4.6, 3.4);
lampScene.add(lamp);
const lampMote = makeMotes({ extent: 5, height: 3 });
lampMote.position.set(1.2, 0, 2.4);
const farMote = makeMotes({ extent: 5, height: 3 });
farMote.position.set(-40, 0, -40);
lampScene.add(lampMote); lampScene.add(farMote);
lampScene.updateMatrixWorld(true);
draw(lampMote, lampScene, 300);
draw(farMote, lampScene, 300);
out.lampMote = lit(lampMote.children[0]);
out.farMote = lit(farMote.children[0]);

// 4. a scene with no lights at all — an asset preview, or the shader
//    check's own fixture: the veil must stay exactly as authored
const bare = new THREE.Scene();
const bareVeil = makeRainVeil({ extent: 70 });
bare.add(bareVeil);
draw(bareVeil, bare, 400);
out.bareVeil = lit(bareVeil.children[0]);
out.bareColor = col(bareVeil.children[0]);

// 5. a colour the caller gave is a statement, not a default
const given = rigScene(-20, 0x0b0f1a);
const givenVeil = makeRainVeil({ extent: 70, color: 0x88ff44 });
given.add(givenVeil);
draw(givenVeil, given, 500);
out.givenColor = col(givenVeil.children[0]);
out.wantGiven = new THREE.Color(0x88ff44).toArray();

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return measure(_PROBE, _LIBS)


@pytest.fixture(scope="module")
def light() -> dict:
    """One node launch of _LIGHT: the scene-light probe under four rigs."""
    return measure(_LIGHT, _RIG_LIBS)


# --- the reference's laws, kept ---------------------------------------


def test_the_stated_bounding_sphere_covers_each_field():
    """`position` is all zeros, so the sphere is the only thing three can
    cull these fields by: state it too small and the whole field vanishes
    the moment the origin leaves frame, state it huge and a field long out
    of shot is drawn every frame."""
    out = measure("""
import { makeRainVeil, makeSnowfall, makeMotes } from './lib/veils.js';

const cross = (a, b) => [a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (v) => {
  const l = Math.hypot(v[0], v[1], v[2]);
  return [v[0] / l, v[1] / l, v[2] / l];
};

const veil = makeRainVeil({ extent: 90, height: 40, direction: [4, 2],
    seed: 3 });
const vm = veil.getObjectByName('RainSheets');
const vu = vm.material.uniforms;
const sheet = vm.geometry.attributes.aSheet.array;
const ax = [vu.uAxis.value.x, vu.uAxis.value.y, vu.uAxis.value.z];
const up = [-ax[0], -ax[1], -ax[2]];
// The quad's width rides on cross(axis, toCamera), which is any unit
// vector in the plane perpendicular to the axis — so sweep that plane.
const b1 = unit(cross(ax, Math.abs(ax[0]) > 0.9 ? [0, 0, 1] : [1, 0, 0]));
const b2 = unit(cross(ax, b1));
let veilNeed = 0;
for (let i = 0; i < vm.geometry.instanceCount; i++) {
  const hw = sheet[i * 4 + 2];
  for (let k = 0; k <= 70; k++) {
    const p = veil.userData.sample(i, k * 1.7);
    for (const c of [0, 1]) {
      const base = [p.x + up[0] * vu.uLen.value * c,
                    p.y + up[1] * vu.uLen.value * c,
                    p.z + up[2] * vu.uLen.value * c];
      for (let a = 0; a < 16; a++) {
        const th = (a / 16) * Math.PI * 2;
        const c1 = Math.cos(th), s1 = Math.sin(th);
        veilNeed = Math.max(veilNeed, Math.hypot(
            base[0] + (b1[0] * c1 + b2[0] * s1) * hw,
            base[1] + (b1[1] * c1 + b2[1] * s1) * hw,
            base[2] + (b1[2] * c1 + b2[2] * s1) * hw));
      }
    }
  }
}

const snow = makeSnowfall({ extent: 36, height: 16, seed: 4 });
const sm = snow.getObjectByName('Snowflakes');
const su = sm.material.uniforms;
const flake = sm.geometry.attributes.aFlake.array;
let sScale = 0;
for (let i = 0; i < sm.geometry.instanceCount; i++) {
  sScale = Math.max(sScale, flake[i * 4]);
}
// Worst case a flake is drawn at: full bokeh, corner of its quad.
const sHalf = su.uSize.value * sScale * (1 + su.uBokeh.value) * Math.SQRT1_2;
let snowNeed = 0;
for (let i = 0; i < sm.geometry.instanceCount; i += 5) {
  for (let k = 0; k < 45; k++) {
    snowNeed = Math.max(snowNeed,
        snow.userData.sample(i, k * 0.83).length() + sHalf);
  }
}

const motes = makeMotes({ extent: 5, height: 3, count: 400, seed: 6 });
const mm = motes.getObjectByName('DustMotes');
const mu = mm.material.uniforms;
const mote = mm.geometry.attributes.aMote.array;
let mScale = 0;
for (let i = 0; i < mm.geometry.instanceCount; i++) {
  mScale = Math.max(mScale, mote[i * 4]);
}
const mHalf = mu.uSize.value * mScale * 1.2 * Math.SQRT1_2;
let moteNeed = 0;
for (let i = 0; i < mm.geometry.instanceCount; i += 3) {
  for (let k = 0; k < 40; k++) {
    moteNeed = Math.max(moteNeed,
        motes.userData.sample(i, k * 1.3).length() + mHalf);
  }
}

console.log(JSON.stringify({
  veilRadius: vm.geometry.boundingSphere.radius, veilNeed,
  snowRadius: sm.geometry.boundingSphere.radius, snowNeed,
  moteRadius: mm.geometry.boundingSphere.radius, moteNeed,
}));
""", _LIBS)
    assert out["veilRadius"] >= out["veilNeed"], out
    assert out["snowRadius"] >= out["snowNeed"], out
    assert out["moteRadius"] >= out["moteNeed"], out
    # And not a made-up huge number, which keeps a field that left the
    # frame long ago in every draw call.
    assert out["veilRadius"] <= out["veilNeed"] * 1.5, out
    assert out["snowRadius"] <= out["snowNeed"] * 1.15, out
    assert out["moteRadius"] <= out["moteNeed"] * 1.15, out


def test_the_sphere_is_local_because_the_group_carries_the_placement(probe):
    """A sphere centred anywhere but the field's own origin is culled
    against the wrong point the moment the caller moves the group."""
    assert probe["centre"]["veil"] == [0, 0, 0], probe["centre"]
    assert probe["centre"]["mote"] == [0, 0, 0], probe["centre"]


def test_snow_wanders_where_rain_falls_in_a_straight_line():
    """The one property that separates snow from rain that is white.  A
    drop falls at 9 m/s down a line the wind tilts; a flake falls at under
    one and its horizontal path is a curve going nowhere.  Both claims are
    measured against the same yardstick — how far the path departs from
    its own chord."""
    out = measure(_DEVIATE + """
import { makeRainVeil, makeSnowfall } from './lib/veils.js';

const veil = makeRainVeil({ extent: 120, height: 50, direction: [4, 2],
    seed: 3 });
const rainDev = [];
for (let i = 0; i < veil.getObjectByName('RainSheets')
    .geometry.instanceCount; i++) {
  const pts = [];
  for (let k = 0; k <= 24; k++) {
    const p = veil.userData.sample(i, k * 0.4);
    pts.push([p.x, p.z]);
  }
  if (!jumped(pts, 30)) rainDev.push(deviate(pts));
}

const snow = makeSnowfall({ extent: 40, height: 18, seed: 4 });
const sm = snow.getObjectByName('Snowflakes');
const snowDev = [];
let slowest = 0;
for (let i = 0; i < 400; i++) {
  const pts = [];
  for (let k = 0; k <= 24; k++) {
    const p = snow.userData.sample(i, k * 0.4);
    pts.push([p.x, p.z]);
  }
  if (!jumped(pts, 10)) snowDev.push(deviate(pts));
}
snowDev.sort((a, b) => a - b);
const fall = sm.material.uniforms.uFall.value;
const flake = sm.geometry.attributes.aFlake.array;
for (let i = 0; i < sm.geometry.instanceCount; i++) {
  slowest = Math.max(slowest, fall * flake[i * 4 + 1]);
}
console.log(JSON.stringify({
  rainMaxDev: Math.max(...rainDev),
  rainPaths: rainDev.length,
  snowMedDev: snowDev[Math.floor(snowDev.length / 2)],
  snowMinDev: snowDev[0],
  snowPaths: snowDev.length,
  snowFastestFall: slowest,
  rainFall: veil.getObjectByName('RainSheets')
      .material.uniforms.uFall.value,
}));
""", _LIBS)
    # Paths that wrapped inside the window are dropped, so both counts are
    # floors, not the field size.
    assert out["rainPaths"] >= 3 and out["snowPaths"] >= 200, out
    # A sheet travels with the wind: a straight line, to float error.
    assert out["rainMaxDev"] < 1e-4, out
    # A flake does not.
    assert out["snowMedDev"] > 0.08, out
    assert out["snowMinDev"] > 0.005, out
    # And it FALLS SLOWLY: even the heaviest flake is far under rain.
    assert out["snowFastestFall"] < 1.5, out
    assert out["rainFall"] > 8.9, out


def test_the_rain_sheets_slant_with_the_wind_they_are_given():
    """A vertical curtain reads as a projection screen.  The sheet lies
    along the path a drop actually takes, so its top is UPWIND of its foot
    — a drop travels downwind as it falls — and the tilt is the wind speed
    against the 9 m/s fall, not a decorative angle."""
    out = measure("""
import { makeRainVeil } from './lib/veils.js';
const probe = (dir) => {
  const g = makeRainVeil({ extent: 80, height: 30, direction: dir, seed: 5 });
  const u = g.getObjectByName('RainSheets').material.uniforms;
  const a = u.uAxis.value;
  const foot = g.userData.sample(0, 0);
  const len = g.userData.length;
  const top = [foot.x - a.x * len, foot.y - a.y * len, foot.z - a.z * len];
  const w = Math.hypot(dir[0], dir[1]);
  return {
    axis: [a.x, a.y, a.z],
    // The horizontal part of the axis must lie ALONG the wind.
    para: (a.x * dir[1] - a.z * dir[0]) / Math.max(w, 1e-9),
    along: (a.x * dir[0] + a.z * dir[1]) / Math.max(w, 1e-9),
    tilt: Math.hypot(a.x, a.z) / -a.y,
    want: w / 9,
    lean: [top[0] - foot.x, top[2] - foot.z],
    rise: top[1] - foot.y,
    height: u.uLen.value * -a.y,
  };
};
console.log(JSON.stringify({
  east: probe([4.5, 0]), skew: probe([3, -5]), calm: probe([0, 0]),
}));
""", _LIBS)
    for name in ("east", "skew"):
        p = out[name]
        assert p["axis"][1] < -0.5, (name, p)
        assert abs(p["para"]) < 1e-6, (name, p)
        assert p["along"] > 0, (name, p)
        # tan(tilt) is the wind against the fall, to a percent.
        assert abs(p["tilt"] - p["want"]) < 0.01 * p["want"] + 1e-6, p
        # Top upwind of foot, and the sheet still spans the height it was
        # asked for however far it leans.
        assert p["lean"][0] * p["axis"][0] < 0, (name, p)
        assert p["rise"] > 0, (name, p)
        assert abs(p["height"] - 30) < 0.05, (name, p)
    # No wind is a plumb curtain, not a division by zero.
    assert out["calm"]["axis"] == [0, -1, 0], out["calm"]
    assert out["calm"]["tilt"] == 0, out["calm"]


def test_motes_stay_inside_the_volume_they_state():
    """Dust HANGS.  The wander is what stops it reading as a dot screen,
    so it is real — but it is bounded, and a mote that leaves the box the
    caller was promised is a speck floating outside the shaft it was
    placed in.  It must also FILL that box, or the guarantee is met by a
    field that never moves at all."""
    out = measure("""
import { makeMotes } from './lib/veils.js';
const g = makeMotes({ extent: 6, height: 4, count: 500, seed: 6 });
const n = g.getObjectByName('DustMotes').geometry.instanceCount;
let outside = 0, maxX = 0, maxZ = 0, maxY = 0, minY = 1e9, fastest = 0;
for (let i = 0; i < n; i++) {
  for (let k = 0; k <= 60; k++) {
    const t = k * 1.7;
    const p = g.userData.sample(i, t);
    if (Math.abs(p.x) > 3 + 1e-6 || Math.abs(p.z) > 3 + 1e-6
        || p.y < -1e-6 || p.y > 4 + 1e-6) outside++;
    maxX = Math.max(maxX, Math.abs(p.x));
    maxZ = Math.max(maxZ, Math.abs(p.z));
    maxY = Math.max(maxY, p.y);
    minY = Math.min(minY, p.y);
    // Step over a short interval, so the settle wrap is not a speed.
    const q = g.userData.sample(i, t + 0.5);
    if (Math.abs(q.y - p.y) < 1) {
      fastest = Math.max(fastest,
          Math.hypot(q.x - p.x, q.y - p.y, q.z - p.z) / 0.5);
    }
  }
}
console.log(JSON.stringify({ outside, maxX, maxZ, maxY, minY, fastest,
    volume: g.userData.volume }));
""", _LIBS)
    assert out["volume"] == {"extent": 6, "height": 4}
    assert out["outside"] == 0, out
    # It uses the box it claims, rather than hiding in the middle.
    assert out["maxX"] > 2.4 and out["maxZ"] > 2.4, out
    assert out["maxY"] > 3.6 and out["minY"] < 0.4, out
    # Near-still: dust hangs.  Anything faster is snow.
    assert out["fastest"] < 0.3, out


def test_the_same_seed_builds_the_same_field():
    """These ship into scenes rendered again and again from the same code;
    a field that resamples is a scene that will not reproduce, and a seed
    that changes nothing is not a seed."""
    out = measure("""
import { makeRainVeil, makeSnowfall, makeMotes } from './lib/veils.js';
const dump = (mesh, names) => names.map((k) =>
    Array.from(mesh.geometry.attributes[k].array));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const veil = (s) => dump(makeRainVeil({ extent: 70, seed: s })
    .getObjectByName('RainSheets'), ['aSheet', 'aVeil']);
const snow = (s) => dump(makeSnowfall({ extent: 20, seed: s })
    .getObjectByName('Snowflakes'), ['aPos', 'aFlake', 'aSwing']);
const mote = (s) => dump(makeMotes({ count: 60, seed: s })
    .getObjectByName('DustMotes'), ['aPos', 'aMote', 'aSwing']);
console.log(JSON.stringify({
  veilSame: same(veil(11), veil(11)), veilDiff: !same(veil(11), veil(12)),
  snowSame: same(snow(11), snow(11)), snowDiff: !same(snow(11), snow(12)),
  moteSame: same(mote(11), mote(11)), moteDiff: !same(mote(11), mote(12)),
}));
""", _LIBS)
    for k, v in out.items():
        assert v, k


def test_tick_advances_every_material_in_every_field(probe):
    """Everything that makes these three read as weather rather than as
    geometry lives in `uTime` — the filaments travelling down a sheet, the
    wander, the twinkle.  One missed material is one frozen field."""
    for k, r in probe["tick"].items():
        assert r["at0"] == 0, (k, r)
        assert r["n"] == 1, (k, r)
        assert r["after"] == 4.25, (k, r)


def test_none_of_the_three_adds_light(probe):
    """The module's own argument, and the reason the light probe below is
    a multiply rather than a lift: rain and snow SCATTER — they lower the
    contrast of the ridge behind them — and a mote is a speck of matter,
    not a lamp.  Additive blending turns every one of them into a white
    slab over a daylight sky at any strength worth having."""
    for k, (normal, transparent, depth_write) in probe["blending"].items():
        assert normal, f"{k} does not blend normally"
        assert transparent and not depth_write, (k, transparent, depth_write)


def test_the_whole_field_compiles_on_the_real_renderer():
    """A custom shader that fails to compile is discarded with no error at
    all — the effect is simply absent.  Both scenes have to be checked:
    asset mode builds an UNFOGGED scene, so `USE_FOG` is never defined and
    the fog branch of every shader here is never compiled, and these veils
    ship into fogged scenes."""
    fogged = _SCENE.replace(
        "__FOG__", "scene.fog = new THREE.FogExp2(0xc9d6e0, 0.004);")
    code, out = compile_scene(fogged, _LIBS)
    assert code == 0, out
    assert '"custom_materials":3' in out.replace(" ", ""), out
    assert "WARN" not in out, out
    code, out = compile_scene(_SCENE.replace("__FOG__", ""), _LIBS)
    assert code == 0, out
    assert "WARN" not in out, out


# --- the port's own laws ----------------------------------------------


def test_every_material_is_assembled_by_the_shader_helper(probe):
    """This host has NO post chain: ACES and the sRGB transfer happen in
    the fragment tail, from the two chunks three appends to its own
    shaders.  A hand-built ShaderMaterial that skips them renders on a
    different curve from every built-in beside it."""
    assert probe["fromHelper"], "a veil material was not built by shader.js"
    assert probe["outChunks"], (
        "a veil fragment shader is missing tonemapping_fragment / "
        "colorspace_fragment")


def test_no_veil_is_a_wall_to_a_depth_or_shadow_pass(probe):
    """GTAOPass rebuilds depth and normals through `scene.overrideMaterial`,
    which is opaque: a stack of rain sheets is a stack of solid floors in
    that buffer, occluding the ridge it hangs in front of.  Every other
    veil in this library (rain, godrays, watermist, waterfall, flock,
    neon) is guarded; these three were not, which is a defect that only
    appears the day the post chain lands."""
    for k, (guarded, casts) in probe["guard"].items():
        assert guarded, f"{k} is not kept out of override passes"
        assert not casts, f"{k} casts a shadow"


def test_a_veil_carries_the_light_of_the_scene_it_hangs_in(light):
    """THE PORT'S LAW.  An unlit pale card is the brightest thing in any
    frame that is not daylight — measured, a moonlit rain curtain adding
    +0.189 luminance at peaks of 0.523 over a scene whose mean was 0.170.
    The probe answers with the irradiance where the field hangs,
    normalised so the library's own day rig is 1.0."""
    day = light["dayVeil"]
    night = light["nightVeil"]
    # Daylight is the calibration point: the whole corpus of daylight
    # frames must look exactly as it did.
    for c in day:
        assert 0.85 <= c <= 1.25, day
    # Moonlight is a different scene, and the veil has to know.
    assert max(night) < 0.55 * min(day), (day, night)
    # ... in the night's own blue, not in grey.
    assert night[2] > night[0] * 1.3, night
    # Snow and dust read the same probe.
    assert max(light["nightSnow"]) < 0.55 * min(day), light["nightSnow"]
    assert max(light["nightMote"]) < 0.7 * min(day), light["nightMote"]


def test_a_lamp_pools_on_the_dust_standing_in_it(light):
    """The half of the probe a hemisphere cannot give: dust in a lamp's
    beam is the thing motes exist for, and a scene-wide average would
    light the specks forty metres away exactly as brightly."""
    near, far = light["lampMote"], light["farMote"]
    assert near[0] > far[0] * 1.6, (near, far)
    # A tungsten practical is warm, so the dust standing in it turns warm
    # against the blue the same night leaves on the field across the
    # valley — the ratio, not the channel, because the moonlight is still
    # in both of them.
    assert near[0] / near[2] > (far[0] / far[2]) * 1.8, (near, far)
    # The field across the valley is left in the moonlight it stands in.
    assert far[2] > far[0] * 1.3, far


def test_a_scene_with_no_lights_leaves_the_veil_exactly_as_authored(light):
    """An asset preview and the shader check both build a scene with no
    lights in it.  A probe that answered zero there would deliver an
    invisible effect and a green test run."""
    assert light["bareVeil"] == [1, 1, 1], light["bareVeil"]


def test_an_untouched_default_takes_the_fog_hue_and_a_given_colour_does_not(
        light):
    """A veil the scene's own haze does not agree with reads as a card in
    FRONT of the weather.  The default takes the fog's hue at its own
    lightness — taking its value as well would send the veil to black in
    a night scene the light probe has already dimmed — and a colour the
    caller passed is a statement that is left alone."""
    night_h, night_s, night_l = light["nightVeilHsl"]
    plain_h, _, plain_l = light["plainHsl"]
    assert abs(night_h - light["fogHue"]) < 0.02, (night_h, light["fogHue"])
    assert abs(night_h - plain_h) > 0.01, (night_h, plain_h)
    # Its own lightness, to the round trip through HSL.
    assert abs(night_l - plain_l) < 1e-6, (night_l, plain_l)
    assert light["nightVeilHsl"][1] <= 0.30 + 1e-9, light["nightVeilHsl"]
    for got, want in zip(light["givenColor"], light["wantGiven"], strict=True):
        assert abs(got - want) < 1e-6, (light["givenColor"],
                                        light["wantGiven"])
