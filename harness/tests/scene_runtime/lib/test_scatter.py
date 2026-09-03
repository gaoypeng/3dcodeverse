"""scatter.js: instances ON the surface, exact count, deterministic, GRADED.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_scatter_lib.py).  Their scaffolding is dropped; the placement
claims are kept verbatim in spirit — MeshSurfaceSampler's two traps
(LOCAL-space samples used raw, and its `Math.random` default) plus slope
gating on TRUE face normals rather than the smoothed vertex normals that
under-report a slope.

What this port ADDED, and the reason each assertion exists:

`colorJitter` shipped as `setScalar(1 - colorJitter * rand())` — a
one-sided GREY multiplier.  Measured on 600 instances of a 0x537a36
tuft at colorJitter 0.45: mean multiplier 0.7783 (the authored albedo
was silently 22 % darker than the colour the author picked, and the
field's mean is never recoverable by choosing a brighter one, because
the bias scales with the jitter) and hue spread 0.000 degrees across
the whole field.  A thousand copies varying only in brightness is the
wallpaper read the option exists to break.  So the multiplier is now
CENTRED on the authored albedo and carries a warm/cool hue axis, and
the three properties that keeps it honest are pinned here:

  * centred — the field mean returns the colour that was authored;
  * hue actually moves, and the axis is capped by the albedo's own
    chroma: a near-neutral colour has no hue to nudge, so an unbounded
    push INVENTS one and a grey field comes back half orange and half
    blue (measured on 0x808080 at full jitter: peak saturation 0.888
    with the cap removed, 0.113 with it);
  * the product stays inside the scene contract's 0.02..0.82 albedo
    band at any jitter, so a pale prototype cannot blow out under ACES
    and a dark one cannot punch a black hole in the field.

And one workflow law: colour must not move the FIELD.  Hue rides its
own PRNG stream and the value draw is taken whether or not it is used,
so turning colorJitter on to break a wallpaper read cannot also re-roll
every position out from under a layout that was already approved.

`alignToNormal` also takes a NUMBER now: real grass on a bank leans
downhill without lying flat on it, and true/false still mean 1/0.
"""

from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node


def _measure(script: str) -> dict:
    return measure(script, ("scatter.js",))


