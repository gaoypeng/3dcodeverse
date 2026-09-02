"""season.js: the turn of the year, and the hour a scene is posed at.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_season_lib.py).  Their renderer-contract assertions are dropped;
the claims their JSDoc makes are kept — the spread is wider at the turn than
in high summer, the tint BLENDS rather than assigns, season and kind are
uniforms so one scene compiles one program, a low sun is warm and its fill is
blue, the pose mutates nothing it was handed, and one seed turns the same
canopy twice.

THE PORT'S OWN LAWS, each one a defect this file exists to stop coming back:

1. THE TURN MUST NOT GO FLAT ON A SHADER-BUILT CROWN.  canopy.js and grass.js
   draw a whole stand in one call and build every leaf card in the VERTEX
   shader from instance attributes, so their ``position`` attribute is a
   zeroed buffer.  The reference celled the per-leaf random off exactly that
   attribute: measured on our renderer 2026-09-01, a whole autumn crown came
   back with a hue spread of 1.3 degrees — one sheet of chocolate, the single
   failure the module's own doc says it exists to avoid.  The grain now
   carries a world fallback keyed on ``vAstraSeaD``, and an InstancedMesh
   (which shares ONE geometry, so its model cell is the same card for every
   copy) decorrelates on the instance.

2. NOTHING MAY WRAP THE SMOOTH FIELD.  For grass and ground the grain IS a
   continuous world field; a ``fract`` anywhere downstream of it draws its own
   sawtooth contour rings across the terrain — measured, six rings per unit of
   field, a floor that read as plywood.

3. THE PALETTE IS GRADED FOR THIS PIPELINE.  Every anchor keeps a dielectric
   floor in the channels it does not own (the reference's autumn red was
   linear (0.274, 0.018, 0.005) — a red with no blue at all, whose shade side
   could take nothing from the sky), and autumn's two anchors stay far enough
   apart in hue to read as a PAIR rather than as one wash.

4. THE LEVELS ARE OURS.  environment.js measured this renderer (ACES,
   exposure 1.0, no post chain) at day 5.4 key / 1.4 fill and night 2.2 / 1.0,
   and sunRig clamps anything under 4.5 (day key) or 0.8 (night fill) UP.  The
   reference's pose asked for 3.3 at noon and 0.75 at night: every daylight
   hour came back clamped to the same 4.5 — one flat key from breakfast to
   dusk — and a posed night rendered at a third of the rig it replaced.
"""
from __future__ import annotations

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "season.js")


# The probe's stand-in for three's own compile: a fragment shader that owns a
# `diffuseColor` the patch can land on, and a vertex shader that owns
# `transformed`.
_COMPILE = """
const compiled = (o) => {
  const m = new THREE.MeshStandardMaterial();
  patchSeasonTint(m, o);
  const s = { uniforms: {},
              vertexShader: 'void main() { #include <begin_vertex> }',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return { u: s.uniforms, vs: s.vertexShader, fs: s.fragmentShader,
           key: m.customProgramCacheKey() };
};
"""


def test_autumn_is_a_spread_of_hues_and_not_one_orange():
    """A canopy turns unevenly — tree by tree and leaf by leaf — so the
    claim is the SPREAD, not the mean.  A single lerp toward one autumn
    colour is the failure this library exists to avoid, and it is the one a
    mean-only test would pass."""
    out = measure("""
import * as THREE from 'three';
import { patchSeasonTint } from './lib/season.js';
""" + _COMPILE + """
const autumn = compiled({ season: 0.5, kind: 'leaf' });
const summer = compiled({ season: 0.25, kind: 'leaf' });
console.log(JSON.stringify({
  autumnSpread: autumn.u.uSeaSpread.value,
  summerSpread: summer.u.uSeaSpread.value,
  phase: autumn.u.uSeaPhase.value,
  // the spread has to be carried per FRAGMENT, not just held per material
  readsGrain: /uSeaGrain/.test(autumn.fs),
  readsSeed: /uSeaSeed/.test(autumn.fs),
}));
""", _LIBS)
    assert out["autumnSpread"] > out["summerSpread"] * 1.5, (
        "autumn must vary more than summer, not merely differ in hue")
    assert out["phase"] == 0.5
    assert out["readsGrain"] and out["readsSeed"]


