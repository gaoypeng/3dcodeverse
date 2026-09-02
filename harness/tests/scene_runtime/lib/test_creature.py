"""Cartoon creatures are their own shape, not small humans.

Cute-ifying figure.js's 7.5-head human yields a small adult; head 40-50%
of height, egg body and three-layer wide-low eyes are what read as alive.

Ported from the scene_multifile_graphics reference (tests/test_creature_lib.py)
and extended with what this port had to fix on OUR renderer: albedos inside
the band ACES at exposure 1 can light, colour graded BETWEEN parts instead of
one flat value, a belly patch that stands proud of the torso rather than
z-fighting along it, tails scaled by the creature rather than by a serpent's
1.5x body depth, and a curl tail that is one piece instead of seven balls.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import measure

pytestmark = pytest.mark.node


_PROBE = """
import * as THREE from 'three';
import { creature, ear, tail, cheeks, walk, idle } from './lib/creature.js';

const rand = (() => { let s = 5;
  return () => (s = (s * 16807) % 2147483647) / 2147483647; })();

const c = creature({ height: 0.45, body: 'biped', bodyColor: 0xf5d547,
                     bellyColor: 0xfff0a8, rand });
ear(c, { type: 'point', length: 0.9, tipColor: 0x2b2b2b });
tail(c, { type: 'bolt' });
cheeks(c, 0xe8543f);
c.updateMatrixWorld(true);

const whole = new THREE.Box3().setFromObject(c);
const named = {};
c.traverse((o) => {
  if (!o.name) return;
  const b = new THREE.Box3().setFromObject(o);
  if (!b.isEmpty()) named[o.name] = {
    c: b.getCenter(new THREE.Vector3()).toArray(),
    s: b.getSize(new THREE.Vector3()).toArray(),
  };
});
const triCount = (o) => o.geometry.index ? o.geometry.index.count / 3
    : o.geometry.attributes.position.count / 3;
let tris = 0;
c.traverse((o) => { if (o.isMesh) tris += triCount(o); });

// Eye layer count: sclera + iris + glint = 3 meshes under each eye.
let eyeMeshes = 0;
c.traverse((o) => { if (o.name === 'eyeL') o.traverse((m) => {
  if (m.isMesh) eyeMeshes++; }); });

// A quadruped's legs must alternate diagonally, never in lockstep.
const q = creature({ height: 0.7, body: 'quadruped' });
walk(q, 0.11);
const j = q.userData.joints;
const diag = [j.legFL.rotation.x, j.legBR.rotation.x];
const other = [j.legFR.rotation.x, j.legBL.rotation.x];

// The three eye layers must be visible FROM THE FRONT, not merely
// present: they shipped with the iris and glint at +Z, behind the
// eyeball, and every creature had two blank white blobs for eyes.
const ray = new THREE.Raycaster();
const wpos = (o) => new THREE.Vector3().setFromMatrixPosition(o.matrixWorld);
let eyeObj = null, cheekObj = null;
c.traverse((o) => { if (o.name === 'eyeL') eyeObj = o;
                    if (o.name === 'cheekL') cheekObj = o; });
const pe = wpos(eyeObj);
ray.set(new THREE.Vector3(pe.x, pe.y, -5), new THREE.Vector3(0, 0, 1));
const eyeHit = ray.intersectObject(c, true)[0];
const irisFirst = eyeHit
    ? eyeHit.object.material.color.getHexString() : null;
let cheekFirst = null;
if (cheekObj) {
  const pc2 = wpos(cheekObj);
  ray.set(new THREE.Vector3(pc2.x, pc2.y, -5), new THREE.Vector3(0, 0, 1));
  const h = ray.intersectObject(c, true)[0];
  if (h) { let o = h.object; while (!o.name && o.parent) o = o.parent;
           cheekFirst = o.name; }
}

// Idle must actually move something -- and must not stretch the skull
// with the chest, which is what scaling the shared body pivot did.
const i0 = creature({ height: 0.5 });
const restY = i0.userData.joints.head.rotation.y;
idle(i0, 1.3);
const idleY = i0.userData.joints.head.rotation.y;
i0.updateMatrixWorld(true);
const headScaleY = new THREE.Vector3().setFromMatrixScale(
    i0.userData.joints.head.matrixWorld).y;

