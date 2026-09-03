"""figure.js: a person is arithmetic, and this file is the arithmetic.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_figure_lib.py).  Their scenarios are kept whole — the seat height
determines the pose, the stride covers the ground the route moves, a knee
bends one way, the wrist lands at the crotch — and their last test (the
reference repo's own prompt files must name every asset library) is dropped
because those prompts are not in this harness.

Then the two bugs this port found by LOOKING at a render on our host, each
pinned here so no later edit can put them back:

1. ``MAT.skin({ color: opts.skin })`` with no ``skin`` option handed
   ``Object.assign`` an explicit ``color: undefined``, which OVERWROTE the
   material's own default — so every face, neck, forearm and hand in every
   scene rendered at albedo 1.0.  Measured on the showcase frame: a cold
   (190,204,215) ball where a head should be, blown_frac 0.0035 over the
   figure pixels.  The fix picks a complexion off SKIN_TONES from the same
   random draw that sets the material variant, so a crowd is MIXED without
   multiplying the material cache.
2. ``carry(fig, held)`` parented the held object to the WAIST pivot — which
   already sits at hipHeight — while offsetting it by a figure-root height
   (0.52 h).  The tray landed 0.85 m above the hands: the render showed a
   crate balanced on the carrier's head.  The fix MEASURES the posed hands.

And the grading law the improvement pass added, which is the part a later
"tidy-up" would silently undo: every albedo on a figure stays inside the
non-emissive window (linear 0.02..0.8, dark hair floored off a light-
swallowing void), and the shoe presents no large horizontal facet — a flat
up-face under an open sky is the brightest thing in the frame, and the box
shoe measured sRGB 159 on a 0x3a albedo, i.e. a white plate on a black shoe,
on every figure in the frame.
"""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("figure.js", "materials.js")


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


_PROBE_SEATED = """
import * as THREE from 'three';
import { figure, sit, walk } from './lib/figure.js';

const SEAT = 0.45;
const f = figure({ height: 1.72, rand: (() => { let s = 7;
  return () => (s = (s * 16807) % 2147483647) / 2147483647; })() });
sit(f, SEAT);
f.updateMatrixWorld(true);

const find = (root, name) => {
  let hit = null;
  root.traverse((o) => { if (o.name === name) hit = o; });
  return hit;
};
const world = (name) => new THREE.Vector3()
    .setFromMatrixPosition(find(f, name).matrixWorld);
const box = (name) => new THREE.Box3().setFromObject(find(f, name));

const waist = world('waist');
const kneeL = world('kneeL');
const footL = box('footL');
const head = box('head');
const whole = new THREE.Box3().setFromObject(f);

// Standing figure, for the proportion checks.
const g = figure({ height: 1.72 });
g.updateMatrixWorld(true);
const gbox = new THREE.Box3().setFromObject(g);
const tp = find(g, 'thighL').geometry.parameters;

// A walk cycle must never hyperextend a knee forward.
let worstKnee = -1e9;
for (let i = 0; i < 40; i++) {
  walk(g, i / 40);
  worstKnee = Math.max(worstKnee, g.userData.joints.kneeL.rotation.x,
                       g.userData.joints.kneeR.rotation.x);
}

console.log(JSON.stringify({
  seat: SEAT,
  pelvisY: waist.y,
  kneeY: kneeL.y,
  kneeZ: kneeL.z,
  footMinY: footL.min.y,
  headMinY: head.min.y,
  seatedTop: whole.max.y,
  standHeight: gbox.max.y - gbox.min.y,
  standFeetY: gbox.min.y,
  forward: f.userData.forward,
  thighTopR: tp.radiusBottom,
  thighEndR: tp.radiusTop,
  worstKnee,
}));
"""


@pytest.fixture(scope="module")
def seated() -> dict:
    """One node launch of _PROBE_SEATED, shared by every test that reads it."""
    return _measure(_PROBE_SEATED)


