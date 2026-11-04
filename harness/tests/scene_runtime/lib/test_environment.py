"""environment.js (91 recorded scenes import it through the starter's env.js)."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "sky.js", "environment.js")


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


def test_night_dome_uses_the_bakes_set_sun_instead_of_the_moon():
    """An explicit night rig baked night reflections but painted a daylight dome."""
    out = _measure("""
import * as THREE from 'three';
import { sunRig } from './lib/environment.js';
import { makeSky } from './lib/sky.js';
const states = [
  {mood:'night'}, {mood:'day'}, {mood:'golden',elevation:-5},
].map(options => {
  const rig = sunRig({...options, disc:true});
  const original = rig.sunDir.clone();
  const {sky, sunDir, setSunDirection} = makeSky(new THREE.Scene(), {rig});
  const u = sky.material.uniforms;
  const state = {night:u.uNight.value, solarY:u.sunPosition.value.y,
    lightY:rig.lightDir.y, discVisible:rig.sunDisc.visible,
    agreesWithBake:u.sunPosition.value.distanceTo(rig.skySunDir),
    inputUnchanged:rig.sunDir.distanceTo(original),
    publicDirectionUnchanged:sunDir.distanceTo(original)};
  setSunDirection([0,1,0]);
  state.canReturnToDay = u.uNight.value === 0 && u.sunPosition.value.y === 1 && sunDir.y === 1;
  sky.userData.dispose(); rig.dispose();
  return state;
});
console.log(JSON.stringify(states));
""")
    night, day, twilight = out
    assert night["night"] == 1 and night["solarY"] < 0 < night["lightY"]
    assert night["discVisible"], "The moon remains visible beside the night sky"
    assert day["night"] == 0 and day["solarY"] > 0
    assert not day["discVisible"], "The physical sky already draws its own sun"
    assert 0 < twilight["night"] < 1 and twilight["solarY"] < 0
    assert all(s["agreesWithBake"] < 1e-12 and s["inputUnchanged"] == 0 for s in out)
    assert all(s["publicDirectionUnchanged"] < 1e-12 for s in out)
    assert all(s["canReturnToDay"] for s in out)


_SHELL = """
import * as THREE from 'three';
import { worldShell } from './lib/environment.js';
const rand = (() => { let s = 9; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
const env = worldShell({ rand, mood: 'day', ridge: true });
env.group.updateMatrixWorld(true);
const hitsFor = (dir) => new THREE.Raycaster(new THREE.Vector3(0, 1.7, 0), dir.normalize()).intersectObject(env.group, true).map((h) => h.object.name);
const outward = [];
for (let a = 0; a < 8; a++) outward.push(hitsFor(new THREE.Vector3(Math.cos(a / 8 * Math.PI * 2), 0, Math.sin(a / 8 * Math.PI * 2))));
const noRidge = worldShell({ rand, mood: 'day', ridge: false });
const bare = worldShell({ rand, mood: 'day' });
const T = (rho, d) => Math.exp(-((rho * d) ** 2));
const mk = () => () => 0.5;
// a directional crest: nothing to the east (0-90 deg), a full crest to the west
const shaped = worldShell({ rand, mood: 'day', ridge: true, ridgeHeight: 0.1,
  ridgeProfile: (az) => (az < 90 ? 0 : 1) }).group.getObjectByName('HorizonRidge');
const sp = shaped.geometry.attributes.position;
let eastTop = -1e9, westTop = -1e9;
for (let i = 0; i < sp.count; i++) {
  const az = (Math.atan2(sp.getZ(i), sp.getX(i)) * 180 / Math.PI + 360) % 360;
  if (az > 5 && az < 85) eastTop = Math.max(eastTop, sp.getY(i));
  if (az > 185 && az < 265) westTop = Math.max(westTop, sp.getY(i));
}
const big = worldShell({ rand: mk(), mood: 'day', bounds: 1200 });
const over = worldShell({ rand: mk(), mood: 'overcast', bounds: 1200 });
console.log(JSON.stringify({ outward, ridgeOptional: !noRidge.group.getObjectByName('HorizonRidge'), ridgeOffByDefault: !bare.group.getObjectByName('HorizonRidge'),
  bareOutward: hitsFor(new THREE.Vector3(1, 0, 0)), eastTop, westTop, fogIsExp2: env.fog.isFogExp2 === true,
  smallD: worldShell({ rand: mk(), mood: 'day', bounds: 150 }).fog.density, bareD: worldShell({ rand: mk(), mood: 'day' }).fog.density,
  bigFarT: T(big.fog.density, 2400), overFarT: T(over.fog.density, 2400), unclampedFarT: T(env.fog.density, 2400),
  explicitWins: worldShell({ rand: mk(), mood: 'day', bounds: 1200, fogDensity: 0.01 }).fog.density === 0.01 }));
"""


@pytest.fixture(scope="module")
def shell() -> dict:
    return _measure(_SHELL)


def test_no_outward_ray_escapes_into_void(shell):
    for hits in shell["outward"]:
        assert "HorizonRidge" in hits and "SkyGradient" in hits, shell["outward"]
    assert "SkyGradient" in shell["bareOutward"], shell["bareOutward"]   # the dome alone closes the sky


def test_the_ridge_is_opt_in_and_can_be_shaped_by_sector(shell):
    """A library default no longer rings every exterior with the same 360-degree crest."""
    assert shell["ridgeOffByDefault"]
    assert shell["eastTop"] <= 1e-6 < shell["westTop"], (shell["eastTop"], shell["westTop"])
    assert shell["westTop"] == pytest.approx(4000 * 0.1, rel=1e-6)


def test_fog_scales_with_the_world_it_wraps(shell):
    assert shell["smallD"] == pytest.approx(0.0018) and shell["bareD"] == pytest.approx(0.0018)
    assert shell["bigFarT"] >= 0.30 and shell["overFarT"] >= 0.30
    assert shell["unclampedFarT"] < 0.01 and shell["explicitWins"]


_SUN = """
import * as THREE from 'three';
import { sunRig } from './lib/environment.js';
const out = {};
const half = THREE.DataUtils.fromHalfFloat;
const sample = (rig, dir) => {
  const img = rig.envTex.image;
  const u = Math.atan2(dir.z, dir.x) / (2 * Math.PI) + 0.5;
  const v = Math.asin(Math.max(-1, Math.min(1, dir.y))) / Math.PI + 0.5;
  const x = Math.min(img.width - 1, Math.floor(u * img.width)), y = Math.min(img.height - 1, Math.floor(v * img.height));
  const i = (y * img.width + x) * 4;
  return [half(img.data[i]), half(img.data[i + 1]), half(img.data[i + 2])];
};
const lum = (c) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
for (const mood of ['day', 'golden', 'night', 'overcast']) {
  const rig = sunRig({ mood, bounds: 120, disc: true });
  const lightDir = rig.sun.position.clone().normalize();
  const anti = lightDir.clone().multiply(new THREE.Vector3(-1, 1, -1));
  const img = rig.envTex.image;
  out[mood] = { envW: img.width, envH: img.height, equirect: rig.envTex.mapping === THREE.EquirectangularReflectionMapping,
    halfFloat: rig.envTex.type === THREE.HalfFloatType, linear: rig.envTex.colorSpace === THREE.LinearSRGBColorSpace,
    dot: lightDir.dot(rig.sunDisc.position.clone().normalize()), lightIsSun: lightDir.dot(rig.lightDir) > 0.999,
    lumSun: lum(sample(rig, lightDir)), lumAway: lum(sample(rig, anti)),
    zenith: sample(rig, new THREE.Vector3(0, 1, 0)), horizon: sample(rig, new THREE.Vector3(1, 0.01, 0)), nadir: sample(rig, new THREE.Vector3(0, -1, 0)),
    fill: rig.fill.intensity, sun: rig.sun.intensity, castShadow: rig.sun.castShadow, shadowMap: rig.sun.shadow.mapSize.x,
    bias: rig.sun.shadow.bias, normalBias: rig.sun.shadow.normalBias,
    frustumRight: rig.sun.shadow.camera.right, night: rig.night, moodName: rig.mood };
}
out.nightFloor = sunRig({ mood: 'night', fill: 0.2 }).fill.intensity;
// A set sun is night: moon opposite, up 30-55 deg; sunDir still the authored sun.
const set = sunRig({ azimuth: 215, elevation: -20 });
const moon = set.sun.position.clone().normalize();
out.set = { night: set.night, mood: set.mood, sunY: set.sunDir.y, moonY: moon.y, moonDot: moon.dot(set.moonDir),
  opposite: Math.atan2(moon.z, moon.x) * 180 / Math.PI, discName: set.sunDisc.name, discOnMoon: set.sunDisc.position.clone().normalize().dot(moon),
  lightDirIsMoon: set.lightDir.dot(moon) > 0.999, envZenith: sample(set, new THREE.Vector3(0, 1, 0)), envMoon: lum(sample(set, moon)),
  envAway: lum(sample(set, moon.clone().multiply(new THREE.Vector3(-1, 1, -1)))), sunIntensity: set.sun.intensity };
// The azimuth convention env.js's SUN_AZIMUTH_DEG comment states (0 = +X, 90 = +Z):
// the harness's camera fitter converts from it, so it must not drift.
out.axes = [0, 90].map((a) => sunRig({ azimuth: a, elevation: 0 }).sunDir.toArray().map((v) => Math.round(v * 1e6) / 1e6));
console.log(JSON.stringify(out));
"""

_MOODS = ("day", "golden", "night", "overcast")


@pytest.fixture(scope="module")
def sun() -> dict:
    return _measure(_SUN)


def test_azimuth_zero_is_plus_x_and_ninety_is_plus_z(sun):
    assert sun["axes"] == [[1, 0, 0], [0, 0, 1]]


def test_the_baked_env_glow_agrees_with_the_shadow_sun(sun):
    for mood in _MOODS:
        assert sun[mood]["lumSun"] > sun[mood]["lumAway"] * 1.15, (mood, sun[mood])


def test_the_night_rig_is_dark_but_lit(sun):
    """Measured on ACES/exposure 1.0."""
    n = sun["night"]
    assert n["sun"] >= 2.0 and n["fill"] >= 0.8 and n["night"], n
    assert sun["nightFloor"] >= 0.55
    zl = 0.2126 * n["zenith"][0] + 0.7152 * n["zenith"][1] + 0.0722 * n["zenith"][2]
    assert zl < 0.03 and n["zenith"][2] > n["zenith"][0], n["zenith"]


def test_the_shadow_sun_is_fitted_and_biased(sun):
    for mood in _MOODS:
        m = sun[mood]
        assert m["castShadow"] and m["frustumRight"] == 120
        # Acne is cured along the normal by ~a texel, not by pushing the whole
        # depth back — a constant bias big enough for a coarse map is what
        # detaches a shadow from the foot of the thing casting it.
        assert abs(m["bias"]) <= 0.0002, m
        assert 0 < m["normalBias"] <= 2 * 120 / m["shadowMap"] * 1.001, m


def test_a_set_sun_is_night_with_a_moon_opposite(sun):
    s = sun["set"]
    assert s["night"] and s["mood"] == "night" and s["sunY"] < 0
    assert 0.5 < s["moonY"] < 0.82 and s["moonDot"] > 0.999
    assert abs(((s["opposite"] - 35) + 180) % 360 - 180) < 1.0, s["opposite"]
    assert s["discName"] == "MoonDisc" and s["discOnMoon"] > 0.999 and s["lightDirIsMoon"]
    assert s["envMoon"] > s["envAway"] * 1.15
    assert s["envZenith"][2] > s["envZenith"][0] and s["sunIntensity"] >= 2.0


def test_the_sky_dome_keeps_or_sets_the_background_the_gate_reads():
    """D112."""
    out = _measure("""
import * as THREE from 'three';
import { makeSky } from './lib/sky.js';
const set = new THREE.Scene();
const mine = new THREE.Color(0x336699);
set.background = mine;
makeSky(set, { elevationDeg: 20 });
const unset = new THREE.Scene();
makeSky(unset, { elevationDeg: 20 });
const night = new THREE.Scene();
makeSky(night, { elevationDeg: -15 });
const hsl = (c) => c.getHSL({});
console.log(JSON.stringify({ kept: set.background === mine, unsetIsColor: !!(unset.background && unset.background.isColor),
  day: unset.background.toArray(), dayL: hsl(unset.background).l, nightL: hsl(night.background).l }));
""")
    assert out["kept"], "a background the caller set survives"
    assert out["unsetIsColor"], "an unset background becomes a colour the census reports"
    assert all(0.0 <= v <= 1.0 for v in out["day"]) and out["nightL"] < out["dayL"]