// ALBEDO BAND. A cartoon palette is written as ink; at exposure 1 with
// no post chain, a 0.99 sclera clips to paper and a 0x151515 pupil has
// no linear lightness for the sun to lift.  Every non-emissive surface
// has to land inside 0.02..0.80 linear lightness.
const albedo = [];
const emissives = [];
const seen = new Set();
for (const o of [c, q]) o.traverse((m) => {
  if (!m.isMesh || seen.has(m.material.uuid)) return;
  seen.add(m.material.uuid);
  const mt = m.material;
  const hsl = mt.color.getHSL({ h: 0, s: 0, l: 0 });
  const lit = mt.emissive && mt.emissive.getHex() !== 0
      && mt.emissiveIntensity > 0;
  if (lit) emissives.push(mt.emissiveIntensity);
  else albedo.push(+hsl.l.toFixed(4));
});

// COLOUR GRADED BETWEEN PARTS: head, torso and limb are one hue family
// at three lightnesses, not one flat value repeated.
const lin = (mesh) => {
  let m = null;
  c.traverse((o) => { if (o.name === mesh) o.traverse((x) => {
    if (x.isMesh && !m) m = x.material; }); });
  return m ? m.color.getHSL({ h: 0, s: 0, l: 0 }) : null;
};
const headL = lin('headMesh'), torsoL = lin('torso'), legL = lin('legMeshL');

// The BELLY PATCH has to stand clearly proud of the torso. Expressed in
// the torso ellipsoid's own normalised space, its front pole was at
// 1.04 -- near-tangent over a wide ring, which renders as a band of
// z-fighting speckle instead of a bib.
let torsoM = null, bellyM = null;
c.traverse((o) => { if (o.name === 'torso') torsoM = o;
                    if (o.name === 'belly') bellyM = o; });
const bellyProud = (Math.abs(bellyM.position.z - torsoM.position.z)
    + bellyM.scale.z * 0.5) / (torsoM.scale.z * 0.5);

// A LEGLESS body still rests on y = 0 (the torso sphere is scaled 1.12
// about its own centre, so it used to dip below the pivot).
const serp = creature({ height: 1.0, body: 'serpent' });
tail(serp, { type: 'fin', length: 1.2 });
serp.updateMatrixWorld(true);
const sBox = new THREE.Box3().setFromObject(serp);
let serpTailH = 0;
serp.traverse((o) => { if (o.name === 'tail') {
  const b = new THREE.Box3().setFromObject(o);
  serpTailH = b.max.y - b.min.y; } });

// The CURL tail is one connected piece, not a row of separated balls.
const cu = creature({ height: 0.7, body: 'quadruped' });
tail(cu, { type: 'curl' });
let curlMeshes = 0, curlTris = 0;
cu.userData.joints.tail.traverse((o) => {
  if (o.isMesh) { curlMeshes++; curlTris += triCount(o); } });

// A `long` ear's coloured tip must CAP the cone it sits on, never flare
// out of it -- it shipped with fixed radii and read as a wedge.
// ONE ear: measured across both, the two shafts pair off against each
// other and the ratio is 1 whatever the tip does.
const le = creature({ height: 0.6 });
ear(le, { type: 'long', length: 1.2, tipColor: 0xffd9a0 });
let earL = null;
le.userData.joints.head.traverse((o) => { if (o.name === 'earL') earL = o; });
const cyl = [];
earL.traverse((o) => {
  if (o.isMesh && o.geometry.parameters
      && o.geometry.parameters.radiusBottom !== undefined) {
    cyl.push({ p: o.geometry.parameters, y: o.position.y });
  }
});
cyl.sort((a, b) => b.p.height - a.p.height);
let tipShaft = null;
if (cyl.length === 2) {
  const shaft = cyl[0].p, tip = cyl[1];
  const y0 = tip.y - tip.p.height / 2;
  const rAt = shaft.radiusBottom
      + (shaft.radiusTop - shaft.radiusBottom) * (y0 / shaft.height);
  tipShaft = tip.p.radiusBottom / rAt;
}