_PLACEMENT = """
import * as THREE from 'three';
import { displaceY } from './lib/noise.js';
import { scatter } from './lib/scatter.js';

const makeTerrain = () => {
  const g = new THREE.PlaneGeometry(160, 160, 96, 96);
  g.rotateX(-Math.PI / 2);
  displaceY(g, 12, 0.03, 7);
  return new THREE.Mesh(g, new THREE.MeshStandardMaterial());
};

// Terrain nested in a transformed parent: scatter must bake the FULL
// world transform, not hand back local-space sample points.
const setup = () => {
  const terrain = makeTerrain();
  terrain.position.set(40, 5, -20);
  terrain.rotation.y = 0.4;
  const parent = new THREE.Group();
  parent.position.set(-10, 2, 3);
  parent.add(terrain);
  new THREE.Group().add(parent);
  parent.parent.updateMatrixWorld(true);
  return terrain;
};

const cone = new THREE.ConeGeometry(0.3, 1.2, 6);
cone.translate(0, 0.6, 0);
const mat = new THREE.MeshStandardMaterial();

const t1 = setup();
const im = scatter(t1, cone, mat, 300, { seed: 11 });
const countExact = im.count === 300 && im.isInstancedMesh === true;

// On-surface: the vertical ray through each instance origin must hit
// the terrain at the origin's own height.
const ray = new THREE.Raycaster();
const down = new THREE.Vector3(0, -1, 0);
const p = new THREE.Vector3();
const q = new THREE.Quaternion();
const s = new THREE.Vector3();
const M = new THREE.Matrix4();
let maxGap = 0;
let sMin = 1e9, sMax = -1e9, nonUniform = 0;
for (let i = 0; i < im.count; i++) {
  im.getMatrixAt(i, M);
  M.decompose(p, q, s);
  ray.set(new THREE.Vector3(p.x, p.y + 80, p.z), down);
  const hits = ray.intersectObject(t1, false);
  if (!hits.length) { maxGap = 1e9; break; }
  maxGap = Math.max(maxGap, Math.abs(hits[0].point.y - p.y));
  sMin = Math.min(sMin, s.x);
  sMax = Math.max(sMax, s.x);
  if (Math.abs(s.x - s.y) > 1e-6 || Math.abs(s.x - s.z) > 1e-6) nonUniform++;
}

// Determinism: fresh identical setup, same seed -> identical matrices;
// different seed -> a different field.
const im2 = scatter(setup(), cone, mat, 300, { seed: 11 });
const im3 = scatter(setup(), cone, mat, 300, { seed: 12 });
let maxSame = 0, maxOther = 0;
const a1 = im.instanceMatrix.array, a2 = im2.instanceMatrix.array,
      a3 = im3.instanceMatrix.array;
for (let i = 0; i < a1.length; i++) {
  maxSame = Math.max(maxSame, Math.abs(a1[i] - a2[i]));
  maxOther = Math.max(maxOther, Math.abs(a1[i] - a3[i]));
}

// Colour must not move the FIELD: same seed, jitter off vs on.
const imPlain = scatter(setup(), cone, mat, 300, { seed: 11 });
const imTinted = scatter(setup(), cone, mat, 300,
    { seed: 11, colorJitter: 0.6 });
let maxShift = 0;
const b1 = imPlain.instanceMatrix.array, b2 = imTinted.instanceMatrix.array;
for (let i = 0; i < b1.length; i++) {
  maxShift = Math.max(maxShift, Math.abs(b1[i] - b2[i]));
}

// alignToNormal on a tilted plane: instance +Y must match the plane's
// world normal; without the flag it must stay world-up despite the
// tilt; a NUMBER must land part way between the two.
const TILT = 0.5;
const tilt = () => {
  const g = new THREE.PlaneGeometry(40, 40, 4, 4);
  g.rotateX(-Math.PI / 2);
  const mSlope = new THREE.Mesh(g, new THREE.MeshStandardMaterial());
  mSlope.rotation.z = TILT;
  mSlope.updateMatrixWorld(true);
  return mSlope;
};
const expectN = new THREE.Vector3(0, 1, 0).applyEuler(tilt().rotation);
const imA = scatter(tilt(), cone, mat, 60, { alignToNormal: true, seed: 2 });
const imU = scatter(tilt(), cone, mat, 60, { seed: 2 });
const imH = scatter(tilt(), cone, mat, 60, { alignToNormal: 0.5, seed: 2 });
const basisY = new THREE.Vector3();
let minDotAligned = 1, minDotUpright = 1;
let halfMin = Math.PI, halfMax = -Math.PI;
for (let i = 0; i < 60; i++) {
  imA.getMatrixAt(i, M);
  basisY.setFromMatrixColumn(M, 1).normalize();
  minDotAligned = Math.min(minDotAligned, basisY.dot(expectN));
  imU.getMatrixAt(i, M);
  basisY.setFromMatrixColumn(M, 1).normalize();
  minDotUpright = Math.min(minDotUpright, basisY.y);
  imH.getMatrixAt(i, M);
  basisY.setFromMatrixColumn(M, 1).normalize();
  const ang = Math.acos(Math.min(1, basisY.y));   // lean from world up
  halfMin = Math.min(halfMin, ang);
  halfMax = Math.max(halfMax, ang);
}

// maxSlopeDeg against MEASURED face normals on steep fBm terrain
// (identity transform so raycast face normals are world-space).
const steepG = new THREE.PlaneGeometry(160, 160, 96, 96);
steepG.rotateX(-Math.PI / 2);
displaceY(steepG, 26, 0.05, 7);
const steepT = new THREE.Mesh(steepG, new THREE.MeshStandardMaterial());
steepT.updateMatrixWorld(true);
const measureMinNy = (mesh) => {
  let minNy = 1;
  for (let i = 0; i < mesh.count; i++) {
    mesh.getMatrixAt(i, M);
    M.decompose(p, q, s);
    ray.set(new THREE.Vector3(p.x, p.y + 80, p.z), down);
    const hits = ray.intersectObject(steepT, false);
    if (!hits.length) return -1;
    minNy = Math.min(minNy, hits[0].face.normal.y);
  }
  return minNy;
};
const imAll = scatter(steepT, cone, mat, 400, { seed: 5 });
const imFlat = scatter(steepT, cone, mat, 400, { seed: 5, maxSlopeDeg: 25 });

// Cap, zero jitter.
const imCap = scatter(makeTerrain(), cone, mat, 5000, { seed: 1 });
const imCap2 = scatter(makeTerrain(), cone, mat, 5000,
    { seed: 1, maxCount: 50 });
const imRigid = scatter(makeTerrain(), cone, mat, 20,
    { seed: 1, scaleJitter: 0 });
let rigidOk = true;
for (let i = 0; i < 20; i++) {
  imRigid.getMatrixAt(i, M);
  M.decompose(p, q, s);
  if (Math.abs(s.x - 1) > 1e-6) rigidOk = false;
}

console.log(JSON.stringify({
  countExact, maxGap, sMin, sMax, nonUniform,
  maxSame, maxOther, maxShift,
  minDotAligned, minDotUpright,
  halfMinDeg: halfMin * 180 / Math.PI, halfMaxDeg: halfMax * 180 / Math.PI,
  tiltDeg: TILT * 180 / Math.PI,
  minNyAll: measureMinNy(imAll), minNyFlat: measureMinNy(imFlat),
  flatCount: imFlat.count,
  capCount: imCap.count, cap2Count: imCap2.count, rigidOk,
  cos25: Math.cos(25 * Math.PI / 180),
}));
"""