_PROBE_GAIT = """
import * as THREE from 'three';
import { figure, walk, carry } from './lib/figure.js';

const find = (root, name) => {
  let hit = null;
  root.traverse((o) => { if (o.name === name) hit = o; });
  return hit;
};
const world = (f, name) => new THREE.Vector3()
    .setFromMatrixPosition(find(f, name).matrixWorld);

// Implied ground speed of the gait: stride length (from the measured
// hip amplitude) times the stride rate (from hip zero crossings).
const implied = (opts) => {
  const f = figure({ height: 1.72 });
  const L = f.userData.hipHeight;
  const j = f.userData.joints;
  const dt = 0.002;
  let amax = 0;
  const cross = [];
  let prev = null;
  for (let i = 0; i < 5000; i++) {
    const t = i * dt;
    walk(f, t, opts);
    const x = j.hipL.rotation.x;
    amax = Math.max(amax, x);
    if (prev !== null && prev <= 0 && x > 0) cross.push(t);
    prev = x;
  }
  const T = (cross[cross.length - 1] - cross[0]) / (cross.length - 1);
  return { speed: 2 * L * Math.sin(amax) * 2 / T, strideRate: 1 / T };
};
const v09 = implied({ speed: 0.9 });
const v15 = implied({ speed: 1.5 });
const legacy = implied({});

// One full stride at rate 1: joint sweeps and phase relations.
const f = figure({ height: 1.72 });
const j = f.userData.joints;
let armLeg = 0, elbMin = 1e9, elbMax = -1e9;
let heel = null, toeOff = null;
const ys = [];
for (let i = 0; i < 200; i++) {
  walk(f, i / 200, { rate: 1, stride: 0.45 });
  armLeg += j.hipL.rotation.x * j.shoulderL.rotation.x;
  elbMin = Math.min(elbMin, j.elbowL.rotation.x);
  elbMax = Math.max(elbMax, j.elbowL.rotation.x);
  if (i % 50 === 0) ys.push(j.waist.position.y);
  if (i === 50) heel = j.ankleL.rotation.x;     // left leg at max forward
  if (i === 150) toeOff = j.ankleL.rotation.x;  // left leg at max trailing
}

// carry(): the hands must land ON the held thing, in FRONT of the body,
// and the held thing must land at the HANDS and nowhere near the head.
const c = figure({ height: 1.72 });
const held = new THREE.Object3D();
carry(c, held);
c.updateMatrixWorld(true);
held.updateMatrixWorld(true);
const ch = world(c, 'handL');
const heldW = new THREE.Vector3().setFromMatrixPosition(held.matrixWorld);
const headBox = new THREE.Box3().setFromObject(find(c, 'head'));
// carry() again on the SAME figure must not walk the object upward.
carry(c, held);
c.updateMatrixWorld(true);
held.updateMatrixWorld(true);
const heldW2 = new THREE.Vector3().setFromMatrixPosition(held.matrixWorld);

// Silhouette: torso lathe profile and the fem shoulder:hip slider.
const radii = (fig) => find(fig, 'torso').geometry.parameters.points
    .map((p) => p.x);
const male = figure({ height: 1.72 });
const female = figure({ height: 1.72, fem: 1 });
male.updateMatrixWorld(true);
const wristY = world(male, 'handL').y / male.userData.height;

console.log(JSON.stringify({
  v09: v09.speed, v15: v15.speed, legacyStrideRate: legacy.strideRate,
  armLeg, elbMin, elbMax, heel, toeOff, ys,
  carryHandY: ch.y, carryHandZ: ch.z,
  heldY: heldW.y, heldZ: heldW.z, heldY2: heldW2.y,
  headMinY: headBox.min.y,
  maleTorso: radii(male), femaleTorso: radii(female),
  maleShoulderX: Math.abs(male.userData.joints.shoulderL.position.x),
  femaleShoulderX: Math.abs(female.userData.joints.shoulderL.position.x),
  hasAnkles: 'ankleL' in male.userData.joints
      && 'ankleR' in male.userData.joints,
  wristY,
}));
"""


@pytest.fixture(scope="module")
def gait() -> dict:
    """One node launch of _PROBE_GAIT, shared by every test that reads it."""
    return _measure(_PROBE_GAIT)