console.log(JSON.stringify({
  height: whole.max.y - whole.min.y,
  hUser: c.userData.height,
  baseY: whole.min.y,
  headH: named.headMesh ? named.headMesh.s[1] : null,
  headRatio: named.headMesh
      ? named.headMesh.s[1] / c.userData.height : null,
  eyeL: named.eyeL ? named.eyeL.c : null,
  eyeR: named.eyeR ? named.eyeR.c : null,
  eyeMeshes,
  headCy: named.headMesh ? named.headMesh.c[1] : null,
  hasTail: !!named.tail,
  hasEars: !!named.earL && !!named.earR,
  hasCheeks: !!named.cheekL,
  bodyW: named.torso ? named.torso.s[0] : null,
  bodyH: named.torso ? named.torso.s[1] : null,
  forward: c.userData.forward,
  tris,
  diagLockstep: Math.abs(diag[0] - diag[1]) < 1e-9,
  gaitOpposed: diag[0] * other[0] < 0,
  idleMoves: Math.abs(idleY - restY) > 1e-6,
  headScaleY,
  irisFirst,
  cheekFirst,
  albedoMin: Math.min(...albedo),
  albedoMax: Math.max(...albedo),
  emissives,
  headL: headL ? +headL.l.toFixed(4) : null,
  torsoL: torsoL ? +torsoL.l.toFixed(4) : null,
  legL: legL ? +legL.l.toFixed(4) : null,
  headHue: headL ? +headL.h.toFixed(4) : null,
  legHue: legL ? +legL.h.toFixed(4) : null,
  bellyProud: +bellyProud.toFixed(3),
  serpBaseY: sBox.min.y,
  serpH: serp.userData.height,
  serpTailH,
  curlMeshes,
  curlTris,
  tipShaft: tipShaft === null ? null : +tipShaft.toFixed(3),
}));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return measure(_PROBE, ("creature.js", "materials.js"))


def test_the_head_is_oversized_the_way_a_cartoon_creature_is(probe):
    """40-50% of total height. A human is 13%.

    This single number is what separates a creature from a small adult,
    and it is the thing a builder gets wrong when handed figure.js.
    """
    assert 0.36 < probe["headRatio"] < 0.56, probe["headRatio"]


def test_the_eyes_have_three_layers(probe):
    """Sclera, iris, and an offset glint.

    A sphere with two flat dark dots is exactly what makes a low-poly
    creature read as a toy instead of as something alive.
    """
    assert probe["eyeMeshes"] == 3, probe["eyeMeshes"]


def test_the_pupil_is_what_the_camera_sees_first(probe):
    """Layers present is not layers visible: a front ray must hit the iris.

    The iris/glint shipped at +Z, behind the eyeball (creatures face -Z);
    the owner caught it on a blank-eyed mascot while every test passed.
    """
    r, g, b = (int(probe["irisFirst"][i:i + 2], 16) for i in (0, 2, 4))
    assert max(r, g, b) < 90, (
        f"the frontmost thing at the eye centre is #{probe['irisFirst']}, "
        "not a dark pupil")


def test_the_cheek_patch_is_on_the_skull_not_inside_it(probe):
    """At x = 0.74r the sphere surface is only at z = -0.67r.

    The patch sat at -0.56r and was half buried, showing as two red
    slivers beside the eyes instead of round cheeks.
    """
    assert probe["cheekFirst"] == "cheekL", probe["cheekFirst"]


def test_the_eyes_are_wide_and_low_on_the_head(probe):
    """High close-set eyes read adult; low wide eyes read young."""
    gap = abs(probe["eyeL"][0] - probe["eyeR"][0])
    assert gap > 0.25 * probe["headH"], f"eyes too close together: {gap}"
    assert probe["eyeL"][1] < probe["headCy"] + 0.02, "eyes above head centre"
    assert probe["eyeL"][2] < 0, "eyes must face -Z, the creature's forward"


def test_the_body_is_an_egg_not_a_torso(probe):
    """Wider than it is tall is the round silhouette."""
    assert probe["bodyW"] > probe["bodyH"] * 0.85, (probe["bodyW"],
                                                    probe["bodyH"])


def test_attachments_land_and_are_findable(probe):
    assert probe["hasEars"] and probe["hasTail"] and probe["hasCheeks"], probe


def test_it_rests_on_zero_and_faces_minus_z(probe):
    """Same contract as every other asset, so place.js works on it."""
    assert abs(probe["baseY"]) < 0.02, probe["baseY"]
    # The bbox includes the ears, which stand above the skull.
    assert abs(probe["hUser"] - 0.45) < 0.05, probe["hUser"]
    assert probe["height"] >= probe["hUser"] * 0.95, (probe["height"],
                                                      probe["hUser"])
    assert probe["forward"] == "-Z"


