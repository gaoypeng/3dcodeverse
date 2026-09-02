"""rain.js: falling streaks, impacts, a wet-material conversion, a puddle.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_rain_lib.py).  Its two laws are kept exactly as they stood,
because both are about the delivery frame rather than about their renderer:
rain must read FROZEN (a frozen drop is a speck, a frozen STREAK is rain,
and one synchronized splash phase is no splash at all), and every custom
ShaderMaterial here must be assembled by lib/shader.js or it is silently
discarded.  Their renderer-contract assertions are not kept: our renderer
runs logarithmicDepthBuffer OFF by default and has no GTAO override pass,
so the logdepth chunks are no-ops here — the two that survive are the ones
that break a COMPILE (an `#include` sharing its line, and the fragment
chunk not being first in main), plus the degenerate base quad, which our
shadow pass still draws with its own material.

THE PORT'S OWN LAWS — each one a frame rendered on our host at
fx/out/rain/, looked at, and measured against a control render of the same
scene with the rain field (or the soak) removed:

1. A STREAK MUST PRINT ON A BLOWN SKY, AND IT CAN ONLY DO IT DARK.  ACES at
   exposure 1.0 puts this host's overcast sky at 0.94, so a pale streak
   laid over it moves the pixel about 3/255: measured against a no-rain
   control, the shipped field touched 0.82% of the sky band by a mean of
   3.4/255 and read as nothing at all in the still.  A streak is ~2 px
   wide, so a bright-core / dark-rim split ACROSS it averages back to one
   tone inside a single pixel — the split has to run along its LENGTH,
   where it has hundreds of pixels.  Head bright (the specular blob),
   tail dark (the trail the drop refracts light out of): 2.03% of the sky
   band, mean 12.8/255, peak 86/255, and every touched pixel darker than
   the control rather than lighter.  Horizontal gradient over the same
   band 0.08 -> 0.45 against a flat 0.11 vertical: still streaks, not
   noise.

2. WATER IS COLOURLESS: IT SHOWS YOU THE SKY.  Every tint here was a
   hardcoded pale blue at full strength, so a night street got white-hot
   crowns — the brightest thing in the frame, on a car nothing else lit.
   Streaks, rings and crowns now read the scene's own fog colour, which
   is the one piece of scene state a ShaderMaterial already has each
   frame: its HUE at the effect's own luminance, and its BRIGHTNESS on a
   fourth root (a night scene is ~140x darker in linear light, and a
   plain multiply would delete the effect instead of dimming it).  On the
   night frame the crown band's hottest pixels fell 0.62 -> 0.56 and the
   pixels over 0.55 by 2.8x, with the crowns still plainly there.

3. ONE STAMP REPEATED 500 TIMES IS NOT A FIELD.  Every crown drew the same
   64 px starburst at the same handedness: a row of identical white pine
   cones along the car's roof line (fx/crop_rain_crowns_before.png).  The
   bake is an ATLAS of four crowns, each with its own ray count, angles
   and rim shape, picked per impact and mirrored per impact — 8 stamps
   before anything repeats (fx/crop_rain_crowns_after.png).

4. A SOAK MAY NOT LIGHT THE OBJECT UP, OR CRUSH IT TO BLACK.  three scales
   the diffuse irradiance with `envMapIntensity` as well as the
   reflection, so the reference's 1.2 gain against a 0.45 albedo cut came
   out NET BRIGHTER: the wet car measured 0.613 against a 0.568 dry
   control on the roof and read as white plastic.  The gain is the
   largest that keeps `(1 - darken) * (1 + envGain)` under 1.  At the
   other end the same multiply crushed black trim: a wet tyre's shadow
   side sat at 0.0006 of a night frame, which is a hole.  An albedo floor
   of 0.02 in linear light lifts it to 0.0112 (and the car's flank with
   it) without touching anything mid-toned.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("shader.js", "rain.js")

# Everything the reference's one big probe measured, in one node launch.
_PROBE = """
import * as THREE from 'three';
import { makeRain, makeSplashes, makePuddle, wetten, splashTexture }
    from './lib/rain.js';