_PROBE_LOOK = """
import * as THREE from 'three';
import { figure } from './lib/figure.js';

const find = (root, name) => {
  let hit = null;
  root.traverse((o) => { if (o.name === name) hit = o; });
  return hit;
};
// three stores colours LINEAR, so these read as albedo directly.
const lum = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
const peak = (c) => Math.max(c.r, c.g, c.b);

const seeded = (n) => { let s = n; return () => (s = (s * 16807) % 2147483647)
    / 2147483647; };

// A crowd: complexions must SPREAD, not repeat one flesh colour, and the
// material cache must not blow up doing it.
const tones = [];
const mats = new Set();
for (let i = 1; i <= 24; i++) {
  const f = figure({ height: 1.7, rand: seeded(i * 7919) });
  const skinMat = find(f, 'head').material;
  tones.push(skinMat.color.getHex());
  mats.add(skinMat.uuid);
}

// Every albedo a default figure ships, plus a deliberately near-black hair.
const plain = figure({ height: 1.72, hair: 0x0a0806 });
const albedos = [];
plain.traverse((o) => {
  if (o.material && o.material.color && !o.material.emissive?.getHex()) {
    albedos.push({ name: o.name || o.type, peak: peak(o.material.color),
                   lum: lum(o.material.color) });
  }
});
const skinC = find(plain, 'head').material.color;
// The hair is the only partial sphere on a figure.
let hairMat = null;
plain.traverse((o) => {
  if (o.geometry && o.geometry.type === 'SphereGeometry'
      && o.geometry.parameters.thetaLength < 3) hairMat = o.material;
});

// The shoe: no near-mirror roughness map, and no big flat up-face.
const foot = find(plain, 'footL');
const fm = foot.material;
const g = foot.geometry.index ? foot.geometry.toNonIndexed() : foot.geometry;
const pos = g.attributes.position;
let flatUp = 0, total = 0;
const a = new THREE.Vector3(), b = new THREE.Vector3(), c2 = new THREE.Vector3();
for (let i = 0; i < pos.count; i += 3) {
  a.fromBufferAttribute(pos, i);
  b.fromBufferAttribute(pos, i + 1);
  c2.fromBufferAttribute(pos, i + 2);
  const n = new THREE.Vector3().subVectors(b, a)
      .cross(new THREE.Vector3().subVectors(c2, a));
  const area = n.length() / 2;
  if (area <= 0) continue;
  n.normalize();
  total += area;
  if (n.y > 0.985) flatUp += area;
}

// Face: eyes and a nose by default, gone (and cheaper) on face:false.
const tri = (root) => { let n = 0; root.traverse((o) => {
  if (o.geometry) n += (o.geometry.index ? o.geometry.index.count
      : o.geometry.attributes.position.count) / 3; }); return n; };
// the SAME figure minus the face, hair included, or the diff measures
// the hair the comparison figure never asked for.
const bare = figure({ height: 1.72, hair: 0x0a0806, face: false });

console.log(JSON.stringify({
  toneCount: new Set(tones).size,
  skinMatCount: mats.size,
  skinPeak: peak(skinC), skinLum: lum(skinC),
  skinWarm: skinC.r - skinC.b,
  maxPeak: Math.max(...albedos.map((x) => x.peak)),
  minLum: Math.min(...albedos.map((x) => x.lum)),
  hairLum: lum(hairMat.color), hairRough: hairMat.roughness,
  shoeRough: fm.roughness, shoeHasRoughMap: !!fm.roughnessMap,
  flatUpFrac: flatUp / total,
  hasEyes: !!find(plain, 'eyeL') && !!find(plain, 'eyeR'),
  bareEyes: !!find(bare, 'eyeL'),
  triFace: tri(plain), triBare: tri(bare),
}));
"""


@pytest.fixture(scope="module")
def look() -> dict:
    """One node launch of _PROBE_LOOK, shared by every grading test."""
    return _measure(_PROBE_LOOK)


# --------------------------------------------------------------- the pose


def test_seated_pelvis_lands_on_the_seat(seated):
    """The seat height determines the pose; the author never types a y.

    A standing figure translated upward — the measured failure — drives
    the thighs through the seat surface.
    """
    m = seated
    assert abs(m["pelvisY"] - m["seat"]) < 0.01, m


def test_seated_legs_reach_the_floor_and_bend_forward(seated):
    """Thighs level and forward, shins down, feet on the ground."""
    m = seated
    assert abs(m["kneeY"] - m["seat"]) < 0.10, f"thigh angle wrong: {m}"
    assert m["kneeZ"] < -0.2, f"knees must go FORWARD (-Z), got {m['kneeZ']}"
    assert abs(m["footMinY"]) < 0.06, f"feet off the floor: {m}"
    assert m["headMinY"] > m["seat"], "head below the seat"


def test_a_seated_person_is_shorter_than_a_standing_one(seated):
    """The sanity check a human does at a glance."""
    assert 1.15 < seated["seatedTop"] < 1.45, seated["seatedTop"]


def test_standing_figure_is_its_requested_height_with_feet_at_zero(seated):
    """`seat()` and `alongPath()` both assume origin-at-feet — and the
    domed shoe this port fitted must not have moved the sole plane."""
    m = seated
    assert abs(m["standFeetY"]) < 0.02, m["standFeetY"]
    assert abs(m["standHeight"] - 1.72) < 0.09, m["standHeight"]
    assert m["forward"] == "-Z"


