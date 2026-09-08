"""environment.js — worldShell, makeOutskirts and the sunRig package, measured under node.

Ported 2026-09-01 from the scene_multifile_graphics reference (tests/test_environment_lib.py).
Changed for OUR renderer: the environment is a linear HALF-FLOAT bake from the graded sky
model (sampled here through DataUtils.fromHalfFloat), the day horizon is 0xdbe3ea, night is
a brighter moon rig (2.2 / fill 1.0 — the reference's 0.8 / 0.6 measured 30-40 % of the
frame under the dark threshold on ACES exposure 1.0), and a SET SUN (elevation < 0) is
night with a moon opposite the sun.
"""
from __future__ import annotations

import math

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "sky.js", "environment.js")


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


_SHELL = """
import * as THREE from 'three';
import { worldShell } from './lib/environment.js';
const rand = (() => { let s = 9; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
const env = worldShell({ rand, mood: 'day' });
env.group.updateMatrixWorld(true);
const hitsFor = (dir) => new THREE.Raycaster(new THREE.Vector3(0, 1.7, 0), dir.normalize()).intersectObject(env.group, true).map((h) => h.object.name);
const outward = [];
for (let a = 0; a < 8; a++) outward.push(hitsFor(new THREE.Vector3(Math.cos(a / 8 * Math.PI * 2), 0, Math.sin(a / 8 * Math.PI * 2))));
const sky = env.group.getObjectByName('SkyGradient');
const cols = sky.geometry.attributes.color, pos = sky.geometry.attributes.position;
let zenithB = null, horizonB = null;
for (let i = 0; i < pos.count; i++) { if (pos.getY(i) > 3990) zenithB = cols.getZ(i); if (Math.abs(pos.getY(i)) < 40) horizonB = cols.getZ(i); }
let tris = 0;
env.group.traverse((o) => { if (o.isMesh) tris += o.geometry.attributes.position.count / 3; });
const noRidge = worldShell({ rand, mood: 'day', ridge: false });
const T = (rho, d) => Math.exp(-((rho * d) ** 2));
const mk = () => () => 0.5;
const big = worldShell({ rand: mk(), mood: 'day', bounds: 1200 });
const over = worldShell({ rand: mk(), mood: 'overcast', bounds: 1200 });
console.log(JSON.stringify({ outward, fogHex: env.fog.color.getHexString(), zenithB, horizonB, tris: Math.round(tris),
  ridgeOptional: !noRidge.group.getObjectByName('HorizonRidge'), fogIsExp2: env.fog.isFogExp2 === true,
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


def test_the_fog_matches_the_horizon_tint(shell):
    assert shell["fogHex"] == "dbe3ea" and shell["fogIsExp2"]


def test_the_sky_is_a_gradient_not_a_flat_colour(shell):
    assert abs(shell["zenithB"] - shell["horizonB"]) > 0.1, shell


def test_the_shell_is_nearly_free_and_the_ridge_optional(shell):
    assert shell["tris"] < 2000 and shell["ridgeOptional"]


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
// The shadow map follows the FRUSTUM: one fixed size gave every scene
// whatever texel it happened to get (the library default landed at 0.146 m).
out.texel = {};
for (const b of [8, 25, 60, 150, 400, 1200]) {
  const r = sunRig({ bounds: b });
  out.texel[b] = { map: r.sun.shadow.mapSize.x, size: (2 * b) / r.sun.shadow.mapSize.x,
                   normalBias: r.sun.shadow.normalBias };
}
out.nightFloor = sunRig({ mood: 'night', fill: 0.2 }).fill.intensity;
const tinted = sunRig({ mood: 'golden', fillSky: 0x7a5aa0, fillGround: 0x3a2e22 });
out.tinted = { sky: tinted.fill.color.getHex(), ground: tinted.fill.groundColor.getHex(),
  nadir: sample(tinted, new THREE.Vector3(0, -1, 0)), plainNadir: sample(sunRig({ mood: 'golden' }), new THREE.Vector3(0, -1, 0)) };
out.discOptional = sunRig({ mood: 'day', disc: false }).sunDisc === null;
const floors = (o) => { const r = sunRig(o); return { sun: r.sun.intensity, fill: r.fill.intensity }; };
out.floors = { dayAsked: floors({ mood: 'day', intensity: 2.8, fill: 0.65 }), goldenAsked: floors({ mood: 'golden', intensity: 2.0, fill: 0.3 }),
  nightAsked: floors({ mood: 'night', intensity: 0.8, fill: 0.6 }), dayBright: floors({ mood: 'day', intensity: 9.0 }) };
// A set sun is night: moon opposite, up 30-55 deg; sunDir still the authored sun.
const set = sunRig({ azimuth: 215, elevation: -20 });
const moon = set.sun.position.clone().normalize();
out.set = { night: set.night, mood: set.mood, sunY: set.sunDir.y, moonY: moon.y, moonDot: moon.dot(set.moonDir),
  opposite: Math.atan2(moon.z, moon.x) * 180 / Math.PI, discName: set.sunDisc.name, discOnMoon: set.sunDisc.position.clone().normalize().dot(moon),
  lightDirIsMoon: set.lightDir.dot(moon) > 0.999, envZenith: sample(set, new THREE.Vector3(0, 1, 0)), envMoon: lum(sample(set, moon)),
  envAway: lum(sample(set, moon.clone().multiply(new THREE.Vector3(-1, 1, -1)))), sunIntensity: set.sun.intensity };
console.log(JSON.stringify(out));
"""