// --- rain: instanced, camera-following, deterministic ---------------
const rain = makeRain({ seed: 5, count: 400 });
const rain2 = makeRain({ seed: 5, count: 400 });
const a1 = rain.geometry.attributes.iOff.array;
const a2 = rain2.geometry.attributes.iOff.array;
let rainIdentical = a1.length === a2.length;
for (let i = 0; i < a1.length && rainIdentical; i++) {
  if (a1[i] !== a2[i]) rainIdentical = false;
}
const a3 = makeRain({ seed: 6, count: 400 }).geometry.attributes.iOff.array;
let rainSeedMatters = false;
for (let i = 0; i < a1.length; i++) {
  if (a1[i] !== a3[i]) { rainSeedMatters = true; break; }
}
const rainT0 = rain.material.uniforms.uTime.value;
rain.userData.update(1.7);
const rainT1 = rain.material.uniforms.uTime.value;
// The base quad must be degenerate: the shadow pass draws `position`
// verbatim with its own material, ignoring this vertex shader.
let basePos = 0;
const bp = rain.geometry.attributes.position.array;
for (let i = 0; i < bp.length; i++) basePos = Math.max(basePos, Math.abs(bp[i]));
const rx = rain.geometry.attributes.iExtra;
let jitMin = 1, jitMax = 0;
for (let i = 0; i < rx.count; i++) {
  jitMin = Math.min(jitMin, rx.getZ(i));
  jitMax = Math.max(jitMax, rx.getZ(i));
}

// --- splashes: raycast onto a real surface --------------------------
const car = new THREE.Mesh(new THREE.BoxGeometry(2, 1.4, 4.5),
    new THREE.MeshStandardMaterial());
car.position.set(0, 0.7, 0);
car.updateMatrixWorld(true);
const sp = makeSplashes({ seed: 11, count: 300, surfaces: [car],
    area: { x: 0, z: 0, w: 20, d: 20 } });
const rings = sp.getObjectByName('SplashRings');
const crowns = sp.getObjectByName('SplashCrowns');
const off = rings.geometry.attributes.iOff.array;
const nrm = rings.geometry.attributes.iNrm.array;
const ext = rings.geometry.attributes.iExtra;
let onRoof = 0, onGround = 0, sideways = 0, minPhase = 1, maxPhase = 0;
const phaseHist = [0, 0, 0, 0];
const cells = {};
for (let i = 0; i < ext.count; i++) {
  const y = off[i * 3 + 1];
  if (y > 1.4) onRoof++; else if (y < 0.1) onGround++;
  if (nrm[i * 3 + 1] < 0.4) sideways++;
  const p = ext.getY(i);
  minPhase = Math.min(minPhase, p);
  maxPhase = Math.max(maxPhase, p);
  phaseHist[Math.min(3, Math.floor(p * 4))]++;
  cells[ext.getW(i)] = (cells[ext.getW(i)] || 0) + 1;
}
// A near-vertical face must not collect splashes; the whole field then
// comes from the ground fallback.
const wall = new THREE.Mesh(new THREE.PlaneGeometry(8, 8),
    new THREE.MeshStandardMaterial({ side: THREE.DoubleSide }));
wall.rotation.x = -Math.PI / 2 + 1.4;   // 80 deg off horizontal
wall.position.set(0, 3, 0);
wall.updateMatrixWorld(true);
const spWall = makeSplashes({ seed: 4, count: 120, surfaces: [wall],
    area: { x: 0, z: 0, w: 20, d: 20 }, surfaceBias: 1.0 });
const wallRings = spWall.getObjectByName('SplashRings');
const wallStuck = wallRings ? wallRings.geometry.instanceCount : 0;
const spMixed = makeSplashes({ seed: 4, count: 120, surfaces: [wall],
    area: { x: 0, z: 0, w: 20, d: 20 } });
const mixedRings = spMixed.getObjectByName('SplashRings');
const mixedOff = mixedRings.geometry.attributes.iOff.array;
let mixedHigh = 0;
for (let i = 0; i < mixedOff.length / 3; i++) {
  if (mixedOff[i * 3 + 1] > 0.5) mixedHigh++;
}
const noSurf = makeSplashes({ seed: 4, count: 80, ground: false });
let emptyUpdates = false;
try { noSurf.userData.update(1.7); emptyUpdates = true; } catch (e) {}

// --- what shader.js owes every one of these materials ---------------
const mats = [rain.material, rings.material, crowns.material];
const fromHelper = mats.every((m) => m.userData.astraShader === true);
const outChunks = mats.every(
    (m) => m.fragmentShader.includes('<tonemapping_fragment>')
        && m.fragmentShader.includes('<colorspace_fragment>'));
