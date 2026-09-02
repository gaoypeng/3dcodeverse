"""canopy.js — the crown has to read as leaves, not as a green solid.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_canopy_lib.py).  The three properties a convex crown cannot have —
a broken silhouette, light between the leaves, colour that varies leaf to leaf
— are pinned exactly as they were.

Their two ``scene_check`` cases wanted a GPU compile, and it is at the end of
this file: ``_probe.compile_scene`` stages the fixture as the workspace
``src/scene.js`` that ``runtime_js/check_shaders.mjs`` boots (13 programs, 2
custom materials).  The structural assertions stay in front of it — they say
WHAT the GLSL does, which a compile cannot.

The last two tests are new here, and both cover a change the port made: the
shade colour is now MORE saturated than the lit one (desaturating it was what
pulled the whole mass toward grey-green felt on our pipeline), and the
transmission through a leaf goes out when the sun goes below the horizon
(``patchLeafSSS`` adds it to the ALBEDO, so nothing else turns it off, and the
night pass rendered every crown as glowing daylight foliage).
"""
from __future__ import annotations

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "grass.js", "foliage_shade.js", "canopy.js")


def test_a_whole_woodland_of_crowns_is_one_draw_call():
    """A crown is thousands of cards; per-leaf meshes would spend the scene's
    whole draw budget on foliage — the avenue that prompted this library
    shipped 158,917 separate meshes and timed its own census out."""
    out = measure("""
import { makeCanopy } from './lib/canopy.js';
const g = makeCanopy({ crowns: [
  { position: [0, 6, 0], radius: 3 },
  { position: [9, 7, 2], radius: 3.5 },
  { position: [-8, 5, 4], radius: 2.5 },
], leaves: 900, seed: 4 });
const meshes = [];
g.traverse((o) => { if (o.isMesh) meshes.push(o); });
const lv = g.getObjectByName('Leaves');
console.log(JSON.stringify({
  meshes: meshes.length,
  instanced: !!lv.geometry.isInstancedBufferGeometry,
  count: lv.geometry.instanceCount,
  attrs: Object.keys(lv.geometry.attributes).sort(),
  culled: lv.frustumCulled,
  box: [lv.geometry.boundingBox.min.toArray(),
        lv.geometry.boundingBox.max.toArray()],
  hasTick: typeof g.userData.tick === 'function',
}));
""", _LIBS)
    # Three crowns, one mesh — that is the whole point of the field.
    assert out["meshes"] == 1, out
    assert out["instanced"] and out["count"] == 2700, out
    assert out["attrs"] == ["aCorner", "iAxis", "iPos", "iVar", "normal",
                            "position", "uv"], out
    assert out["culled"] is False, out
    # position stays at zero, so a derived bound would be a point at the
    # origin: the stated box must span all three crowns.
    assert out["box"][0][0] < -9 and out["box"][1][0] > 11, out
    assert out["hasTick"], out


def test_neighbouring_leaves_are_not_the_same_green():
    """Broken colour is the mechanism: a mass of cards that all share one hue
    is the flat crown again with more triangles.  The spread is carried per
    leaf and scaled by `hue`, so hue 0 must collapse it."""
    out = measure("""
import { makeCanopy } from './lib/canopy.js';
const read = (h) => {
  const lv = makeCanopy({ leaves: 2000, seed: 9, hue: h })
      .getObjectByName('Leaves');
  const v = lv.geometry.attributes.iVar.array;
  let lo = 1e9, hi = -1e9;
  for (let i = 1; i < v.length; i += 4) {
    lo = Math.min(lo, v[i]); hi = Math.max(hi, v[i]);
  }
  return { lo, hi, swing: lv.material.userData.uniforms.uLeafHue.value };
};
console.log(JSON.stringify({ on: read(0.30), off: read(0) }));
""", _LIBS)
    on = out["on"]
    # Offsets run the full -1..1 so the swing uniform alone sets the spread:
    # neighbours land up to `hue` radians apart.
    assert on["lo"] < -0.9 and on["hi"] > 0.9, on
    assert abs(on["swing"] - 0.30) < 1e-6, on
    assert out["off"]["swing"] == 0, out


