"""grass.js — the field that has to read as a field, close up and from above.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_grass_lib.py).  Its properties are kept whole: the whole field is
ONE instanced draw call with ``position`` pinned at the origin, the clump field
decides where grass grows and how tall, the mat under the blades carries that
same field, both stand on the caller's ground, a seed is a promise, one
``tick`` drives both materials, and blade shadows stay opt-in.

Their compile case is restored at the end of this file — ``_probe.compile_scene``
stages the fixture as a workspace ``src/scene.js``, the shape
``runtime_js/check_shaders.mjs`` boots (16 programs, 4 custom materials).  What is
asserted before it is the STRUCTURE of the GLSL, read back off ``onBeforeCompile``,
and the frames are at fx/out/grass/ (day and night).

The last four tests are new, and each pins a change the port made.  The first
of them is a real bug: the blade's shading normal was built as
``cross(grTan, grSide)``, which is the BACK face of the strip the vertex shader
sweeps, and DOUBLE_SIDED cannot rescue that — three multiplies ``vNormal`` by
``gl_FrontFacing``, which is decided by the same winding the normal was built
from, so the two flip together and the product is invariant.  Every blade shaded
by its far side.  Backlit that reads as soft; front-lit it is a black field, and
on our night rig the whole meadow came back at luminance 0.009 next to a lit
rock and lit ground.  The other three pin the grading: MeshPhysicalMaterial for
one uniform (``specularIntensity`` — a Standard dielectric's F90 is pinned at
1.0 and a field seen edge-on from a low camera reflected the sky at full
strength over every pixel), the backlight term that reads the scene's own key
light rather than a hardcoded sun, and the one metre-scale colour field that
blades and mat now share.
"""
from __future__ import annotations

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "grass.js")

# Run a material's patch chain the way three does, and hand back the GLSL.
_COMPILE = """
const compile = (mat) => {
  const shader = {
    uniforms: {},
    vertexShader: '#include <begin_vertex>\\n',
    fragmentShader: 'void main() {\\n#include <color_fragment>\\n}\\n',
  };
  mat.onBeforeCompile(shader, {});
  return shader;
};
"""


def test_the_whole_field_of_blades_is_one_draw_call():
    """Thousands of blades are affordable only as ONE instanced mesh;
    a per-tuft mesh would spend the scene's draw budget on grass."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const g = makeGrass({ extent: 4, density: 600, seed: 3 });
const meshes = [];
g.traverse((o) => { if (o.isMesh) meshes.push(o); });
const blades = g.getObjectByName('Blades');
console.log(JSON.stringify({
  names: g.children.map((c) => c.name),
  meshes: meshes.length,
  instanced: !!blades.geometry.isInstancedBufferGeometry,
  count: blades.geometry.instanceCount,
  perBlade: blades.geometry.index.count / 3,
  attrs: Object.keys(blades.geometry.attributes).sort(),
  culled: blades.frustumCulled,
  box: [blades.geometry.boundingBox.min.toArray(),
        blades.geometry.boundingBox.max.toArray()],
  hasTick: typeof g.userData.tick === 'function',
}));
""", _LIBS)
    assert out["names"] == ["Blades", "Sward"]
    # One mesh for every blade plus the mat under them, and nothing else.
    assert out["meshes"] == 2, out
    assert out["instanced"] and out["count"] > 3000, out
    assert out["perBlade"] == 8, out
    assert out["attrs"] == ["aCorner", "iPos", "iShape", "iVar", "normal",
                            "position", "uv"]
    # position is zero, so a derived bound would be a point at the
    # origin and the field would vanish the moment it left the centre.
    assert out["culled"] is False
    assert out["box"][0][0] == -2 and out["box"][1][2] == 2, out
    assert out["box"][1][1] > 0.3, out
    assert out["hasTick"]


def test_the_blades_keep_position_at_the_origin():
    """Any pass that redraws with an override material ignores this vertex
    shader, so a real blade left in `position` would stack every copy at the
    world origin and burn a slab there."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const b = makeGrass({ extent: 3, density: 400 }).getObjectByName('Blades');
const g = b.geometry;
console.log(JSON.stringify({
  posAllZero: Array.from(g.attributes.position.array).every((v) => v === 0),
  corners: g.attributes.aCorner.count,
  perInstance: [g.attributes.iPos.count, g.attributes.iShape.count,
                g.attributes.iVar.count],
  instances: g.instanceCount,
}));
""", _LIBS)
    assert out["posAllZero"], out
    # A 1x4 strip: two corners per row, five rows.
    assert out["corners"] == 10
    assert out["perInstance"] == [out["instances"]] * 3