const sources = mats.flatMap((m) => [m.vertexShader, m.fragmentShader]);
const NL = String.fromCharCode(10);
const includesAlone = sources.every((s) => s.split(NL).every((ln) => {
  const t = ln.trim();
  return !t.includes('#include')
      || (t.indexOf('#include <') === 0 && t.indexOf('>') === t.length - 1);
}));
const fragFirst = mats.every((m) => {
  const lines = m.fragmentShader.split(NL).map((l) => l.trim());
  const i = lines.findIndex((l) => l.indexOf('void main') === 0);
  return i >= 0 && lines[i + 1] === '#include <logdepthbuf_fragment>';
});

// --- the crown atlas -------------------------------------------------
const t1 = splashTexture(48), t2 = splashTexture(48);
let texIdentical = true;
for (let i = 0; i < t1.image.data.length; i++) {
  if (t1.image.data[i] !== t2.image.data[i]) { texIdentical = false; break; }
}
let alphaMax = 0, alphaSum = 0;
for (let i = 3; i < t1.image.data.length; i += 4) {
  alphaMax = Math.max(alphaMax, t1.image.data[i]);
  alphaSum += t1.image.data[i];
}
const sheet = splashTexture(32, 2);
const cellSum = [0, 0, 0, 0];
let borderMax = 0;
for (let y = 0; y < 64; y++) {
  for (let x = 0; x < 64; x++) {
    const a = sheet.image.data[(y * 64 + x) * 4 + 3];
    cellSum[(y < 32 ? 0 : 2) + (x < 32 ? 0 : 1)] += a;
    // Every cell must be transparent at its own border or mip levels
    // bleed one crown into the next.
    if (x % 32 === 0 || x % 32 === 31 || y % 32 === 0 || y % 32 === 31) {
      borderMax = Math.max(borderMax, a);
    }
  }
}

// --- wetten ----------------------------------------------------------
const mk = (hex, rough) => {
  const m = new THREE.MeshStandardMaterial(
      { color: hex, roughness: rough, metalness: 0 });
  m.envMapIntensity = 1;
  return m;
};
const dry = mk(0x808080, 0.9);
const wet = mk(0x808080, 0.9);
wetten(wet, 1.0);
const dryTrim = mk(0x14171b, 0.8);
const wetTrim = mk(0x14171b, 0.8);
wetten(wetTrim, 1.0);
const porous = mk(0x808080, 0.95);
wetten(porous, 1.0);
const polished = mk(0x808080, 0.25);
wetten(polished, 1.0);
const chan = (c) => [c.r, c.g, c.b];
// One shared material under two meshes must be wetted ONCE.
const shared = mk(0x808080, 0.9);
const grp = new THREE.Group();
grp.add(new THREE.Mesh(new THREE.BoxGeometry(), shared));
grp.add(new THREE.Mesh(new THREE.BoxGeometry(), shared));
wetten(grp, 1.0);

// --- puddle ----------------------------------------------------------
const pud = makePuddle(6, 4, { seed: 3 });
const ppos = pud.geometry.attributes.position;
const pcol = pud.geometry.attributes.color;
const pnrm = pud.geometry.attributes.normal;
let yAbs = 0, yNonZero = 0, colMin = 2, colMax = 0, minNy = 1;
let crestCool = 0, troughWarm = 0, tinted = 0;
for (let i = 0; i < ppos.count; i++) {
  const y = ppos.getY(i);
  yAbs = Math.max(yAbs, Math.abs(y));
  if (Math.abs(y) > 1e-6) yNonZero++;
  const r = pcol.getX(i), g = pcol.getY(i), b = pcol.getZ(i);
  colMin = Math.min(colMin, r);
  colMax = Math.max(colMax, r);
  if (Math.abs(r - b) > 1e-4) tinted++;
  if (y > 1e-5 && b > r) crestCool++;
  if (y < -1e-5 && r > b) troughWarm++;
  if (Math.abs(g - 1) > 0.5) tinted--;   // green stays the anchor
  minNy = Math.min(minNy, pnrm.getY(i));
}
const coarse = makePuddle(6, 4, { seed: 3, segments: 12 });
let coarseNy = 1;
const cn = coarse.geometry.attributes.normal;
for (let i = 0; i < cn.count; i++) coarseNy = Math.min(coarseNy, cn.getY(i));
const before = [];
for (let i = 0; i < 40; i++) before.push(ppos.getY(i * 7));
pud.userData.update(1.7);
let moved = 0;
for (let i = 0; i < 40; i++) {
  if (Math.abs(ppos.getY(i * 7) - before[i]) > 1e-7) moved++;
}
const pudB = makePuddle(6, 4, { seed: 3 });
pudB.userData.update(1.7);
let pudIdentical = true;
const qpos = pudB.geometry.attributes.position;
for (let i = 0; i < ppos.count; i++) {
  if (ppos.getY(i) !== qpos.getY(i)) { pudIdentical = false; break; }
}

