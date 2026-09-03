"""sky.js — the graded Preetham dome: sun-coherent, non-competing, graded for OUR renderer.

Ported 2026-09-01 from the scene_multifile_graphics reference (tests/test_sky_lib.py); the
render-wrapper assertions are theirs.  Added: the grade itself (three's Sky.js gamma-encodes
before the tone map and rendered (240,244,246) on ACES/exposure 1.0 — measured), the night
blend, and the CPU radiance model the environment bake shares.
"""
from __future__ import annotations

import math

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "sky.js", "environment.js")

_PROBE = """
import * as THREE from 'three';
import { makeSky, skyRadiance, SKY_GRADE, nightAmount, afterglowAmount, lowSunAmount } from './lib/sky.js';
import { sunRig, worldShell } from './lib/environment.js';
const out = {};

const s1 = new THREE.Scene();
s1.background = new THREE.Color(0x88aacc);
const r1 = makeSky(s1);
const u = r1.sky.material.uniforms;
const el = 15 * Math.PI / 180, az = 135 * Math.PI / 180;
const expect = new THREE.Vector3(Math.cos(el) * Math.cos(az), Math.sin(el), Math.cos(el) * Math.sin(az));
out.def = { inScene: s1.children.includes(r1.sky), bgNull: s1.background === null, isSky: r1.sky.isSky === true,
  len: r1.sunDir.length(), dotMath: r1.sunDir.dot(expect), sunY: r1.sunDir.y,
  turbidity: u.turbidity.value, rayleigh: u.rayleigh.value, mieC: u.mieCoefficient.value, mieG: u.mieDirectionalG.value,
  scale: r1.sky.scale.x, uniformDot: u.sunPosition.value.clone().normalize().dot(r1.sunDir), renderOrder: r1.sky.renderOrder,
  gain: u.uSkyGain.value, gamma: u.uSkyGamma.value, night: u.uNight.value, clouds: u.cloudCoverage ? u.cloudCoverage.value : 0 };

// The patch actually rewrites three's transfer line (fails loudly if Sky.js moves it).
const shader = { uniforms: {}, vertexShader: 'void main() {}',
  fragmentShader: 'uniform float mieDirectionalG;\\nvoid main() {\\n\\t\\t\\tvec3 retColor = pow( texColor, vec3( 1.0 / ( 1.2 + ( 1.2 * vSunfade ) ) ) );\\n}' };
r1.sky.material.onBeforeCompile(shader);
out.patch = { gainIn: /uSkyGain \\* pow\\( texColor/.test(shader.fragmentShader),
  nightIn: /mix\\( retColor, astraNight, uNight \\)/.test(shader.fragmentShader),
  dither: /gl_FragCoord/.test(shader.fragmentShader), uniformsBound: !!shader.uniforms.uNightZenith,
  key: r1.sky.material.customProgramCacheKey() };

const s2 = new THREE.Scene();
const m2 = makeSky(s2, { elevationDeg: 8, azimuthDeg: 35 });
const rig2 = sunRig({ elevation: 8, azimuth: 35, bounds: 100 });
out.convention = { dot: m2.sunDir.dot(rig2.sun.position.clone().normalize()),
  rigSunDirDot: rig2.sunDir.dot(rig2.sun.position.clone().normalize()), rigSunDirLen: rig2.sunDir.length() };

const s3 = new THREE.Scene();
const rig3 = sunRig({ mood: 'golden', bounds: 100 });
const m3 = makeSky(s3, { rig: rig3 });
out.rig = { dot: m3.sunDir.dot(rig3.sunDir), uniformDot: m3.sky.material.uniforms.sunPosition.value.clone().normalize().dot(rig3.sunDir),
  discHidden: rig3.sunDisc.visible === false };

const s4 = new THREE.Scene();
const v = new THREE.Vector3(3, 4, 0);
const m4 = makeSky(s4, { sunDir: v });
out.vec = { len: m4.sunDir.length(), dot: m4.sunDir.dot(v.clone().normalize()), inputKept: v.length() === 5 };

const s5 = new THREE.Scene();
const shell = worldShell({ rand: () => 0.5 });
s5.add(shell.group);
const m5 = makeSky(s5, { elevationDeg: 20 });
out.layer = { gradient: shell.group.getObjectByName('SkyGradient').renderOrder, sky: m5.sky.renderOrder,
  ridge: shell.group.getObjectByName('HorizonRidge').renderOrder };

// NIGHT: a rig with the sun set keeps its moon disc and blends the night gradient in.
const s6 = new THREE.Scene();
const rig6 = sunRig({ azimuth: 215, elevation: -20 });
const m6 = makeSky(s6, { rig: rig6 });
out.night = { amount: m6.sky.material.uniforms.uNight.value, moonKept: rig6.sunDisc.visible !== false,
  sunBelow: m6.sunDir.y < 0, exposureOpt: makeSky(new THREE.Scene(), { exposure: 1, contrast: 1 }).sky.material.uniforms.uSkyGain.value };

// Radiance model: the numbers the environment bake shares with the dome.
const sun = new THREE.Vector3(Math.cos(38 * Math.PI / 180) * Math.cos(215 * Math.PI / 180), Math.sin(38 * Math.PI / 180), Math.cos(38 * Math.PI / 180) * Math.sin(215 * Math.PI / 180));
const dir = (elDeg, azDeg) => new THREE.Vector3(Math.cos(elDeg * Math.PI / 180) * Math.cos(azDeg * Math.PI / 180), Math.sin(elDeg * Math.PI / 180), Math.cos(elDeg * Math.PI / 180) * Math.sin(azDeg * Math.PI / 180));
const zen = skyRadiance(dir(89.9, 0), sun), hor = skyRadiance(dir(2, 0), sun);
const sunSide = skyRadiance(dir(10, 215), sun), antiSide = skyRadiance(dir(10, 35), sun);
const nightSun = dir(-20, 215);
const nz = skyRadiance(dir(89.9, 0), nightSun), nh = skyRadiance(dir(2, 0), nightSun);
const goldSun = dir(6, 215);
const goldSunSide = skyRadiance(dir(8, 215), goldSun), goldAnti = skyRadiance(dir(8, 35), goldSun);
out.model = { zen, hor, sunSide, antiSide, nz, nh, goldSunSide, goldAnti,
  amounts: { n0: nightAmount(0.2), n1: nightAmount(-0.3), g: afterglowAmount(-0.06), gDeep: afterglowAmount(-0.4), low: lowSunAmount(0.05), high: lowSunAmount(0.6) },
  grade: { gain: SKY_GRADE.gain, gamma: SKY_GRADE.gamma } };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    return measure(_PROBE, _LIBS)


def test_defaults_are_the_battery_graded_atmosphere_plus_our_grade(probe):
    m = probe["def"]
    assert m["isSky"]
    assert abs(m["turbidity"] - 4.5) < 1e-9 and abs(m["rayleigh"] - 1.6) < 1e-9
    assert abs(m["mieC"] - 0.0035) < 1e-12 and abs(m["mieG"] - 0.8) < 1e-9
    assert m["scale"] == 4500 and m["clouds"] == 0
    # The renderer grade: three's Sky at gain 1 is a white card on ACES/exposure 1.0.
    assert m["gain"] == probe["model"]["grade"]["gain"] < 0.6, m
    assert m["gamma"] == probe["model"]["grade"]["gamma"] > 1.2, m
    assert m["night"] == 0


def test_the_grade_patch_rewrites_the_transfer_line(probe):
    p = probe["patch"]
    assert p["gainIn"] and p["nightIn"] and p["dither"] and p["uniformsBound"], p
    assert p["key"] == "astra:sky-grade"


def test_sun_dir_is_normalized_and_matches_the_az_el_math(probe):
    m = probe["def"]
    assert abs(m["len"] - 1) < 1e-9 and m["dotMath"] > 1 - 1e-9
    assert abs(m["sunY"] - math.sin(math.radians(15))) < 1e-9
    assert m["uniformDot"] > 1 - 1e-9


def test_the_dome_owns_the_backdrop(probe):
    assert probe["def"]["inScene"] and probe["def"]["bgNull"]


def test_sky_and_sun_rig_share_one_direction_convention(probe):
    m = probe["convention"]
    assert m["dot"] > 1 - 1e-9 and m["rigSunDirDot"] > 1 - 1e-9 and abs(m["rigSunDirLen"] - 1) < 1e-9


def test_rig_delegation_slaves_the_dome_and_hides_the_flat_disc(probe):
    m = probe["rig"]
    assert m["dot"] > 1 - 1e-9 and m["uniformDot"] > 1 - 1e-9 and m["discHidden"]


def test_explicit_sun_dir_is_normalized_and_input_untouched(probe):
    m = probe["vec"]
    assert abs(m["len"] - 1) < 1e-9 and m["dot"] > 1 - 1e-9 and m["inputKept"]


def test_the_dome_layers_between_world_shell_gradient_and_ridge(probe):
    m = probe["layer"]
    assert m["gradient"] < m["sky"] < m["ridge"], m


def test_a_set_sun_blends_the_night_gradient_and_keeps_the_moon(probe):
    m = probe["night"]
    assert m["sunBelow"] and m["amount"] == 1.0 and m["moonKept"]
    assert m["exposureOpt"] == 1, "the ungraded dome stays reachable"


def test_the_radiance_model_is_a_blue_gradient_brighter_toward_the_sun(probe):
    m = probe["model"]
    zen, hor = m["zen"], m["hor"]
    lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]  # noqa: E731
    assert zen[2] > zen[0] * 2, "zenith is blue"
    assert lum(hor) > lum(zen) * 2, "horizon brighter than zenith"
    assert lum(hor) < 1.6, "horizon not blown (linear, pre-ACES)"
    assert lum(m["sunSide"]) > lum(m["antiSide"]) * 1.3, "haze brightens toward the sun"
    # Night: dark, blue, horizon above zenith; both in the fog family.
    assert lum(m["nz"]) < 0.03 and lum(m["nh"]) < 0.1 and m["nz"][2] > m["nz"][0]
    assert lum(m["nh"]) > lum(m["nz"])
    # Golden hour warms toward the sun: red/blue ratio higher sun-side than opposite.
    assert m["goldSunSide"][0] / m["goldSunSide"][2] > m["goldAnti"][0] / m["goldAnti"][2]
    a = m["amounts"]
    assert a["n0"] == 0 and a["n1"] == 1 and a["g"] > 0.5 and a["gDeep"] == 0
    assert a["low"] > 0.9 and a["high"] == 0