def test_clumps_decide_where_grass_grows_and_how_tall():
    """A field of identical blades is a carpet. Grass grows in tussocks:
    taller and greener inside one, sparser and dryer between them.
    `patchy` moves blades OUT of the open ground and into the clumps
    without changing how much grass there is — that is `density`'s job,
    and it stays true to its name at any patchiness."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
function field(patchy) {
  const b = makeGrass({ extent: 8, density: 500, seed: 4, patchy })
      .getObjectByName('Blades');
  const n = b.geometry.instanceCount;
  const shape = b.geometry.attributes.iShape.array;
  const vary = b.geometry.attributes.iVar.array;
  const rows = [];
  for (let i = 0; i < n; i++) rows.push([vary[i * 4 + 2], shape[i * 4]]);
  rows.sort((a, b2) => a[0] - b2[0]);
  const mean = (from, to) => {
    let s = 0;
    for (let i = from; i < to; i++) s += rows[i][1];
    return s / (to - from);
  };
  const third = Math.floor(n / 3);
  return {
    perM2: n / 64,
    onBareGround: rows.filter((r) => r[0] > 0.6).length / n,
    greenestH: mean(0, third),
    driestH: mean(n - third, n),
  };
}
const open = field(0.05), bare = field(0.85);
console.log(JSON.stringify({ open, bare }));
""", _LIBS)
    open_, bare = out["open"], out["bare"]
    # Sparser between the clumps: patchiness thins the open ground, and
    # the blades it takes from there stand in the tussocks instead.
    assert bare["onBareGround"] < open_["onBareGround"] * 0.6, out
    for f in (open_, bare):
        assert 400 < f["perM2"] < 520, out
        # Taller in the clumps, in both fields.
        assert f["greenestH"] > f["driestH"] * 1.3, out


def test_the_mat_carries_the_same_clump_field_as_the_blades():
    """From above, blades show only their tips: what makes the field
    read as continuous rather than as a scatter of dots is the mat under
    them, which has to be green exactly where the blades are dense."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const g = makeGrass({ extent: 8, density: 400, seed: 4 });
const sward = g.getObjectByName('Sward');
const clump = sward.geometry.attributes.aClump;
const pos = sward.geometry.attributes.position;
const blades = g.getObjectByName('Blades');
const iPos = blades.geometry.attributes.iPos.array;
const iVar = blades.geometry.attributes.iVar.array;
// Green mat under green blades: bin the mat by its own clump value and
// ask what the blades standing in each bin look like.
let lowSum = 0, lowN = 0, highSum = 0, highN = 0;
for (let i = 0; i < blades.geometry.instanceCount; i++) {
  const x = iPos[i * 3], z = iPos[i * 3 + 2];
  let best = 0, bestD = 1e9;
  for (let v = 0; v < pos.count; v++) {
    const d = (pos.getX(v) - x) ** 2 + (pos.getZ(v) - z) ** 2;
    if (d < bestD) { bestD = d; best = clump.array[v]; }
  }
  if (best < 0.35) { lowSum += iVar[i * 4 + 2]; lowN++; }
  if (best > 0.65) { highSum += iVar[i * 4 + 2]; highN++; }
}
console.log(JSON.stringify({
  verts: pos.count,
  clumpMin: Math.min(...clump.array),
  clumpMax: Math.max(...clump.array),
  dryOnBareMat: lowSum / Math.max(lowN, 1),
  dryOnGreenMat: highSum / Math.max(highN, 1),
  binned: [lowN, highN],
}));
""", _LIBS)
    assert out["verts"] > 200, out
    # The field is calibrated on the patch it covers, so a patch always
    # gets the whole range instead of one flat mid-grey wash.
    assert out["clumpMin"] < 0.15 and out["clumpMax"] > 0.85, out
    assert min(out["binned"]) > 20, out
    # Blades standing on green mat are the green ones.
    assert out["dryOnGreenMat"] < out["dryOnBareMat"] - 0.25, out