console.log(JSON.stringify({
  rainIdentical, rainSeedMatters, rainT0, rainT1, basePos,
  rainInstances: rain.geometry.instanceCount,
  rainCulled: rain.frustumCulled,
  rainMeshes: [rain.type, rain.name],
  rainTransparent: rain.material.transparent,
  rainDepthWrite: rain.material.depthWrite,
  rainExtraSize: rx.itemSize, jitMin, jitMax,
  splashKids: sp.children.map((c) => c.name),
  splashInstances: rings.geometry.instanceCount,
  extraSize: ext.itemSize, cells: Object.keys(cells).map(Number).sort(),
  onRoof, onGround, sideways, minPhase, maxPhase, phaseHist,
  wallStuck, mixedHigh,
  mixedCount: mixedRings.geometry.instanceCount,
  noSurfKids: noSurf.children.length, emptyUpdates,
  includesAlone, fragFirst, fromHelper, outChunks,
  texIdentical, alphaMax, alphaMean: alphaSum / (48 * 48),
  texFiltered: t1.minFilter === THREE.LinearMipmapLinearFilter
      && t1.magFilter === THREE.LinearFilter
      && t1.generateMipmaps === true,
  texAniso: t1.anisotropy, texSize: [t1.image.width, sheet.image.width],
  cellSum, borderMax,
  atlasUniform: crowns.material.uniforms.uAtlas.value,
  atlasMap: crowns.material.uniforms.map.value.image.width,
  dryColor: chan(dry.color), wetColor: chan(wet.color),
  dryTrimColor: chan(dryTrim.color), wetTrimColor: chan(wetTrim.color),
  dryRough: dry.roughness, wetRough: wet.roughness,
  porousRough: porous.roughness, polishedRough: polished.roughness,
  wetEnv: wet.envMapIntensity,
  sharedColor: chan(shared.color),
  yAbs, yNonZero, pVerts: ppos.count, colMin, colMax, minNy, moved,
  crestCool, troughWarm, tinted,
  pudIdentical, coarseNy, segDefault: Math.round(Math.sqrt(ppos.count)) - 1,
}));
"""

# The scene our check_shaders.mjs boots.  FOGGED, because the fog uniforms
# are where these shaders read the scene's own colour: without `USE_FOG`
# the branch that does it is never compiled.
_SCENE = """
import * as THREE from 'three';
import { makeRain, makeSplashes, makePuddle, wetten } from './lib/rain.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 20, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  __FOG__
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const car = new THREE.Mesh(new THREE.BoxGeometry(2, 1.4, 4.5),
      new THREE.MeshStandardMaterial({ color: 0x2a3542, roughness: 0.5 }));
  car.position.set(0, 0.7, 0);
  car.updateMatrixWorld(true);
  wetten(car, 0.8);
  scene.add(car);
  const rain = makeRain({ seed: 5, count: 600 });
  const splash = makeSplashes({ seed: 11, count: 200, surfaces: [car] });
  const pud = makePuddle(6, 4, { seed: 3 });
  pud.position.set(3, 0.02, 3);
  scene.add(rain); scene.add(splash); scene.add(pud);
  return {
    scene,
    cameras: [{ name: 'a', position: [8, 3, 10], lookAt: [0, 1, 0],
                fov: 45 }],
    update(t) {
      rain.userData.update(t);
      splash.userData.update(t);
      pud.userData.update(t);
    },
  };
}
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return measure(_PROBE, _LIBS)


# --- the reference's laws, kept ---------------------------------------


def test_every_material_is_assembled_by_the_shader_helper(probe):
    """This host has NO post chain: ACES and the sRGB transfer happen in
    the fragment tail, from the two chunks three appends to its own
    shaders.  A hand-built ShaderMaterial that skips them renders on a
    different curve from every built-in beside it — measured on the
    reference's own hex, (185,206,219) built-in against (85,139,194)
    hand-built."""
    assert probe["fromHelper"], (
        "a rain material was not built by lib/shader.js makeShaderMaterial "
        "(no userData.astraShader)")
    assert probe["outChunks"], (
        "a rain fragment shader is missing tonemapping_fragment / "
        "colorspace_fragment — it renders untonemapped next to every "
        "built-in material in the same frame")