def test_a_hedge_is_a_leaf_mass_that_is_not_a_ball():
    """The first builder handed this library wrote its own Icosahedra for a
    hedgerow and said why in a comment: it wanted a "non-spherical" foliage
    volume, and a crown described by one radius cannot be one.  Per-axis
    extents, with the lighting normal following the ELLIPSOID rather than the
    sphere the direction was drawn on."""
    out = measure("""
import { makeCanopy } from './lib/canopy.js';
const lv = makeCanopy({ crowns: [
  { position: [0, 1.2, 0], size: [0.9, 1.2, 18] }],
  leaves: 3000, seed: 6 }).getObjectByName('Leaves');
const p = lv.geometry.attributes.iPos.array;
const a = lv.geometry.attributes.iAxis.array;
let ex = 0, ez = 0, sideward = 0, endward = 0;
for (let i = 0; i < p.length; i += 3) {
  ex = Math.max(ex, Math.abs(p[i]));
  ez = Math.max(ez, Math.abs(p[i + 2]));
  // Judge it well DOWN the length, where the two normals disagree: near the
  // middle any normal points sideways anyway.
  if (Math.abs(p[i + 2]) > 8 && Math.abs(p[i]) > 0.4) {
    if (Math.abs(a[i]) > Math.abs(a[i + 2])) sideward++;
    else endward++;
  }
}
console.log(JSON.stringify({ ex, ez, sideward, endward }));
""", _LIBS)
    # The mass runs down its long axis and stays thin across.
    assert out["ez"] > 12 and out["ex"] < 2, out
    # Facing follows the hedge's flank; on a sphere-derived normal these would
    # split evenly instead.
    assert out["sideward"] > 8 * out["endward"], out


def test_two_canopies_with_one_seed_are_identical():
    """A crown that reshuffles every reload cannot be reviewed, and the refine
    loop compares renders across rounds."""
    out = measure("""
import { makeCanopy } from './lib/canopy.js';
const of = () => Array.from(
    makeCanopy({ leaves: 400, seed: 21 })
        .getObjectByName('Leaves').geometry.attributes.iPos.array);
const a = of(), b = of();
console.log(JSON.stringify({ same: a.every((v, i) => v === b[i]),
                             n: a.length }));
""", _LIBS)
    assert out["same"] and out["n"] == 1200, out


def test_a_shade_leaf_is_darker_cooler_and_more_saturated():
    """The default under-colour is the crown's inside, and mixing toward it is
    most of what gives the mass a form.  The reference desaturated it
    (`s * 0.9`), which is what LIGHTENING a colour does — measured on our
    pipeline the crown came out at 0.34 mean foliage saturation and read as
    grey-green felt.  A shade leaf is the chlorophyll-dense one."""
    out = measure("""
import * as THREE from 'three';
import { makeCanopy } from './lib/canopy.js';
const u = makeCanopy({ leaves: 200 }).getObjectByName('Leaves')
    .material.userData.uniforms;
const hsl = (c) => { const o = {}; c.getHSL(o); return o; };
console.log(JSON.stringify({ lit: hsl(u.uLeafLit.value),
                             under: hsl(u.uLeafUnder.value) }));
""", _LIBS)
    lit, under = out["lit"], out["under"]
    assert under["l"] < lit["l"] * 0.45, out          # darker
    assert under["h"] > lit["h"] + 0.02, out          # cooler
    assert under["s"] > lit["s"], out                 # and MORE saturated


def test_transmission_through_a_leaf_goes_out_when_the_sun_does():
    """`patchLeafSSS` adds its glow to the albedo, before any light is
    applied, so a sun below the horizon does not turn it off by itself: on the
    night showcase pass every crown stayed a bright yellow-green over correctly
    dark ground.  Full strength is kept for any sun at or above the horizon —
    a low sun is exactly when a backlit crown glows most."""
    out = measure("""
import * as THREE from 'three';
import { makeCanopy } from './lib/canopy.js';
const amt = (el) => {
  const r = el * Math.PI / 180;
  const dir = new THREE.Vector3(Math.cos(r), Math.sin(r), 0.2).normalize();
  return makeCanopy({ leaves: 200, backlit: { sunDir: dir } })
      .getObjectByName('Leaves').material.userData.uniforms.uLeafAmt.value;
};
const off = makeCanopy({ leaves: 200, backlit: false })
    .getObjectByName('Leaves').material.userData.uniforms.uLeafAmt;
console.log(JSON.stringify({ high: amt(38), low: amt(1), night: amt(-20),
                             off: off === undefined }));
""", _LIBS)
    assert out["high"] > 0.4, out
    # Golden hour keeps the whole effect; it is the best moment for it.
    assert abs(out["low"] - out["high"]) < 1e-6, out
    assert out["night"] == 0, out
    # `backlit: false` never installs the patch at all.
    assert out["off"] is True, out