def test_blades_and_mat_stand_on_the_callers_ground():
    """Grass that ignores `heightAt` floats over a slope or buries
    itself in it — the one defect a still frame shows immediately."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const h = (x, z) => 0.8 * Math.sin(x * 0.3) * Math.cos(z * 0.21) + 0.9;
const g = makeGrass({ extent: 10, density: 300, seed: 8, heightAt: h });
const iPos = g.getObjectByName('Blades').geometry.attributes.iPos.array;
let bladeErr = 0;
for (let i = 0; i < iPos.length; i += 3) {
  bladeErr = Math.max(bladeErr,
      Math.abs(iPos[i + 1] - h(iPos[i], iPos[i + 2])));
}
const p = g.getObjectByName('Sward').geometry.attributes.position;
let matErr = 0, lift = 0;
for (let v = 0; v < p.count; v++) {
  const d = p.getY(v) - h(p.getX(v), p.getZ(v));
  matErr = Math.max(matErr, Math.abs(d - 0.012));
  lift = Math.max(lift, d);
}
const flat = makeGrass({ extent: 6, density: 300, seed: 8 });
const fPos = flat.getObjectByName('Blades').geometry.attributes.iPos.array;
let flatMax = 0;
for (let i = 1; i < fPos.length; i += 3) flatMax = Math.max(flatMax, fPos[i]);
console.log(JSON.stringify({
  bladeErr, matErr, lift, flatMax,
  boxLow: g.getObjectByName('Blades').geometry.boundingBox.min.y,
}));
""", _LIBS)
    assert out["bladeErr"] < 1e-4, out
    assert out["matErr"] < 1e-4, out
    # Just clear of the ground: level with it the mat z-fights, higher
    # and the blades look planted in a tray.
    assert 0.005 < out["lift"] < 0.05, out
    # No heightAt: the field rests exactly on y = 0.
    assert out["flatMax"] == 0, out
    assert out["boxLow"] <= 0.11, out


def test_two_fields_with_one_seed_are_identical():
    """Determinism is the contract for every shipped factory: a re-run
    that reshuffles the blades is a re-run nobody can compare against."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const fieldOf = (s) => Array.from(
    makeGrass({ extent: 5, density: 400, seed: s })
        .getObjectByName('Blades').geometry.attributes.iShape.array);
const a = fieldOf(6), b = fieldOf(6), c = fieldOf(7);
console.log(JSON.stringify({
  same: JSON.stringify(a) === JSON.stringify(b),
  differs: JSON.stringify(a) !== JSON.stringify(c),
  n: a.length,
}));
""", _LIBS)
    assert out["same"] and out["differs"] and out["n"] > 0


def test_tick_advances_every_material_in_the_group():
    """The bend lives entirely in the vertex shader, so an un-advanced
    uTime is a field frozen mid-gust, not a field at rest."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const g = makeGrass({ extent: 4, density: 200 });
const times = () => {
  const out = [];
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      const u = m && m.userData && m.userData.uniforms;
      if (u && u.uTime) out.push(u.uTime.value);
    }
  });
  return out;
};
const before = times();
g.userData.tick(2.5);
console.log(JSON.stringify({ before, after: times() }));
""", _LIBS)
    assert out["before"] == [0, 0]
    assert out["after"] == [2.5, 2.5]


def test_blade_shadows_are_opt_in_and_displaced_when_on():
    """Wheat set the precedent: tens of thousands of instances in the
    shadow map are a real SwiftShader cost, so blades cast only when
    asked. Off, the mesh stays OUT of the map (its depth pass would draw
    the zero-position quads); on, it casts through a depth material
    carrying the blade displacement on the surface's own uniform map —
    anything else is no shadow, or one frozen at t = 0."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const pick = (g) => {
  let m;
  g.traverse((o) => { if (o.name === 'Blades') m = o; });
  return m;
};
const off = pick(makeGrass({ extent: 6, density: 40, seed: 3 }));
const on = pick(makeGrass({ extent: 6, density: 40, seed: 3,
                            shadows: true }));
console.log(JSON.stringify({
  offCasts: off.castShadow, offDepth: !!off.customDepthMaterial,
  onCasts: on.castShadow,
  onShared: !!on.customDepthMaterial
      && on.customDepthMaterial.userData.uniforms
          === on.material.userData.uniforms,
}));
""", _LIBS)
    assert not out["offCasts"] and not out["offDepth"], out
    assert out["onCasts"] and out["onShared"], out


# --- what the port changed -------------------------------------------------