def test_no_include_shares_a_line_and_the_depth_chunk_leads_main(probe):
    """Both are COMPILE failures, and a shader that fails to compile is
    dropped with no error anywhere: the effect is just missing.  Our
    renderer keeps logarithmicDepthBuffer off, so the chunks themselves are
    no-ops here — their placement is not."""
    assert probe["includesAlone"], (
        "an #include shares its line with other code — the GLSL "
        "preprocessor rejects it and the whole shader fails to compile")
    assert probe["fragFirst"], (
        "logdepthbuf_fragment is not the first statement in main()")


def test_the_base_quad_is_degenerate_for_override_passes(probe):
    """Every field here is one instanced quad whose corners travel in
    `aCorner`, because a pass that draws with its own material (the shadow
    map, or a GTAO pass on a host that has one) ignores these vertex
    shaders and draws `position` verbatim — a real unit quad there prints
    at the world origin."""
    assert probe["basePos"] == 0, (
        f"base quad is not degenerate (max |position| {probe['basePos']})")


def test_rain_is_one_instanced_camera_following_mesh(probe):
    """Rain is a per-frame cost the whole scene pays: it must stay ONE draw
    call, and it must not be frustum-culled, because the streak positions
    are computed in the shader (the geometry's own bounds say nothing about
    where the field ended up)."""
    assert probe["rainMeshes"] == ["Mesh", "Rainfall"], probe["rainMeshes"]
    assert probe["rainInstances"] == 400, probe["rainInstances"]
    assert probe["rainCulled"] is False, (
        "frustumCulled left on — the shader-placed field pops out of frame "
        "whenever the unit quad's bounds leave it")
    assert probe["rainTransparent"] and probe["rainDepthWrite"] is False, (
        "streaks must not write depth, or they occlude each other")


def test_the_field_replays_byte_for_byte_from_its_seed(probe):
    """Refine rounds re-render the same scene to compare frames; any
    Math.random in the field would make every round's rain a different
    field and every comparison noise."""
    assert probe["rainIdentical"], "two makeRain(seed 5) fields differ"
    assert probe["rainSeedMatters"], "seed 6 gave the seed-5 field"
    assert probe["texIdentical"], "two splashTexture() bakes differ"
    assert probe["pudIdentical"], (
        "two puddles at the same seed and t differ — ripples are not "
        "reproducible across a re-render")


def test_update_is_the_only_animation_handle(probe):
    """update(t) drives everything; a scene that calls it must see the
    phase move, and one that forgets must still get a formed effect rather
    than a flat plate."""
    assert probe["rainT0"] == 0 and probe["rainT1"] == 1.7, probe
    assert probe["moved"] >= 35, (
        f"puddle update(t) moved only {probe['moved']}/40 sampled vertices")
    assert probe["yNonZero"] > probe["pVerts"] * 0.5, (
        f"only {probe['yNonZero']}/{probe['pVerts']} vertices are displaced "
        "at t=0 — a puddle nobody ticks would render flat")


def test_splashes_land_on_the_surfaces_they_are_given(probe):
    """The whole point of passing meshes: drops must hit the car roof at
    its real height and tilt, not rain THROUGH it onto the road.  And rain
    does not splash on vertical faces, so a wall collects none."""
    assert probe["splashKids"] == ["SplashRings", "SplashCrowns"], probe
    assert probe["onRoof"] > 60, (
        f"only {probe['onRoof']}/300 impacts reached the 1.4 m roof — "
        "surfaceBias is not aiming samples at the surfaces")
    assert probe["onGround"] > 40, (
        f"{probe['onGround']} impacts on the ground — misses must fall "
        "through to the ground plane, not be dropped")
    assert probe["sideways"] == 0, (
        "an impact kept a near-vertical normal; maxSlopeDeg must reject "
        "walls and undersides")
    assert probe["wallStuck"] == 0, (
        f"{probe['wallStuck']} splashes stuck to an 80-degree face")
    assert probe["mixedHigh"] == 0, probe["mixedHigh"]
    assert probe["mixedCount"] == 120, (
        f"the field shrank to {probe['mixedCount']}/120 around a steep face "
        "— rejected samples must be re-drawn, not dropped")
    assert probe["noSurfKids"] == 0 and probe["emptyUpdates"], (
        "no surfaces and ground:false must yield an EMPTY group that still "
        "ticks, not splashes hanging in the air or a crash")


def test_splash_phases_span_the_whole_cycle(probe):
    """Stills are the delivery frame.  If every impact shared a phase the
    frozen frame would show one synchronized pulse (or nothing at all);
    spread phases put rings at every radius in the SAME frame, which is
    what reads as continuous rainfall."""
    assert probe["minPhase"] < 0.1 and probe["maxPhase"] > 0.9, probe
    quarters = probe["phaseHist"]
    assert min(quarters) > 40, (
        f"phase quartiles {quarters} — impacts are clustered in the cycle, "
        "so a frozen frame shows only part of the splash range")