_GRADE = """
import * as THREE from 'three';
import { scatter } from './lib/scatter.js';

const g = new THREE.PlaneGeometry(80, 80, 24, 24);
g.rotateX(-Math.PI / 2);
const surf = new THREE.Mesh(g, new THREE.MeshStandardMaterial());
surf.updateMatrixWorld(true);
const proto = new THREE.ConeGeometry(0.2, 0.5, 5);
proto.translate(0, 0.25, 0);

const N = 600;
const circStd = (hs) => {
  const c = hs.reduce((s, h) => s + Math.cos(h), 0) / hs.length;
  const s = hs.reduce((s2, h) => s2 + Math.sin(h), 0) / hs.length;
  const R = Math.max(Math.hypot(c, s), 1e-9);
  return Math.sqrt(-2 * Math.log(R)) * 180 / Math.PI;
};

// `hex` is a hue-carrying albedo (foliage) or a near-neutral one (stone).
const field = (hex, jitter, opts = {}) => {
  const mat = new THREE.MeshStandardMaterial({ color: hex });
  const im = scatter(surf, proto, mat, N, Object.assign(
      { seed: 3, colorJitter: jitter }, opts));
  if (!im.instanceColor) return { hasTint: false };
  const a = im.instanceColor.array, col = new THREE.Color(), hsl = {};
  const hs = [];
  let lumSum = 0, peak = 0, floor = 1e9, satMax = 0;
  for (let i = 0; i < N; i++) {
    const R = a[i * 3] * mat.color.r;
    const G = a[i * 3 + 1] * mat.color.g;
    const B = a[i * 3 + 2] * mat.color.b;
    col.setRGB(R, G, B);
    col.getHSL(hsl);
    hs.push(hsl.h * Math.PI * 2);
    satMax = Math.max(satMax, (Math.max(R, G, B) - Math.min(R, G, B))
        / Math.max(Math.max(R, G, B), 1e-9));
    lumSum += 0.2126 * R + 0.7152 * G + 0.0722 * B;
    peak = Math.max(peak, R, G, B);
    floor = Math.min(floor, Math.max(R, G, B));
  }
  const baseLum = 0.2126 * mat.color.r + 0.7152 * mat.color.g
      + 0.0722 * mat.color.b;
  return {
    hasTint: true,
    lumRatio: (lumSum / N) / baseLum,   // 1.0 == the authored albedo
    hueStd: circStd(hs),
    satMax, peak, floor,
  };
};

console.log(JSON.stringify({
  leaf: field(0x537a36, 0.45),
  stone: field(0x7d786f, 0.45),
  leafNoHue: field(0x537a36, 0.45, { hueJitter: 0 }),
  // extremes: the albedo band must hold at the loudest legal jitter,
  // and a flat grey at full jitter must not invent a hue wheel
  pale: field(0xf2f2f2, 1.0),
  dark: field(0x0a0a0a, 1.0),
  greyLoud: field(0x808080, 1.0),
  off: field(0x537a36, 0),
}));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of the placement probe, shared by its tests."""
    return _measure(_PLACEMENT)