def test_a_legless_body_rests_on_the_ground_too(probe):
    """A serpent's torso is scaled 1.12 in Y about its own centre.

    Taken from the pivot, its underside dips 6% of the body height below
    zero, and the animal shipped buried to the waist.
    """
    assert abs(probe["serpBaseY"]) < 0.02 * probe["serpH"], probe["serpBaseY"]


def test_a_quadruped_walks_on_diagonal_pairs(probe):
    """Legs moving in lockstep is the classic toy-animal tell."""
    assert probe["diagLockstep"], "diagonal pairs must move together"
    assert probe["gaitOpposed"], "the other diagonal must be out of phase"


def test_idle_actually_moves_something(probe):
    """A perfectly still creature is a statue in every judged frame."""
    assert probe["idleMoves"]


def test_idle_breathes_with_the_chest_and_not_with_the_skull(probe):
    """The head is a CHILD of the body pivot that idle() squashes.

    Without the counter-scale the face pulses along with the chest,
    which reads as a bug in every frame the judge sees. It cancels to
    1e-4 rather than exactly, because idle also TILTS the head and a
    rotated child under a non-uniform parent scale is sheared, not
    scaled; the defect this guards is 3.5%, two orders above that.
    """
    assert abs(probe["headScaleY"] - 1) < 2e-3, probe["headScaleY"]


def test_a_village_can_afford_a_crowd_of_them(probe):
    assert probe["tris"] < 6000, probe["tris"]


def test_every_albedo_is_inside_the_band_this_renderer_can_light(probe):
    """No post chain: ACES at exposure 1 clips a 0.99 sclera to paper.

    And a near-black pupil has no linear lightness left for the sun to
    lift, so it reads as a hole punched in the face rather than an eye.
    """
    assert probe["albedoMin"] >= 0.02, probe["albedoMin"]
    assert probe["albedoMax"] <= 0.80, probe["albedoMax"]


def test_emissives_stay_in_the_bloom_friendly_band(probe):
    """1.5-4: below it the tone map folds the highlight into its own
    surface, above it every emissive is the same white blob."""
    assert probe["emissives"], "the eye glint is emissive"
    assert all(1.5 <= e <= 4.0 for e in probe["emissives"]), probe["emissives"]


def test_the_parts_are_graded_not_one_flat_value(probe):
    """Face at the author's colour, chest under it, limbs under that.

    A painter does this on every toy; the renderer will not invent it,
    and without it a body is one flat value with a shadow on it.
    """
    assert probe["headL"] > probe["torsoL"] > probe["legL"], probe
    assert probe["headL"] - probe["legL"] > 0.04, probe
    # ... and the hue moves too: shaded parts go cool, not just dark.
    assert abs(probe["headHue"] - probe["legHue"]) > 0.004, probe


def test_the_belly_patch_stands_proud_of_the_torso(probe):
    """Two near-tangent ellipsoids render as a ring of z-fight speckle.

    At the shipped depth the bib's front pole reached 1.04 of the torso
    radius and its edge was noise, not an outline.
    """
    assert probe["bellyProud"] > 1.10, probe["bellyProud"]


def test_a_tail_is_scaled_by_the_creature_not_by_its_body_depth(probe):
    """A serpent's bodyD is 1.5x its height.

    Taken neat as the tail's span it gave a 1.9 m serpent a 3.4 m fin —
    a pyramid taller than the animal wearing it.
    """
    assert probe["serpTailH"] < probe["serpH"], (probe["serpTailH"],
                                                 probe["serpH"])


def test_the_curl_tail_is_one_piece(probe):
    """Seven separated balls read as a bubble trail leaving the animal.

    One tapered tube also costs 400 triangles instead of 980.
    """
    assert probe["curlMeshes"] <= 2, probe["curlMeshes"]
    assert probe["curlTris"] < 700, probe["curlTris"]


def test_a_long_ears_tip_caps_the_cone_it_sits_on(probe):
    """Fixed tip radii flared out of a `long` ear (0.087 hr wide there)
    and read as a coloured wedge stuck between the ears."""
    assert probe["tipShaft"] is not None
    assert probe["tipShaft"] <= 1.1, probe["tipShaft"]