def test_the_crown_texture_is_filtered_like_every_other_datatexture(probe):
    """DataTexture defaults to NearestFilter with NO mipmaps.  A 64 px
    crown seen 20 m down the street then stair-steps into hard blocks."""
    assert probe["texFiltered"], "crown texture left on Nearest/no mipmaps"
    assert probe["texAniso"] >= 8, probe["texAniso"]
    assert probe["alphaMax"] > 200, (
        f"crown never reaches opaque (max alpha {probe['alphaMax']}) — it "
        "would read as a smudge")
    assert 2 < probe["alphaMean"] < 60, (
        f"crown alpha mean {probe['alphaMean']} — a crown is thin spray on "
        "a transparent background, not a filled blob")


def test_puddle_ripples_are_real_geometry(probe):
    """The ripples must survive into a still under the scene's OWN
    lighting, so they are vertex displacement with recomputed normals (a
    scrolling normal map would need an animation to read), plus a
    crest-brightening vertex colour so the rings stay legible even under
    flat overcast light."""
    assert 0.002 < probe["yAbs"] < 0.05, (
        f"ripple amplitude {probe['yAbs']} m — millimetre waves on a "
        "puddle, not surf")
    assert probe["minNy"] < 0.999, (
        f"steepest ripple normal {probe['minNy']} — the surface is flat to "
        "the shading, so no crest can catch a highlight")
    assert probe["coarseNy"] > probe["minNy"], (
        f"a 12-segment puddle ({probe['coarseNy']}) is not flatter than the "
        f"default {probe['segDefault']}-segment one ({probe['minNy']})")
    assert probe["colMin"] < 0.95 and probe["colMax"] > 1.05, (
        f"crest/trough vertex colours {probe['colMin']}..{probe['colMax']} "
        "— the rings lose their fallback readability")


def test_a_square_area_given_as_one_number_is_not_nan():
    """`area` is documented as {x, z, w, d}.  A number is truthy, so
    `area: 12` read x and w as undefined and every ground-fallback impact
    landed at NaN — 520 of 780 offset floats non-finite, with 260 instances
    still created and drawn."""
    out = measure("""
import { makeSplashes } from './lib/rain.js';
const finite = (g) => {
  let bad = 0, total = 0;
  g.traverse((o) => {
    const a = o.geometry && o.geometry.getAttribute('iOff');
    if (!a) return;
    for (let i = 0; i < a.array.length; i++) {
      total++; if (!Number.isFinite(a.array[i])) bad++;
    }
  });
  return { bad, total };
};
const num = finite(makeSplashes({ area: 12, y: 0, seed: 4 }));
const rect = finite(makeSplashes({ area: { x: 0, z: 0, w: 12, d: 12 },
                                   y: 0, seed: 4 }));
console.log(JSON.stringify({ num, rect }));
""", _LIBS)
    assert out["num"]["total"] > 0, "the probe found no offsets to check"
    assert out["num"]["bad"] == 0, out["num"]
    assert out["rect"]["bad"] == 0, out["rect"]


# --- the port's own laws ----------------------------------------------


def test_a_streak_is_bright_at_the_head_and_dark_down_the_tail():
    """PORT LAW 1.  Against this host's 0.94 sky a pale streak is worth
    3/255 and reads as nothing; the only mark that prints there is a dark
    one.  The split has to run along the streak's LENGTH — it is about two
    pixels wide, so a core/rim split across it averages back to one tone
    inside a single pixel.  vUv.y = 1 is the leading end, so the tint runs
    from the rim colour at the tail to the streak colour at the head, and
    the alpha ramp lifts the tail's floor (0.45 -> 0.62) so the dark half
    has enough coverage to print.  Measured against a no-rain control on
    the mid camera: 0.82% of the sky band touched at a mean 3.4/255,
    against 2.03% at 12.8/255 with a peak of 86/255 — and every touched
    pixel DARKER than the control, which is the only direction a drop can
    move a blown sky."""
    out = measure("""
import { makeRain } from './lib/rain.js';
const m = makeRain({ count: 200 }).material;
const f = m.fragmentShader;
const lum = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
console.log(JSON.stringify({
  // the tail-to-head mix, on the length axis
  mixesOnLength: /mix\\(rainAirHue\\(uRim[\\s\\S]{0,120}vUv\\.y\\)/.test(f),
  head: lum(m.uniforms.uColor.value),
  tail: lum(m.uniforms.uRim.value),
  tailFloor: /0\\.62 \\+ 0\\.38 \\* vUv\\.y/.test(f),
  dithered: f.includes('astraHash21(gl_FragCoord.xy)'),
  // per-streak hue, so the field is not one flat hatch
  jitters: f.includes('vJit'),
}));
""", _LIBS)
    assert out["mixesOnLength"], (
        "the streak tint does not run from uRim to uColor along vUv.y")
    assert out["tailFloor"] and out["dithered"] and out["jitters"], out
    # The tail has to be far below the sky it crosses, and the head far
    # above the ground it crosses.
    assert out["tail"] < 0.02, out
    assert out["head"] > 0.6, out