def test_the_tint_blends_and_never_assigns_over_what_it_lands_on():
    """A season tint lands on grass, leaves and ground that other patches have
    already coloured, so it is the patch with the most to erase."""
    out = measure("""
import * as THREE from 'three';
import { patchSeasonTint } from './lib/season.js';
""" + _COMPILE + """
const c = compiled({});
const body = c.fs.slice(c.fs.indexOf('#include <color_fragment>'));
const writes = body.match(/diffuseColor\\.rgb\\s*=[^=]/g) || [];
console.log(JSON.stringify({
  writes: writes.length,
  allBlend: writes.every(() =>
      /diffuseColor\\.rgb\\s*=\\s*mix\\(\\s*diffuseColor\\.rgb/.test(body)
      || /diffuseColor\\.rgb\\s*\\*=/.test(body)),
  // and it must READ what was there, not only its brightness: an earlier
  // patch's hue jitter is what keeps a crown from flattening to one colour
  readsBaseHue: /uSeaKeep/.test(body),
}));
""", _LIBS)
    assert out["writes"] >= 1
    assert out["allBlend"], "a season tint must blend, not assign"
    assert out["readsBaseHue"], (
        "the turn must keep a share of the hue an earlier patch left here")


def test_season_and_kind_are_uniforms_so_a_scene_shares_one_program():
    """Four seasons on three kinds is twelve materials in one scene.  If
    either branched the SOURCE that is twelve programs, and the first material
    to compile a cache key would decide the source for all of them."""
    out = measure("""
import * as THREE from 'three';
import { patchSeasonTint } from './lib/season.js';
""" + _COMPILE + """
const a = compiled({ season: 0.0, kind: 'leaf' });
const b = compiled({ season: 0.75, kind: 'leaf' });
const c = compiled({ season: 0.5, kind: 'grass' });
console.log(JSON.stringify({
  seasonSharesSource: a.fs === b.fs,
  kindSharesSource: a.fs === c.fs,
  oneKey: a.key === b.key && a.key === c.key,
}));
""", _LIBS)
    assert out["seasonSharesSource"], "season must be a uniform"
    assert out["kindSharesSource"], "kind must be a uniform"
    assert out["oneKey"]


def test_a_shader_built_crown_still_turns_unevenly():
    """LAW 1.  canopy.js ships `position` as a zeroed attribute, so the model
    cell is one value for a whole draw call.  The grain must fall back to the
    world field there — and an InstancedMesh, which shares one geometry, must
    decorrelate on the instance, or a stand of leaves picks one pigment and
    only its DATE varies."""
    out = measure("""
import * as THREE from 'three';
import { patchSeasonTint } from './lib/season.js';
""" + _COMPILE + """
const c = compiled({ kind: 'leaf' });
const grain = c.fs.slice(c.fs.indexOf('vec3 astraSeasonGrain()'));
console.log(JSON.stringify({
  // the flag is measured off the MODEL attribute, in the vertex stage
  flagsDegenerate: /vAstraSeaD\\s*=[^;]*dot\\(\\s*position\\s*,\\s*position/
      .test(c.vs),
  // and it has to reach the field weight, not merely be declared
  fallsBack: /vAstraSeaD/.test(grain.slice(0, grain.indexOf('return'))),
  perInstance: /vAstraSeaI/.test(grain.slice(0, grain.indexOf('return'))),
}));
""", _LIBS)
    assert out["flagsDegenerate"], (
        "the vertex stage must flag an asset with no model position")
    assert out["fallsBack"], (
        "a crown whose leaves have no model position must fall back to the "
        "world field, or its whole turn is one colour")
    assert out["perInstance"], (
        "an InstancedMesh shares one geometry: the pigment must decorrelate "
        "on the instance")


def test_nothing_wraps_the_smooth_world_field():
    """LAW 2.  For grass and ground the grain is a continuous field, and a
    `fract` on anything derived from it draws contour rings across the whole
    terrain.  Only the hash cells (already discontinuous) and the date's own
    ring position may wrap."""
    out = measure("""
import * as THREE from 'three';
import { patchSeasonTint } from './lib/season.js';
""" + _COMPILE + """
const c = compiled({ kind: 'ground' });
const body = c.fs.slice(c.fs.indexOf('vec3 seG = astraSeasonGrain();'));
const tail = body.slice(0, body.indexOf('diffuseColor.rgb = mix'));
console.log(JSON.stringify({
  wraps: (tail.match(/fract\\s*\\(/g) || []).length,
  tail,
}));
""", _LIBS)
    assert out["wraps"] == 0, (
        "the twist and value randoms must be continuous combinations of the "
        f"grain, not wrapped: {out['tail']}")