def test_limbs_taper(seated):
    """Constant-radius limbs are what make a crowd read as pipes."""
    m = seated
    assert m["thighEndR"] < m["thighTopR"] * 0.9, m


def test_the_walk_cycle_never_hyperextends_a_knee(seated):
    """A knee flexes backward only. Forward is the uncanny-crowd tell."""
    assert seated["worstKnee"] <= 1e-6, seated["worstKnee"]


def test_walk_speed_matches_stride_times_cadence(gait):
    """The anti-moonwalk contract: stepLength x cadence === speed."""
    m = gait
    assert abs(m["v09"] - 0.9) < 0.05, m["v09"]
    assert abs(m["v15"] - 1.5) < 0.08, m["v15"]


def test_default_gait_is_a_walk_not_a_sprint(gait):
    """The plain two-arg call must stay backward compatible AND pace a
    human walk (the old default, 2.2 strides/s, was 264 steps/min)."""
    assert 0.7 < gait["legacyStrideRate"] < 1.25, gait["legacyStrideRate"]


def test_arms_counter_swing_the_legs(gait):
    """Same-side arm and leg anti-phase, elbow pumping with the swing; a
    NEGATIVE elbow angle is an anatomically impossible backward bend."""
    m = gait
    assert m["armLeg"] < -5, f"arm/leg not counter-phase: {m['armLeg']}"
    assert m["elbMax"] - m["elbMin"] > 0.15, "elbows frozen"
    assert m["elbMin"] > 0, f"elbow bends backward: {m['elbMin']}"


def test_pelvis_bobs_twice_per_stride(gait):
    """Dip at double support, rise at midstance, twice per stride: once
    reads as a limp, never as ice-skating."""
    high0, low1, high2, low3 = gait["ys"]
    assert high0 - low1 > 0.015, f"no visible bob: {gait['ys']}"
    assert high0 - low1 < 0.09, f"bouncing, not walking: {gait['ys']}"
    assert abs(high0 - high2) < 0.005 and abs(low1 - low3) < 0.005, gait["ys"]


def test_ankles_pitch_heel_strike_and_toe_off(gait):
    """The only two moments the eye reads as ground contact."""
    m = gait
    assert m["hasAnkles"], "no ankle pivots in userData.joints"
    assert m["heel"] > 0.05, f"no heel-strike dorsiflexion: {m['heel']}"
    assert m["toeOff"] < -0.15, f"no toe-off push: {m['toeOff']}"


def test_torso_has_a_waist_and_fem_slides_shoulder_hip_ratio(gait):
    """The shoulder-waist-hip S-curve is the strongest silhouette cue at
    10-30 m; `fem` slides shoulder:hip from the male 1.4 to 1.2."""
    m = gait
    hip, waist, chest = m["maleTorso"][0], m["maleTorso"][1], m["maleTorso"][2]
    assert waist < hip * 0.9 and waist < chest * 0.75, m["maleTorso"]
    assert m["femaleShoulderX"] < m["maleShoulderX"] * 0.9, m
    assert m["femaleTorso"][0] > m["maleTorso"][0] * 1.05, \
        "fem does not widen the hips"


def test_wrist_lands_near_the_crotch(gait):
    """Canon arm length: the wrist falls at ~0.485h. The old forearm
    (0.16h) put it at 0.44h — gorilla arms on every standing figure."""
    assert 0.41 < gait["wristY"] < 0.50, gait["wristY"]


# ------------------------------------------------- what carry() holds, where


def test_carry_hands_land_on_the_held_object(gait):
    """carry() parents the tray in FRONT of the chest; the hands must be
    there too (the old elbow sign left them BEHIND the body plane)."""
    m = gait
    assert m["carryHandZ"] < -0.25, f"hands behind: {m['carryHandZ']}"
    assert 0.80 < m["carryHandY"] < 1.10, m["carryHandY"]


def test_the_held_object_sits_at_the_hands_not_on_the_head(gait):
    """THE PORT'S BUG.  `held` is parented to the waist pivot, which is
    already at hipHeight, and the old code then offset it by a
    figure-ROOT height (0.52 h) — 0.85 m above the hands.  The render
    showed a crate balanced on the carrier's head."""
    m = gait
    assert m["heldY"] > m["carryHandY"], "the object hangs below the hands"
    assert m["heldY"] - m["carryHandY"] < 0.12, \
        f"held {m['heldY']:.3f} vs hands {m['carryHandY']:.3f}: floating"
    assert m["heldY"] < m["headMinY"], \
        f"held at {m['heldY']:.3f} is up at the head ({m['headMinY']:.3f})"
    assert m["heldZ"] < -0.25, f"held behind the body: {m['heldZ']}"