_MOODS = ("day", "golden", "night", "overcast")


@pytest.fixture(scope="module")
def sun() -> dict:
    return _measure(_SUN)


def test_the_env_texture_is_a_linear_half_float_equirect(sun):
    for mood in _MOODS:
        m = sun[mood]
        assert m["envW"] >= 64 and m["envH"] >= 32 and m["equirect"], m
        assert m["halfFloat"] and m["linear"], m


def test_the_sun_disc_sits_on_the_light_vector(sun):
    for mood in _MOODS:
        assert sun[mood]["dot"] > 0.999 and sun[mood]["lightIsSun"], (mood, sun[mood])


def test_the_baked_env_glow_agrees_with_the_shadow_sun(sun):
    for mood in _MOODS:
        assert sun[mood]["lumSun"] > sun[mood]["lumAway"] * 1.15, (mood, sun[mood])


def test_the_day_env_is_an_hdr_sky_with_a_warm_ground_half(sun):
    """What chrome, water and glass reflect: a blue zenith, a pale (not white)
    horizon in the dome's own family, an HDR sun, and a ground bounce below."""
    d = sun["day"]
    assert d["zenith"][2] > d["zenith"][0] * 2, d["zenith"]
    hl = 0.2126 * d["horizon"][0] + 0.7152 * d["horizon"][1] + 0.0722 * d["horizon"][2]
    assert 0.5 < hl < 2.5, d["horizon"]   # toward the sun's azimuth: ~236 sRGB after ACES, not clipped
    assert d["lumSun"] > 3.0, "the sun in the env is HDR (bloom-friendly), not a clipped band"
    assert d["nadir"][0] > d["nadir"][2], "ground bounce is warm"
    assert d["nadir"][0] < d["horizon"][0]


def test_the_night_rig_is_dark_but_lit(sun):
    """Measured on ACES/exposure 1.0: the reference moon (0.8 / fill 0.6) put the
    ground at 17/255; 2.2 / 1.0 reads moonlit (ground ~45, shade ~14)."""
    n = sun["night"]
    assert n["sun"] >= 2.0 and n["fill"] >= 0.8 and n["night"], n
    assert sun["nightFloor"] >= 0.55
    zl = 0.2126 * n["zenith"][0] + 0.7152 * n["zenith"][1] + 0.0722 * n["zenith"][2]
    assert zl < 0.03 and n["zenith"][2] > n["zenith"][0], n["zenith"]


def test_the_fill_colours_can_be_tinted_and_the_env_ground_half_follows(sun):
    """The cookbook's time-of-day rows tint the hemisphere (cool sky / warm ground per hour);
    the boat-workshop run (loop 8, 2026-09-07) passed `fillSky`/`fillGround` and the rig
    silently ignored them."""
    t = sun["tinted"]
    assert t["sky"] == 0x7A5AA0 and t["ground"] == 0x3A2E22, t
    assert t["nadir"] != t["plainNadir"], "the baked ground bounce follows the override"


def test_the_shadow_sun_is_fitted_and_biased(sun):
    for mood in _MOODS:
        m = sun[mood]
        assert m["castShadow"] and m["frustumRight"] == 120
        # Acne is cured along the normal by ~a texel, not by pushing the whole
        # depth back — a constant bias big enough for a coarse map is what
        # detaches a shadow from the foot of the thing casting it.
        assert abs(m["bias"]) <= 0.0002, m
        assert 0 < m["normalBias"] <= 2 * 120 / m["shadowMap"] * 1.001, m