def test_every_anchor_is_graded_for_this_pipeline():
    """LAW 3.  A channel at zero has nothing for the sky to fill, and two
    autumn anchors a few degrees apart are one wash however they are picked."""
    out = measure("""
import * as THREE from 'three';
import { patchSeasonTint } from './lib/season.js';
""" + _COMPILE + """
const hueOf = (c) => { const o = {}; c.getHSL(o); return o.h * 360; };
const rows = {};
for (const kind of ['leaf', 'grass', 'ground']) {
  const u = compiled({ kind }).u;
  const anchors = ['uSeaSpring', 'uSeaSummer', 'uSeaGold', 'uSeaRed',
                   'uSeaWinter'].map((n) => u[n].value);
  rows[kind] = {
    // working-space (linear) channels: the albedo the shader multiplies
    minChannel: Math.min(...anchors.map((c) => Math.min(c.r, c.g, c.b))),
    maxChannel: Math.max(...anchors.map((c) => Math.max(c.r, c.g, c.b))),
    autumnGap: Math.abs(hueOf(u.uSeaGold.value) - hueOf(u.uSeaRed.value)),
    twist: u.uSeaTwist.value,
    keep: u.uSeaKeep.value,
  };
}
console.log(JSON.stringify(rows));
""", _LIBS)
    for kind, row in out.items():
        assert row["minChannel"] >= 0.012, (
            f"{kind}: an anchor with a channel at zero takes nothing from the "
            f"sky in shade ({row['minChannel']:.4f})")
        assert row["maxChannel"] <= 0.8, f"{kind}: albedo over 0.8"
        assert row["autumnGap"] >= 15, (
            f"{kind}: gold and red are {row['autumnGap']:.1f} deg apart — one "
            "wash, not a pair")
        assert row["twist"] > 0 and row["keep"] > 0, (
            f"{kind}: the hue variance knobs must not be switched off")


def test_the_sun_is_warm_low_and_neutral_at_noon():
    """Colour temperature is the cue that says what hour it is.  A low sun is
    warm and its fill turns blue; noon is neutral."""
    out = measure("""
import { dayCycle } from './lib/season.js';
const at = (hour) => {
  const d = dayCycle(null, { hour, latitude: 40 });
  return { el: d.elevation, keyEl: d.keyElevation,
           warmth: d.sunColor.r - d.sunColor.b,
           fillCool: d.ambientColor.b - d.ambientColor.r,
           night: d.night, intensity: d.intensity, fill: d.fill,
           kelvin: d.kelvin, mood: d.mood };
};
console.log(JSON.stringify({
  dawn: at(6.5), morning: at(9), noon: at(12), dusk: at(17.5),
  late: at(18.6), night: at(20),
}));
""", _LIBS)
    assert out["noon"]["el"] > out["dawn"]["el"], "noon must be highest"
    assert out["noon"]["el"] > out["dusk"]["el"]
    assert out["dawn"]["warmth"] > out["noon"]["warmth"], (
        "a low sun must be warmer than a high one")
    assert out["dusk"]["warmth"] > out["noon"]["warmth"]
    assert out["dawn"]["fillCool"] > 0, "low-sun shadows go blue"
    assert out["night"]["night"] is True
    assert out["night"]["intensity"] < out["noon"]["intensity"]
    # The sun goes DOWN at night; the key is lifted so a night scene still
    # has somewhere to light from, and it says so by its name.
    assert out["night"]["el"] < 0, "the sun must set"
    assert out["night"]["keyEl"] > 0, "the moon key stays above the horizon"
    assert out["dusk"]["mood"] == "golden" and out["noon"]["mood"] == "day"


def test_the_pose_is_levelled_for_this_renderer():
    """LAW 4.  sunRig clamps a day key under 4.5 and a night fill under 0.8
    UP, so a pose graded for a post chain at exposure 1.1 arrives as one flat
    key across the whole working day and a night three times too dark."""
    out = measure("""
import { dayCycle } from './lib/season.js';
const at = (hour) => {
  const d = dayCycle(null, { hour, latitude: 40 });
  return { intensity: d.intensity, fill: d.fill, el: d.elevation };
};
const day = [8, 9, 10, 11, 12, 13, 14, 15, 16].map(at);
console.log(JSON.stringify({
  dayMin: Math.min(...day.map((d) => d.intensity)),
  dayMax: Math.max(...day.map((d) => d.intensity)),
  fillMin: Math.min(...day.map((d) => d.fill)),
  fillMax: Math.max(...day.map((d) => d.fill)),
  night: at(23), setting: at(18.75),
}));
""", _LIBS)
    # environment.js's own day rig is 5.4 over a 4.5 floor: the working day
    # has to live ABOVE the floor or every hour of it renders identically.
    assert out["dayMin"] >= 4.5, (
        f"the day key falls under sunRig's floor ({out['dayMin']:.2f}) — "
        "every hour clamped to the same light")
    assert out["dayMax"] <= 6.0, "and never blows past the golden rig"
    assert out["fillMin"] >= 1.0 and out["fillMax"] <= 1.45, (
        "the fill tracks environment.js's 1.1 (golden) .. 1.4 (day)")
    # night is a MOON rig, measured at 2.2 / 1.0 on this renderer
    assert out["night"]["intensity"] >= 2.0
    assert out["night"]["fill"] >= 0.9
    # and the sun really does go out in its last degrees
    assert out["setting"]["intensity"] < out["dayMin"]