def test_carry_is_idempotent_on_the_same_figure(gait):
    """Measuring the posed hands must read the SAME pose each time: a
    re-parent that accumulated its own offset would walk a tray up the
    body one call per frame."""
    m = gait
    assert abs(m["heldY2"] - m["heldY"]) < 1e-9, (m["heldY"], m["heldY2"])


# ------------------------------------------------------- the grading law


def test_skin_is_never_the_undefined_white_ball(look):
    """THE PORT'S OTHER BUG.  `MAT.skin({ color: opts.skin })` with no
    `skin` option passed an explicit `color: undefined`, and
    `Object.assign` copied it OVER the material's default — albedo 1.0
    on every face, neck, forearm and hand.  Measured on the showcase
    frame: a cold (190,204,215) ball where a head should be."""
    m = look
    assert m["skinPeak"] < 0.72, f"skin albedo {m['skinPeak']:.3f} is a lamp"
    assert m["skinLum"] > 0.03, f"skin albedo {m['skinLum']:.3f} is a void"
    assert m["skinWarm"] > 0.05, \
        "skin must be WARMER than it is blue; white is what the bug gave"


def test_a_crowd_is_mixed_without_multiplying_the_material_cache(look):
    """The complexion is drawn from the SAME number that sets the
    material variant, so a ladder of tones costs no more materials than
    the single tone it replaced (one per variant bucket, 9 of them)."""
    m = look
    assert m["toneCount"] >= 3, f"a cloned crowd: {m['toneCount']} tone(s)"
    assert m["skinMatCount"] <= 9, \
        f"{m['skinMatCount']} skin materials for 24 people — cache blown"


def test_every_albedo_stays_inside_the_non_emissive_window(look):
    """Linear 0.02..0.8.  Above it a surface is a light source the tone
    map cannot hold; below it, a hole no environment can fill — and the
    figure ships one deliberately near-black hair colour (0x0a0806) to
    prove the floor is applied rather than merely documented."""
    m = look
    assert m["maxPeak"] <= 0.8, f"peak albedo {m['maxPeak']:.3f}"
    assert m["minLum"] >= 0.02, f"darkest albedo {m['minLum']:.4f}"
    assert m["hairLum"] >= 0.02, f"hair floored to a void: {m['hairLum']:.4f}"
    assert m["hairRough"] < 0.8, \
        "hair needs a broad specular band or it reads as a felt helmet"


def test_the_shoe_presents_no_flat_horizontal_facet(look):
    """A horizontal facet under an open sky is the brightest thing in
    the frame: the box shoe measured sRGB 159 on a 0x3a albedo — a white
    plate stuck to a black shoe, on every figure in the render.  A domed
    upper spreads the same light over a curve.  The near-mirror dips of
    the noise roughness map made the same face worse.

    Measured on the same probe: the box presented 27.2% of its area
    straight up, the dome 6.4% (a small cap at the top of the toe) —
    the sole, correctly, still reads 12.7% straight DOWN.
    """
    m = look
    assert m["flatUpFrac"] < 0.12, \
        f"{m['flatUpFrac']:.2%} of the shoe faces straight up"
    assert not m["shoeHasRoughMap"], "the shoe's roughness map mirrors the sky"
    assert m["shoeRough"] >= 0.7, f"shoe roughness {m['shoeRough']}"


def test_a_head_has_a_face_and_face_false_takes_it_back(look):
    """A ball is what makes a close figure read as a shop dummy however
    good the gait is; a crowd that stays past ~15 m pays nothing for it.

    Measured: 90 triangles of 1 718, 5.2% of a figure.  The first cut
    was 372 (20.5%) at six-segment spheres, which is not what a face
    the size of a thumbnail is worth.
    """
    m = look
    assert m["hasEyes"], "no eyes on a default figure"
    assert not m["bareEyes"], "face:false still built eyes"
    assert m["triBare"] < m["triFace"], "face:false saved nothing"
    assert m["triFace"] - m["triBare"] < 0.10 * m["triFace"], (
        f"a face costs {m['triFace'] - m['triBare']:.0f} of "
        f"{m['triFace']:.0f} triangles")