@pytest.fixture(scope="module")
def grade() -> dict:
    """One node launch of the colour-grading probe."""
    return _measure(_GRADE)


def test_instances_sit_on_the_surface_in_world_space(probe):
    """Every instance origin lies ON the surface even under a
    transformed parent — raw sampler points are LOCAL-space, and used
    raw they are the floating/buried cover the census red-flags."""
    assert probe["countExact"], "count wrong or not an InstancedMesh"
    assert probe["maxGap"] < 1e-3, f"instances off the surface: {probe['maxGap']}"


def test_same_seed_is_bit_identical_and_seed_moves_the_field(probe):
    """Same seed must reproduce exact matrices (fix rounds re-render
    untouched zones pixel-identically; Math.random would break this
    silently) — and a different seed must actually move instances."""
    assert probe["maxSame"] == 0, f"same seed diverged by {probe['maxSame']}"
    assert probe["maxOther"] > 1, f"seed changes nothing: {probe['maxOther']}"


def test_colour_jitter_does_not_move_the_field(probe):
    """Grading is not layout.  Turning colorJitter on to break a
    wallpaper read must leave every matrix bit-identical, or every
    colour tweak silently re-rolls a field that was already approved."""
    assert probe["maxShift"] == 0, (
        f"colorJitter moved the instances by {probe['maxShift']}")


def test_align_to_normal_tilts_leans_or_stays_upright(probe):
    """alignToNormal:true lays grass/rocks onto the slope; the default
    keeps trees/posts world-vertical (each mode's +Y exposes the other's
    failure).  A NUMBER leans part way — 0.5 on a 28.6 deg bank must
    land near half of it, not at either end."""
    m = probe
    assert m["minDotAligned"] > 0.999, f"not aligned: {m['minDotAligned']}"
    assert m["minDotUpright"] > 0.999, f"not upright: {m['minDotUpright']}"
    half = m["tiltDeg"] * 0.5
    assert abs(m["halfMinDeg"] - half) < 1.0, m
    assert abs(m["halfMaxDeg"] - half) < 1.0, m


def test_max_slope_deg_gates_on_true_face_normals(probe):
    """maxSlopeDeg must gate on the TRUE face normal: interpolated
    vertex normals let 60-degree faces through a 25-degree filter (the
    measured bug this pins)."""
    m = probe
    assert m["minNyAll"] < 0.5, f"terrain not steep, test is void: {m}"
    assert m["minNyFlat"] >= m["cos25"] - 1e-3, (
        f"steep faces passed the filter: {m['minNyFlat']}")
    assert m["flatCount"] >= 350, f"rejection starved: {m['flatCount']}"