def test_the_shading_normal_is_wound_the_way_the_strip_is():
    """THE bug this port fixed.  The sweep sends the plane's x to `grSide`
    and its y to `grTan`, so the face three rasterises has the normal
    `cross(grSide, grTan)`; the reference wrote `cross(grTan, grSide)`, the
    other one.  DOUBLE_SIDED cannot rescue it — three multiplies vNormal by
    gl_FrontFacing, and gl_FrontFacing is decided by the same winding the
    normal was built from, so both flip together and the product is
    invariant.  Every blade shaded by its far face: soft when backlit, and
    a BLACK field under any key light on the camera's side (measured on our
    night rig, the whole meadow at luminance 0.009 beside a lit rock and lit
    ground; 0.049 once wound right, and the frame's dark fraction 0.42 ->
    0.19).  The cup term flips with it or the channel is inside out."""
    out = measure(_COMPILE + """
import { makeGrass } from './lib/grass.js';
const vs = compile(makeGrass({ extent: 4, density: 100 })
    .getObjectByName('Blades').material).vertexShader;
console.log(JSON.stringify({
  right: vs.includes('cross(grSide, grTan) - grSide * aCorner.x'),
  wrong: vs.includes('cross(grTan, grSide)'),
  guarded: vs.includes('#ifndef FLAT_SHADED'),
}));
""", _LIBS)
    assert out["right"], out
    assert not out["wrong"], out
    # The depth pass defines FLAT_SHADED, and a depth shader has no vNormal.
    assert out["guarded"], out


def test_the_blade_material_turns_down_the_sky_it_cannot_see():
    """Measured on our renderer with the blade albedo forced to BLACK and the
    environment off, the field still came back at luminance 0.36, saturation
    0.07 — a flat neutral sheen that was 70% of every pixel, which is why no
    albedo could make this grass read as green.  A Standard dielectric's F90
    is pinned at 1.0, so a field seen nearly edge-on from a low camera
    reflects the sky at full strength everywhere; `specularIntensity` (a
    Physical uniform, scaling F0 AND F90) is the only knob that turns it
    down, and a blade of grass is a waxy leaf, not polished glass.  The env
    cut alongside it is the AO this host has no pass for — a blade standing
    in a sward sees a slot of sky, not a dome."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const g = makeGrass({ extent: 4, density: 100 });
const b = g.getObjectByName('Blades').material;
const s = g.getObjectByName('Sward').material;
console.log(JSON.stringify({
  type: b.type, specular: b.specularIntensity, blade: b.envMapIntensity,
  sward: s.envMapIntensity, doubleSided: b.side === 2,
  rough: [b.roughness, s.roughness], metal: [b.metalness, s.metalness],
}));
""", _LIBS)
    assert out["type"] == "MeshPhysicalMaterial", out
    assert 0.1 <= out["specular"] <= 0.45, out
    # Both cut, and the mat — the bottom of the sward — further than the
    # blades standing in it, whose one uniform is weighted to their tips.
    assert 0.2 <= out["sward"] < out["blade"] < 1.0, out
    assert out["doubleSided"] and out["metal"] == [0, 0], out
    assert min(out["rough"]) > 0.6, out


def test_the_backlight_reads_the_scenes_own_key_light():
    """A blade is a fraction of a millimetre of sap, and with the shading
    normal finally facing the eye a BACKLIT blade's diffuse response is
    nearly nothing — which is what backlit means.  The glow through it is
    therefore not a garnish, it is the light on the field, and it goes in as
    emitted radiance because albedo cannot add light to a face the sun is
    behind.  Direction AND colour come off `directionalLights[0]` in view
    space, so it can never disagree with the sun and a night rig's moon
    drives it too; the two gates keep it off a front-lit field, and the
    depth weight keeps it out of the roots (a field that glows to its roots
    is fog)."""
    out = measure(_COMPILE + """
import { makeGrass } from './lib/grass.js';
const fs = compile(makeGrass({ extent: 4, density: 100 })
    .getObjectByName('Blades').material).fragmentShader;
const emit = fs.split('totalEmissiveRadiance +=')[1] || '';
console.log(JSON.stringify({
  emits: fs.includes('totalEmissiveRadiance +='),
  guarded: fs.includes('#if NUM_DIR_LIGHTS > 0'),
  readsColor: emit.includes('directionalLights[0].color'),
  readsDir: fs.includes('grL = directionalLights[0].direction'),
  gates: emit.includes('grThru') && emit.includes('grBeam'),
  depthWeighted: emit.includes('grDepth * grDepth'),
  hardcoded: /vec3\\s*\\(\\s*1\\.0\\s*,\\s*0\\.9/.test(emit),
}));
""", _LIBS)
    assert out["emits"] and out["guarded"], out
    assert out["readsColor"] and out["readsDir"], out
    assert out["gates"] and out["depthWeighted"], out
    assert not out["hardcoded"], out