def test_the_day_cycle_does_not_mutate_what_it_was_handed():
    """It returns a description a composer applies.  A helper that reaches
    into a rig it was given makes the pose impossible to re-derive, and two
    calls at two hours would fight."""
    out = measure("""
import * as THREE from 'three';
import { dayCycle } from './lib/season.js';
const rig = { sunDir: new THREE.Vector3(0, 1, 0), intensity: 1,
              marker: 'untouched' };
const before = JSON.stringify(rig);
const a = dayCycle(rig, { hour: 7 });
const b = dayCycle(rig, { hour: 17 });
console.log(JSON.stringify({
  rigUnchanged: JSON.stringify(rig) === before,
  independent: a.elevation !== b.elevation || a.azimuth !== b.azimuth,
  bothFinite: [a.elevation, b.elevation, a.fogDensity, b.fogDensity,
               a.intensity, b.intensity].every(Number.isFinite),
  // fog stays in the sky's own hue family — it IS the horizon colour
  fogIsHorizon: a.fogColor.getHex() === a.horizon.getHex(),
  freshColours: a.fogColor !== a.horizon,
}));
""", _LIBS)
    assert out["rigUnchanged"], "dayCycle must not mutate its argument"
    assert out["independent"] and out["bothFinite"]
    assert out["fogIsHorizon"] and out["freshColours"]


def test_one_seed_turns_the_same_canopy_every_time():
    out = measure("""
import * as THREE from 'three';
import { patchSeasonTint } from './lib/season.js';
""" + _COMPILE + """
const seedOf = (seed) => compiled({ seed }).u.uSeaSeed.value;
console.log(JSON.stringify({
  same: seedOf(4).x === seedOf(4).x && seedOf(4).y === seedOf(4).y,
  differs: seedOf(4).x !== seedOf(9).x,
}));
""", _LIBS)
    assert out["same"] and out["differs"]


def test_the_tint_compiles_over_the_libraries_it_lands_on():
    """A season tint goes on foliage that already carries leaf transmission,
    wind and surface breakup — on an InstancedMesh, which is how a stand of
    trees is drawn, and on a canopy.js crown, which is the asset whose model
    position the fallback exists for."""
    code, out = compile_scene("""
import * as THREE from 'three';
import { patchSeasonTint, dayCycle } from './lib/season.js';
import { patchLeafSSS, patchWind } from './lib/foliage_shade.js';
import { patchMicroBreakup } from './lib/surface_wear.js';
import { makeCanopy } from './lib/canopy.js';
export function createScene() {
  const scene = new THREE.Scene();
  const pose = dayCycle(null, { hour: 16 });
  const sun = new THREE.DirectionalLight(pose.sunColor, pose.intensity);
  sun.position.copy(pose.sunDir).multiplyScalar(60);
  scene.add(sun);
  scene.add(new THREE.HemisphereLight(0xbfd4ee, 0x6a5a44, pose.fill));
  const leaf = new THREE.MeshStandardMaterial({ color: 0x4a6b2a });
  patchLeafSSS(leaf, {});
  patchWind(leaf, { height: 4 });
  patchMicroBreakup(leaf, {});
  patchSeasonTint(leaf, { season: 0.5, kind: 'leaf' });
  scene.add(new THREE.Mesh(new THREE.PlaneGeometry(1, 1), leaf));
  scene.add(new THREE.InstancedMesh(
      new THREE.ConeGeometry(1, 3, 8), leaf, 4));
  const crown = makeCanopy({ position: [0, 4, 0], radius: 2.5, leaves: 200 });
  patchSeasonTint(crown.children[0].material,
                  { season: 0.5, kind: 'leaf', seed: 3 });
  scene.add(crown);
  const ground = new THREE.MeshStandardMaterial({ color: 0x6b5d3c });
  patchSeasonTint(ground, { season: 0.5, kind: 'ground' });
  const mat = new THREE.Mesh(new THREE.PlaneGeometry(20, 20, 8, 8), ground);
  mat.rotation.x = -Math.PI / 2;
  scene.add(mat);
  return { scene, update() {},
           cameras: [{ name: 'wood', position: [9, 5, 11],
                       lookAt: [0, 2, 0], fov: 45 }] };
}
""", ("shader.js", "season.js", "foliage_shade.js", "surface_wear.js",
      "canopy.js", "grass.js", "noise.js"))
    assert code == 0, out
    assert "WARN" not in out, out