def test_every_tint_here_reads_the_scene_and_survives_a_scene_without_fog():
    """PORT LAW 2.  Water is colourless; a streak, a ring and a crown are
    all showing you the sky.  The fog uniforms are the one piece of scene
    state a ShaderMaterial has for free every frame, so all three take the
    fog colour's HUE at their own luminance and its BRIGHTNESS on a fourth
    root.  Both reads sit under `#ifdef USE_FOG` — a scene with no fog
    never defines it, and an unguarded `fogColor` there is a compile error
    that would delete the whole effect."""
    out = measure("""
import { makeRain, makeSplashes } from './lib/rain.js';
const rain = makeRain({ count: 100 }).material;
const sp = makeSplashes({ count: 40, seed: 2 });
const ring = sp.getObjectByName('SplashRings').material;
const crown = sp.getObjectByName('SplashCrowns').material;
const mats = { rain, ring, crown };
const out = {};
for (const [k, m] of Object.entries(mats)) {
  const f = m.fragmentShader;
  const body = f.slice(f.indexOf('rainAirHue(vec3'));
  out[k] = {
    hue: (f.match(/rainAirHue\\(/g) || []).length,
    lit: (f.match(/rainAirLit\\(/g) || []).length,
    guarded: (body.match(/#ifdef USE_FOG/g) || []).length,
    air: m.uniforms.uAir ? m.uniforms.uAir.value : null,
    dim: m.uniforms.uDim ? m.uniforms.uDim.value : null,
  };
}
console.log(JSON.stringify(out));
""", _LIBS)
    for k in ("rain", "ring", "crown"):
        # one definition + at least one call
        assert out[k]["hue"] >= 2, (k, out[k])
        # both helpers guard their scene read
        assert out[k]["guarded"] == 2, (k, out[k])
        assert 0 < out[k]["air"] <= 1, (k, out[k])
    # Streaks keep their own brightness — they are lit by whatever is
    # behind them.  Thrown water is a surface, and it goes dark with the
    # scene: a night street had white-hot crowns before this.
    assert out["rain"]["lit"] == 1, out["rain"]
    assert out["ring"]["lit"] == 2 and out["crown"]["lit"] == 2, out
    assert 0.2 <= out["crown"]["dim"] <= 0.5, out["crown"]


def test_the_crown_field_is_not_one_stamp_repeated(probe):
    """PORT LAW 3.  500 impacts drew the same 64 px starburst at the same
    handedness, which along a car's roof line is a row of identical white
    pine cones.  The bake is an atlas — four crowns, each with its own ray
    count, angles and rim — picked per impact and mirrored per impact.
    Cells must be transparent at their borders, or a mip level averages
    two crowns into one blur."""
    assert probe["atlasUniform"] == 2, probe["atlasUniform"]
    assert probe["atlasMap"] == 128, (
        f"crown atlas is {probe['atlasMap']} px — a 2x2 sheet of 64 px "
        "cells is 128")
    assert probe["texSize"] == [48, 64], probe["texSize"]
    sums = probe["cellSum"]
    assert min(sums) > 0, sums
    assert len(set(sums)) == 4, (
        f"atlas cells carry {len(set(sums))} distinct crowns, not 4: {sums}")
    assert max(sums) < min(sums) * 2.2, (
        f"one crown is more than twice another's mass {sums} — a field of "
        "wildly unequal splashes reads as an error, not as variation")
    assert probe["borderMax"] <= 2, (
        f"a cell reaches alpha {probe['borderMax']} at its own border — mip "
        "levels will bleed the neighbouring crown into it")
    assert probe["cells"] == [0, 1, 2, 3], (
        f"impacts used atlas cells {probe['cells']} — all four must appear")
    assert probe["extraSize"] == 4 and probe["rainExtraSize"] == 3, probe
    assert probe["jitMin"] < 0.05 and probe["jitMax"] > 0.95, probe