def test_every_leaf_is_built_in_the_vertex_shader_and_the_chain_is_whole():
    """The whole crown lives in a patched MeshStandardMaterial, so a typo in
    the leaf GLSL is a blank screen rather than a bad-looking tree.

    This drives the material's own `onBeforeCompile` and reads the GLSL that
    would go to the driver: the leaf body must reach the vertex stage, the
    grade must reach the fragment stage, `patchLeafSSS` must have CHAINED
    behind the leaf patch rather than replaced it (assigning
    `onBeforeCompile` outright is how a second patch silently eats the
    first), and `shadows: true` must install the displaced depth pass — a
    shadow pass over the undisplaced geometry draws only the zero-position
    quads.  The real GPU compile of this same material is
    `test_every_program_compiles_on_the_gpu` at the end of this file; the frames
    are at fx/out/canopy/ (zero shader_errors)."""
    out = measure("""
import * as THREE from 'three';
import { makeCanopy } from './lib/canopy.js';
const g = makeCanopy({
  crowns: [{ position: [0, 7, 0], radius: 4 },
           { position: [0, 0.9, -9], size: [0.9, 0.9, 14] }],
  leaves: 1200, wind: { strength: 1.4, speed: 0.8 },
  backlit: { sunDir: new THREE.Vector3(0.45, 0.62, 0.35) },
  shadows: true, seed: 2,
});
const mesh = g.getObjectByName('Leaves');
const glsl = (mat) => {
  const s = { uniforms: {}, vertexShader: '#include <begin_vertex>',
              fragmentShader: '#include <color_fragment>' };
  mat.onBeforeCompile(s, null);
  return s;
};
const s = glsl(mesh.material);
const d = mesh.customDepthMaterial ? glsl(mesh.customDepthMaterial) : null;
console.log(JSON.stringify({
  patches: mesh.material.userData.astraPatches.map((p) => p.name),
  key: mesh.material.customProgramCacheKey(),
  vertexBuildsLeaf: s.vertexShader.includes('leafLocalDir')
      && s.vertexShader.includes('transformed = lfP;'),
  fragmentGrades: s.fragmentShader.includes('astraHueShift')
      && s.fragmentShader.includes('diffuseColor.rgb = clamp(lfC'),
  sssAfterLeaf: s.fragmentShader.indexOf('uLeafTint')
      > s.fragmentShader.indexOf('uLeafUnder'),
  varyingsOnce: (s.fragmentShader.match(/varying vec2 vLeafB;/g) || []).length,
  boundUniforms: ['uLeafLit', 'uLeafUnder', 'uLeafHue', 'uLeafWind', 'uTime']
      .every((k) => s.uniforms[k] !== undefined),
  depthDisplaces: !!d && d.vertexShader.includes('transformed = lfP;'),
}));
""", _LIBS)
    # foliageBase is the shared varying `patchLeafSSS` installs before its
    # own patch; the leaf patch must still be FIRST, and the cache key must
    # name the whole chain in order (one key per distinct GLSL).
    assert out["patches"] == ["canopy:leaf", "foliageBase", "leaf"], out
    assert out["key"] == "astra:canopy:leaf+foliageBase+leaf", out
    assert out["vertexBuildsLeaf"] and out["fragmentGrades"], out
    assert out["sssAfterLeaf"], out
    assert out["varyingsOnce"] == 1, out
    assert out["boundUniforms"], out
    assert out["depthDisplaces"], out

# A FOGGED, shadow-casting scene: `USE_FOG` and the shadow branches only exist
# in a program built from one, and a patch that drops a chunk compiles fine in
# an unfogged fixture and renders as a sticker in a real scene.
_COMPILE_SCENE = """
import * as THREE from 'three';
import { tickShaders } from './lib/shader.js';
import { makeCanopy } from './lib/canopy.js';

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
  scene.add(makeCanopy({ crowns: [
    { position: [0, 4, 0], radius: 2.4, height: 3.2 },
    { position: [6, 2, -3], size: [7, 1.6, 1.4] },
  ], wind: 0.5 }));
  return {
    scene,
    cameras: [{ name: 'a', position: [12, 5, 14], lookAt: [0, 2, 0], fov: 45 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_every_program_compiles_on_the_gpu():
    """A crown is thousands of leaf cards in ONE instanced draw with a displaced
    vertex stage and its own depth material; the hedge form (`size`) builds the
    same program from a different mass.

    The reference had this case and the port dropped it: `_probe.shader_check`
    staged only `src/fixture.js` while `check_shaders.mjs` boots the workspace's
    `src/scene.js`, so it could never run.  `_probe.compile_scene` stages the
    scene, so the case is back (consolidation, 2026-09-01)."""
    code, out = compile_scene(_COMPILE_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