def test_the_blades_and_the_mat_break_colour_on_one_field():
    """A blade is a couple of pixels wide and its own hue jitter averages
    straight back out — measured, quadrupling the per-blade swing moved the
    frame by under two degrees.  What survives is the METRE: warm where the
    sun lands, cool where only the sky reaches, and bands of straw that have
    nothing to do with how dense the grass is.  Both shaders have to read
    that field at the same scale, off the same world XZ, or the mat lays one
    meadow's colour over another's — the blades sample it at their ROOT, not
    at the bending spine, so the wind cannot slide the colour about."""
    out = measure(_COMPILE + """
import { makeGrass } from './lib/grass.js';
const g = makeGrass({ extent: 4, density: 100 });
const blade = compile(g.getObjectByName('Blades').material);
const sward = compile(g.getObjectByName('Sward').material);
const HUE = 'astraHueBreak(', DRY = 'astraFbm2(';
console.log(JSON.stringify({
  bladeHue: blade.fragmentShader.includes(
      'astraHueBreak(grC, vGrassW, 0.55, 0.62)'),
  swardHue: sward.fragmentShader.includes(
      'astraHueBreak(swCol, vSward.xy, 0.55, 0.62)'),
  bladeDry: blade.vertexShader.includes('astraFbm2(vGrassW * 0.35 + 5.0, 2)'),
  swardDry: sward.fragmentShader.includes(
      'astraFbm2(vSward.xy * 0.35 + 5.0, 2)'),
  rootNotSpine: blade.vertexShader.includes(
      'vGrassW = (modelMatrix * vec4(grBase, 1.0)).xz'),
}));
""", _LIBS)
    assert out["bladeHue"] and out["swardHue"], out
    assert out["bladeDry"] and out["swardDry"], out
    assert out["rootNotSpine"], out


def test_one_wind_is_read_in_either_spelling():
    """`windOf` is exported so a scene's meadow, reeds and trees can take the
    SAME wind; a library that re-implements this reader is a library that
    will drift away from it.  Both spellings, a Vector2 or a pair, and a
    degenerate direction that must not produce NaN."""
    out = measure("""
import * as THREE from 'three';
import { windOf } from './lib/grass.js';
const r = (w) => { const o = windOf(w);
  return [+o.dir.x.toFixed(4), +o.dir.y.toFixed(4), o.amp, o.speed]; };
console.log(JSON.stringify({
  bare: r(2),
  none: r(undefined),
  arr: r({ dir: [0, 3], strength: 1, speed: 2 }),
  vec: r({ dir: new THREE.Vector2(0, 3), strength: 1, speed: 2 }),
  zero: r({ dir: [0, 0] }),
}));
""", _LIBS)
    # A bare number is a strength multiplier on the default breeze.
    assert out["bare"] == [pytest.approx(0.9113, abs=1e-3),
                           pytest.approx(0.4101, abs=1e-3), 1.0, 1]
    assert out["none"][2] == 0.5 and out["none"][3] == 1
    # [x, z] and Vector2 are the same direction, normalised.
    assert out["arr"] == out["vec"] == [0, 1, 0.5, 2]
    # A zero direction falls back to +X instead of NaN.
    assert out["zero"][:2] == [1, 0]

# A FOGGED, shadow-casting scene: `USE_FOG` and the shadow branches only exist
# in a program built from one, and a patch that drops a chunk compiles fine in
# an unfogged fixture and renders as a sticker in a real scene.
_COMPILE_SCENE = """
import * as THREE from 'three';
import { tickShaders } from './lib/shader.js';
import { makeGrass } from './lib/grass.js';

export const BOUNDS = { min: [-30, 0, -30], max: [30, 20, 30] };
export function heightAt(x, z) { return Math.sin(x * 0.1) * 0.3; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.004);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 1.0));
  const sun = new THREE.DirectionalLight(0xffffff, 3);
  sun.position.set(10, 20, 10); sun.castShadow = true; scene.add(sun);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 80),
                                new THREE.MeshStandardMaterial({ color: 0x6d6a52 }));
  ground.rotation.x = -Math.PI / 2; ground.receiveShadow = true; scene.add(ground);
  const g = makeGrass({ extent: 20, density: 120, heightAt, shadows: true });
  scene.add(g);
  return {
    scene,
    cameras: [{ name: 'a', position: [12, 5, 14], lookAt: [0, 2, 0], fov: 45 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_every_program_compiles_on_the_gpu():
    """Two materials (blade and, behind `shadows: true`, the custom depth material
    that makes the blades cast what they actually are) plus a vertex texture fetch
    for the gust field.  Only the GPU can say the GLSL is legal.

    The reference had this case and the port dropped it: `_probe.shader_check`
    staged only `src/fixture.js` while `check_shaders.mjs` boots the workspace's
    `src/scene.js`, so it could never run.  `_probe.compile_scene` stages the
    scene, so the case is back (consolidation, 2026-09-01)."""
    code, out = compile_scene(_COMPILE_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