def test_count_cap_and_scale_jitter(probe):
    """The overdraw cap (1000 default, the one measured scatter
    footgun) must clamp, maxCount must override it, scaleJitter must
    spread uniformly inside 1 +- jitter and 0 must mean rigid."""
    m = probe
    assert m["capCount"] == 1000, f"default cap: {m['capCount']}"
    assert m["cap2Count"] == 50, f"maxCount override: {m['cap2Count']}"
    assert m["sMin"] >= 0.699 and m["sMax"] <= 1.301, m
    assert m["sMax"] - m["sMin"] > 0.3, f"no scale spread: {m}"
    assert m["nonUniform"] == 0, "scale jitter is non-uniform"
    assert m["rigidOk"], "scaleJitter 0 still scaled"


def test_colour_jitter_is_centred_on_the_authored_albedo(grade):
    """The shipped `1 - colorJitter * rand()` could only darken: at
    jitter 0.45 the field's mean luminance measured 0.778 of the colour
    the author picked, and the bias grows with the jitter.  A field's
    mean must BE the authored albedo."""
    for name in ("leaf", "stone", "leafNoHue"):
        assert grade[name]["hasTint"], f"{name}: no instanceColor written"
        assert abs(grade[name]["lumRatio"] - 1.0) < 0.03, (
            f"{name} field mean is {grade[name]['lumRatio']:.3f} of the "
            "authored albedo")
    assert not grade["off"]["hasTint"], "colorJitter 0 still wrote colours"


def test_colour_jitter_varies_hue_not_only_brightness(grade):
    """Measured on the shipped version: 0.000 degrees of hue across 600
    copies.  A thousand leaves at one hue IS the wallpaper read the
    option exists to break, so the axis must actually move."""
    assert grade["leaf"]["hueStd"] > 3.5, (
        f"foliage hue is flat: {grade['leaf']['hueStd']:.3f} deg")
    assert grade["stone"]["hueStd"] > 1.0, (
        f"stone hue is flat: {grade['stone']['hueStd']:.3f} deg")
    assert grade["leafNoHue"]["hueStd"] < 0.01, (
        f"hueJitter 0 still moved hue: {grade['leafNoHue']['hueStd']}")


def test_hue_axis_is_capped_by_the_albedos_own_saturation(grade):
    """A near-neutral albedo has no hue to nudge — the axis INVENTS one,
    and hue angle stops meaning anything (a pure grey at full jitter
    measures 193 deg of hue spread while being, correctly, still grey).
    So the guard is on CHROMA: whatever the jitter, a grey prototype may
    come back a warm or cool grey, never an orange one."""
    assert grade["greyLoud"]["satMax"] < 0.16, (
        "flat grey at full jitter turned chromatic: peak saturation "
        f"{grade['greyLoud']['satMax']:.3f}")
    # 0x7d786f carries 0.225 of chroma; the warm tail measures 0.453,
    # so the axis roughly doubles it at the extreme and no further.
    assert grade["stone"]["satMax"] < 0.50, (
        f"stone over-tinted: {grade['stone']['satMax']:.3f}")
    assert grade["leaf"]["satMax"] > grade["stone"]["satMax"], (
        "a saturated albedo must be allowed a WIDER axis than a neutral "
        f"one: {grade['leaf']['satMax']:.3f} vs {grade['stone']['satMax']:.3f}")


def test_jitter_holds_the_albedo_band_at_any_setting(grade):
    """The scene contract's non-emissive band is 0.02..0.82.  A pale
    prototype at full jitter must not multiply past the ceiling (it
    blows out under ACES before the sun lands on it) and a dark one
    must not fall through the floor (a black hole in the field)."""
    for name in ("leaf", "stone", "pale", "dark"):
        assert grade[name]["peak"] <= 0.821, (
            f"{name} peak albedo {grade[name]['peak']:.4f} over the ceiling")
        assert grade[name]["floor"] >= 0.0199, (
            f"{name} floor albedo {grade[name]['floor']:.4f} under the floor")
    assert grade["pale"]["peak"] > 0.5, "pale test is void, nothing near the cap"