def test_the_shadow_map_is_sized_to_the_frustum_it_covers(sun):
    """Measured 2026-09-01 on OUR PCFSoftShadowMap (no post chain to hide it):
    a fixed 2048 over the library's default bounds 150 gave 0.146 m/texel and
    the contact shadow of a 1 m sphere came back a 14-texel staircase."""
    t = sun["texel"]
    for b, m in t.items():
        assert 1024 <= m["map"] <= 4096, (b, m)
        assert m["map"] == 2 ** round(math.log2(m["map"])), (b, m)
    # Small worlds are not handed a 4096 map they cannot use...
    assert t["8"]["map"] == 1024 and t["25"]["map"] == 1024, t
    # ...and every world up to the 0.05 m target gets there.
    for b in ("8", "25", "60"):
        assert t[b]["size"] <= 0.05, (b, t[b])
    # Past it the map caps and the texel grows with the world, never the reverse.
    assert t["150"]["map"] == 4096 and t["1200"]["map"] == 4096, t
    assert t["150"]["size"] < t["400"]["size"] < t["1200"]["size"], t
    # The library default (bounds 150) is at least twice as fine as the old fixed map.
    assert t["150"]["size"] <= (2 * 150 / 2048) / 2 + 1e-9, t["150"]


def test_the_disc_can_be_skipped(sun):
    assert sun["discOptional"]


def test_daylight_has_a_sun_floor_because_colour_dies_with_the_light(sun):
    f = sun["floors"]
    assert f["dayAsked"]["sun"] >= 4.5 and f["dayAsked"]["fill"] >= 1.2
    assert f["goldenAsked"]["sun"] >= 4.5
    assert f["nightAsked"]["sun"] == 0.8, "night is legitimately dark: an explicit ask stands"
    assert f["dayBright"]["sun"] == 9.0


def test_a_set_sun_is_night_with_a_moon_opposite(sun):
    s = sun["set"]
    assert s["night"] and s["mood"] == "night" and s["sunY"] < 0
    assert 0.5 < s["moonY"] < 0.82 and s["moonDot"] > 0.999
    assert abs(((s["opposite"] - 35) + 180) % 360 - 180) < 1.0, s["opposite"]
    assert s["discName"] == "MoonDisc" and s["discOnMoon"] > 0.999 and s["lightDirIsMoon"]
    assert s["envMoon"] > s["envAway"] * 1.15
    assert s["envZenith"][2] > s["envZenith"][0] and s["sunIntensity"] >= 2.0


def test_outskirts_meets_the_scene_ground_at_its_seam():
    out = _measure("""
import { makeOutskirts } from './lib/environment.js';
const ground = (x, z) => 2.0 + 0.01 * x;
const g = makeOutskirts({ inner: 150, baseY: 0, heightAt: ground, relief: 30, seed: 9 });
const land = g.children.find((o) => o.name === 'OutskirtsLand');
const pos = land.geometry.attributes.position;
let seamWorst = 0, maxR = 0, maxY = -1e9, farLift = 0, nFar = 0;
for (let i = 0; i < pos.count; i++) {
  const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i), r = Math.hypot(x, z);
  maxR = Math.max(maxR, r); maxY = Math.max(maxY, y);
  if (r < 151) seamWorst = Math.max(seamWorst, Math.abs(y - ground(x, z)));
  if (r > 900) { farLift += y; nFar++; }
}
const copses = g.children.find((o) => o.name === 'OutskirtsCopses');
const a = makeOutskirts({ inner: 100, seed: 4 }).children[0].geometry.attributes.position;
const b = makeOutskirts({ inner: 100, seed: 4 }).children[0].geometry.attributes.position;
const c = makeOutskirts({ inner: 100, seed: 5 }).children[0].geometry.attributes.position;
let same = true, differ = false;
for (let i = 0; i < a.count * 3; i++) { if (a.array[i] !== b.array[i]) same = false; if (a.array[i] !== c.array[i]) differ = true; }
const reach = (o) => { const p = o.children[0].geometry.attributes.position; let r = 0;
  for (let i = 0; i < p.count; i++) r = Math.max(r, Math.hypot(p.getX(i), p.getZ(i))); return r; };
console.log(JSON.stringify({ seamWorst, maxR, maxY, meanFarY: farLift / nFar, meshes: g.children.length,
  copseCount: copses ? copses.count : 0, patched: !!land.material.userData.astraShader, same, differ,
  small: reach(makeOutskirts({ inner: 120, shellRadius: 1200, seed: 2 })), big: reach(makeOutskirts({ inner: 300, shellRadius: 9000, seed: 2 })),
  clamped: reach(makeOutskirts({ inner: 120, shellRadius: 1200, outer: 5000, seed: 2 })),
  shorter: reach(makeOutskirts({ inner: 120, shellRadius: 1200, outer: 600, seed: 2 })) }));
""")
    assert out["seamWorst"] < 0.05, out
    assert 3279 <= out["maxR"] <= 3281 and out["maxY"] < 80 and out["meanFarY"] > 1.0, out
    assert out["meshes"] == 2 and out["copseCount"] > 50 and out["patched"], out
    assert out["same"] and out["differ"]
    assert abs(out["small"] - 984) < 1.5 and abs(out["big"] - 7380) < 2.0
    assert abs(out["clamped"] - 984) < 1.5 and abs(out["shorter"] - 600) < 1.5