def test_a_soak_darkens_the_thing_it_soaks(probe):
    """PORT LAW 4.  Wet is darker AND shinier, and three gives the second
    half no knob that does not also do the first: `envMapIntensity` scales
    the diffuse irradiance with the reflection.  At the reference's 1.2
    gain the product (1 - 0.45) * 2.2 = 1.21 came out net BRIGHTER, and on
    this host that measured 0.613 of wet roof against a 0.568 dry control
    — a white plastic slab.  The gain is the largest one that keeps the
    product under 1 while still doubling nothing: 0.6."""
    dry_l = sum(probe["dryColor"]) / 3
    wet_l = sum(probe["wetColor"]) / 3
    assert wet_l < dry_l * 0.75, (probe["dryColor"], probe["wetColor"])
    assert probe["wetEnv"] > 1.5, (
        f"envMapIntensity {probe['wetEnv']} — without the reflection boost "
        "the surface just goes dark")
    assert (wet_l / dry_l) * probe["wetEnv"] < 1.0, (
        f"albedo x env = {(wet_l / dry_l) * probe['wetEnv']:.3f} — a soak "
        "that lands over 1 lights the object up instead of wetting it")
    assert probe["sharedColor"] == probe["wetColor"], (
        "a material shared by two meshes was wetted twice, so shared "
        "materials darken by however many meshes happen to use them")


def test_a_soak_leaves_a_floor_under_black_trim(probe):
    """PORT LAW 4, the other end.  The same multiply that darkens mid tones
    correctly takes near-black trim to nothing: a wet tyre's shadow side
    measured 0.0006 of a night frame, and the car's flank with it — a hole,
    not a dark surface.  Water on black rubber is still lit by the sky.
    The floor is on the albedo in LINEAR light, so it lifts only what was
    already under it."""
    trim = probe["wetTrimColor"]
    dry = probe["dryTrimColor"]
    assert max(trim) >= 0.0199, (
        f"wet trim albedo {trim} — under the 0.02 floor, so it renders as "
        "a hole in the frame")
    assert max(trim) > max(dry), (
        f"the floor did not engage: dry {dry}, wet {trim}")
    # ...and a mid-grey is not lifted by it.
    assert sum(probe["wetColor"]) / 3 < sum(probe["dryColor"]) / 3, probe


def test_a_soaked_brick_is_not_a_mirror(probe):
    """One flat roughness floor for every material made brick, dirt and car
    paint identical the moment they got wet.  A porous surface holds water
    in its own relief and comes back a damp sheen; a polished one is
    already close to the floor.  Each keeps a share of its own roughness,
    with the floor as the hard stop."""
    assert probe["wetRough"] < 0.3 < probe["dryRough"], probe
    assert probe["porousRough"] > probe["wetRough"] > probe["polishedRough"], (
        f"porous {probe['porousRough']}, mid {probe['wetRough']}, polished "
        f"{probe['polishedRough']} — wetting flattened them into one look")
    assert probe["polishedRough"] >= 0.1 - 1e-9, probe["polishedRough"]


def test_the_puddle_tints_its_crests_and_its_troughs(probe):
    """A crest tips toward the sky it mirrors and goes cool; a trough shows
    the bed under it and stays warm.  The reference multiplied all three
    channels by one number, which leaves the whole pool on a single flat
    hue — the one thing standing water never has."""
    assert probe["tinted"] > probe["pVerts"] * 0.9, (
        f"only {probe['tinted']}/{probe['pVerts']} vertices carry a tint — "
        "the vertex colours are achromatic")
    assert probe["crestCool"] > 0 and probe["troughWarm"] > 0, probe


def test_the_whole_field_compiles_on_the_real_renderer():
    """A custom shader that fails to compile is discarded with no error at
    all — the effect is simply absent.  Both scenes have to be checked: the
    fog uniforms are where these shaders read the scene's colour, and the
    branch that does it exists only when the renderer defines USE_FOG, so
    the fogged scene compiles the read and the fog-less one compiles the
    fallback."""
    fogged = _SCENE.replace(
        "__FOG__", "scene.fog = new THREE.FogExp2(0xc9d6e0, 0.02);")
    code, out = compile_scene(fogged, _LIBS)
    assert code == 0, out
    assert '"custom_materials":3' in out.replace(" ", ""), out
    assert "WARN" not in out, out
    code, out = compile_scene(_SCENE.replace("__FOG__", ""), _LIBS)
    assert code == 0, out
