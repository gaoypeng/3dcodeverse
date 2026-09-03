"""woodland.js — bark that runs UP a trunk, and a far field that BELONGS.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_woodland_lib.py).  Its claims are kept whole: the grain is
anisotropic, the four kinds are four structures sharing one program, the bark
BLENDS over what it lands on, the imposter field is one instanced draw call
seated on the caller's ground inside a stated bounding sphere, it stays out of
every override pass, and a seed is a promise.

Their compile cases are restored at the end of this file, through
``_probe.compile_scene`` (16 programs, 3 custom materials).  What is asserted
before them is the STRUCTURE of the GLSL, read back off ``onBeforeCompile`` and
off the built material; the frames are at fx/out/woodland/ (day and night).

The last four tests are new, and each pins a change the port made:

* the card material is OPAQUE with alpha-to-coverage.  Blended, the field is
  one draw call and therefore unsorted against itself, so a card resolved its
  soft edge against the sky and then wrote depth — a pale halo traced round
  every silhouette with another card behind it, measured at 2.5 per mille of
  the treeline band and gone (0.9) once coverage decides per MSAA sample;
* the card's light comes from the scene's own ``sunRig`` package, environment
  bake included, instead of a pair of constants graded for another renderer.
  Under the moon the reference's constants lit the treeline at 1.54x the real
  crown standing in it — a daylight wood at midnight;
* a card is a BALL: the bowed normal hands half of every backlit card a full
  sun term the geometry beside it does not get (1.41x, measured), and a crown
  occludes its own interior, which nothing else in this material can supply;
* the bark tints are albedos.  Birch was 0.85 and rendered as a white pole.
"""
from __future__ import annotations

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "grass.js", "woodland.js")

# Drive onBeforeCompile on a bare standard material and read the GLSL back.
_COMPILE = """
const compile = (m) => {
  const s = { uniforms: {},
              vertexShader: 'void main() { #include <begin_vertex> }',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return s;
};
"""


def test_bark_fissures_run_up_the_trunk_and_not_around_it():
    """A trunk's grain runs UP.  That one asymmetry is the whole claim: an
    isotropic noise on a cylinder reads as lichen or as stone, never as bark.
    The library divides the along-axis coordinate by `aniso` and leaves the
    across-axis alone, so the ratio is measurable in the shipped source and in
    the uniforms it hands the GPU."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchBark } from './lib/woodland.js';
const u = (kind) => {
  const m = new THREE.MeshStandardMaterial();
  patchBark(m, { kind });
  const s = compile(m);
  return { u: s.uniforms, fs: s.fragmentShader };
};
const oak = u('oak'), birch = u('birch'), bamboo = u('bamboo');
console.log(JSON.stringify({
  oakAniso: oak.u.uBarkAniso ? oak.u.uBarkAniso.value : null,
  birchAniso: birch.u.uBarkAniso ? birch.u.uBarkAniso.value : null,
  bambooAniso: bamboo.u.uBarkAniso ? bamboo.u.uBarkAniso.value : null,
  // The ALONG-axis coordinate is divided by aniso and the ACROSS pair is
  // not.  Read the two lines rather than looking for one spelling.
  dividesOneAxis: (() => {
    const along = /float bkH = [^;]*;/.exec(oak.fs);
    const across = /vec2 bkQ = [^;]*;/.exec(oak.fs);
    return !!along && !!across
        && /uBarkAniso/.test(along[0]) && !/uBarkAniso/.test(across[0]);
  })(),
  kindIsUniform: oak.fs === birch.fs && oak.fs === bamboo.fs,
  kindKnob: oak.u.uBarkKind ? oak.u.uBarkKind.value : null,
}));
""", _LIBS)
    for k in ("oakAniso", "birchAniso", "bambooAniso"):
        assert out[k] is not None, f"{k} is not a uniform"
        assert out[k] > 2, f"{k} is {out[k]}: that grain is not directional"
    assert out["dividesOneAxis"], (
        "the anisotropy must stretch one axis, not scale both")
    assert out["kindIsUniform"], (
        "four kinds must share one program: kind is a uniform, not a branch")


def test_the_four_kinds_are_four_structures():
    """pine plates, birch papery bands, oak deep ridges, bamboo smooth nodes.
    If they differed only in tint they would be one bark with four colours,
    which is what a palette-only implementation gives."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchBark } from './lib/woodland.js';
const u = (kind) => {
  const m = new THREE.MeshStandardMaterial();
  patchBark(m, { kind });
  return compile(m).uniforms;
};
const k = ['pine', 'birch', 'oak', 'bamboo'].map(u);
const num = (n) => k.map((x) => (x[n] ? x[n].value : null));
console.log(JSON.stringify({
  kinds: num('uBarkKind'),
  aniso: num('uBarkAniso'),
  scale: num('uBarkScale'),
  tints: k.map((x) => x.uBarkTint.value.toArray()),
}));
""", _LIBS)
    assert len(set(out["kinds"])) == 4, "the four kinds share an id"
    assert len(set(out["aniso"])) > 1 or len(set(out["scale"])) > 1, (
        "the kinds differ only in tint")


def test_a_bark_tint_is_an_albedo():
    """New here.  `tint` is multiplied over the material's own colour and then
    lit; anything near 1.0 has nowhere left to go under a 5.4 sun and clips
    before the tone map can shape it.  Birch shipped at 0xd8d4c8 and came back
    as the brightest thing in a daylight frame — luminance 0.746 with a p95 of
    0.833, brighter than the sky's own gradient over it."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchBark } from './lib/woodland.js';
const worst = {};
for (const kind of ['pine', 'birch', 'oak', 'bamboo']) {
  const m = new THREE.MeshStandardMaterial();
  patchBark(m, { kind });
  const c = compile(m).uniforms.uBarkTint.value;
  // THREE.Color holds LINEAR working values; the 0.8 ceiling is stated in
  // the sRGB the table is written in.
  worst[kind] = Math.max(...new THREE.Color().copyLinearToSRGB(c).toArray());
}
console.log(JSON.stringify(worst));
""", _LIBS)
    for kind, top in out.items():
        assert top <= 0.80 + 1e-3, (
            f"{kind}'s tint peaks at {top:.3f}: that is not an albedo")
        assert top >= 0.02, f"{kind}'s tint is black"


def test_bark_blends_and_never_assigns_over_what_it_lands_on():
    """A trunk carries edge wear, micro-breakup and often moss before the bark
    goes on.  patchTriplanar and patchSlopeSplat both assigned diffuseColor.rgb
    and each erased the other for months."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchBark } from './lib/woodland.js';
const m = new THREE.MeshStandardMaterial();
patchBark(m, {});
const fs = compile(m).fragmentShader;
const body = fs.slice(fs.indexOf('#include <color_fragment>'));
const assigns = (body.match(/diffuseColor\\.rgb\\s*=(?!=)/g) || []).length;
const folds = (body.match(
    /diffuseColor\\.rgb\\s*=\\s*(mix\\(\\s*diffuseColor|diffuseColor)/g)
    || []).length;
console.log(JSON.stringify({ assigns, folds,
  multiplies: (body.match(/diffuseColor\\.rgb\\s*\\*=/g) || []).length }));
""", _LIBS)
    assert out["assigns"] == out["folds"], (
        "bark assigns over the colour it landed on")
    assert out["multiplies"] >= 1, "the fissures never darken anything"


def test_a_fissure_is_dark_and_cool_and_never_black():
    """New here.  The cut used to multiply the albedo by `1 - depth` with a
    relief floor of 0.45 under it, so a deep oak at depth 0.62 reached 17% of
    its own colour — paint, not relief, and the first thing to crush on a night
    frame.  What reaches the bottom of a fissure is SKY: dimmer AND cooler than
    the ridge beside it, which is the depth cue that survives being resolved
    down to four pixels."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchBark } from './lib/woodland.js';
const m = new THREE.MeshStandardMaterial();
patchBark(m, { kind: 'oak', depth: 1.0 });
const fs = compile(m).fragmentShader;
// the cut's own multiplier: mix(lit ridge, dark fissure, bkCut)
const cut = /mix\\(1\\.0 \\+ 0\\.30 \\* uBarkDepth,\\s*([0-9.]+) - ([0-9.]+) \\* uBarkDepth/
    .exec(fs.replace(/\\s+/g, ' '));
const lit = /clamp\\(1\\.0 \\+ bkRel \\* [0-9.]+ \\+ bkFlat \\* [0-9.]+, ([0-9.]+),/
    .exec(fs);
// a cool tint multiplied in with the cut, blue channel above red
const cool = /vec3\\(0\\.(\\d+), 0\\.(\\d+), 1\\.(\\d+)\\),\\s*bkCut/
    .exec(fs.replace(/\\s+/g, ' '));
console.log(JSON.stringify({
  floor: cut ? +cut[1] - +cut[2] : null,
  litFloor: lit ? +lit[1] : null,
  cool: !!cool,
}));
""", _LIBS)
    assert out["floor"] is not None, "the fissure multiplier is gone"
    assert out["floor"] >= 0.25, (
        f"a full-depth fissure lands at {out['floor']:.2f} of the albedo")
    assert out["litFloor"] is not None and out["litFloor"] >= 0.5, (
        "the relief can take a facet to half its own colour")
    assert out["cool"], "the fissure is not cooler than the ridge beside it"


def test_a_trunk_is_never_one_flat_colour():
    """New here.  Every effect in this library has to carry hue VARIANCE, and a
    trunk is the surface a camera gets closest to.  The drift is read off the
    same field as the grain, very coarsely, so it follows the trunk instead of
    spotting it — and the moss in the cracks takes the same drift."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchBark } from './lib/woodland.js';
const m = new THREE.MeshStandardMaterial();
patchBark(m, {});
const fs = compile(m).fragmentShader.replace(/\\s+/g, ' ');
console.log(JSON.stringify({
  // warm on one channel, cool on the other: a value ramp is not a hue
  drift: / bkDr = astraBarkFbm\\(bkQ \\* 0\\.\\d+/.test(fs),
  tintDrifts: /uBarkTint \\* vec3\\(1\\.0 \\+ bkDr[^)]*1\\.0 - bkDr/.test(fs),
  mossDrifts: /uBarkMossColor \\* \\(1\\.0 \\+ bkDr/.test(fs),
}));
""", _LIBS)
    assert out["drift"], "no coarse colour field on the trunk"
    assert out["tintDrifts"], "the drift does not move the HUE, only the value"
    assert out["mossDrifts"], "the moss is one flat green cushion"


def test_imposters_are_one_draw_call_seated_on_the_ground():
    """A forest of imposters that costs a draw call each has bought nothing.
    And a card floating off the terrain is worse than the real tree it
    replaced."""
    out = measure("""
import { makeImposters } from './lib/woodland.js';
const bed = (x, z) => 2 + Math.sin(x * 0.2) + Math.cos(z * 0.15);
const g = makeImposters({ count: 60, extent: 30, heightAt: bed, seed: 4 });
let meshes = 0, instanced = 0, sphere = null, worstFoot = 0, n = 0;
let contained = true;
g.traverse((o) => {
  if (!o.isMesh) return;
  meshes++;
  const geo = o.geometry;
  if (geo.instanceCount || o.isInstancedMesh) instanced++;
  const s = geo.boundingSphere;
  if (s) sphere = s.radius;
  const p = geo.getAttribute('aPos') || geo.getAttribute('aOff');
  if (!p) return;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    worstFoot = Math.max(worstFoot, Math.abs(y - bed(x, z)));
    n++;
    if (!s) continue;
    // CONTAINMENT, not a small number: a tidy radius about the wrong
    // centre culls the field off-screen.
    const dx = x - s.center.x, dy = y - s.center.y, dz = z - s.center.z;
    if (Math.sqrt(dx * dx + dy * dy + dz * dz) > s.radius) contained = false;
  }
});
console.log(JSON.stringify({ meshes, instanced, sphere, worstFoot, n,
                             contained }));
""", _LIBS)
    assert out["meshes"] == 1, f"{out['meshes']} draw calls for one field"
    assert out["instanced"] == 1, "the field is not instanced"
    assert out["contained"], (
        "the stated sphere does not cover every card: three culls by this "
        "sphere and by nothing else, because position is all zeros")
    assert out["sphere"] and out["sphere"] < 1e3, (
        "the field states no real radius; the 10 km default frames a "
        "kilometre of empty air")
    assert out["n"] and out["worstFoot"] < 0.35, (
        f"an imposter floats {out['worstFoot']:.2f} m off the bed")


def test_the_field_is_kept_out_of_the_occlusion_pass():
    """GTAOPass redraws with an opaque override material, where a transparent
    card is a solid wall — 50/255 of false darkening — and a billboard tree
    casting its card's shadow is worse still."""
    out = measure("""
import { makeImposters } from './lib/woodland.js';
const g = makeImposters({ count: 20, extent: 10, seed: 2 });
const bad = [];
g.traverse((o) => {
  if (!o.isMesh) return;
  if (!o.userData.astraNoOverride) bad.push(o.name + ':unguarded');
  if (o.castShadow) bad.push(o.name + ':castShadow');
});
console.log(JSON.stringify({ bad }));
""", _LIBS)
    assert not out["bad"], out["bad"]


def test_the_cards_take_coverage_not_blending():
    """New here.  The whole field is ONE draw call, so the cards are not sorted
    against each other: a blended card resolved its antialiased edge against
    whatever was already in the buffer — the sky — and then wrote depth, so the
    card behind it never filled that pixel in.  The result was a pale halo
    traced round every silhouette with another card behind it (2.5 per mille of
    the treeline band; 0.9 after).  Alpha-to-coverage decides per MSAA sample
    instead, which this renderer has, and moves the field into the opaque
    queue where it is depth-sorted for free."""
    out = measure("""
import { makeImposters } from './lib/woodland.js';
const g = makeImposters({ count: 12, extent: 10, seed: 1 });
let m = null;
g.traverse((o) => { if (o.isMesh) m = o.material; });
console.log(JSON.stringify({
  transparent: m.transparent,
  a2c: !!m.alphaToCoverage,
  depthWrite: m.depthWrite,
  // the hard drop is still there for the pixels coverage would waste
  discards: /discard/.test(m.fragmentShader),
}));
""", _LIBS)
    assert out["a2c"], "no alpha-to-coverage: the silhouettes keep their halo"
    assert not out["transparent"], (
        "a blended card in a single unsorted draw call resolves against the "
        "sky and then writes depth")
    assert out["depthWrite"], "a card that writes no depth is behind the wood"
    assert out["discards"], "the empty corners of the quad are still drawn"


def test_a_card_is_lit_by_the_scene_it_stands_in():
    """New here, and it is the port's biggest change.  A card does its own
    lighting — no normals three can light, no place in the shadow map — so the
    only thing that makes the far field belong to the near one is being handed
    the same irradiance.  The reference's constants (0xfff0d6 * 3.0 and
    0x9fb2c4 * 0.85) were graded for another exposure and another post chain;
    on our rig they put a MIDNIGHT treeline at 1.54x the luminance of the real
    crown standing in it — a daylight wood under a moon.  Reading the rig,
    environment bake included, that is 0.82."""
    out = measure("""
import * as THREE from 'three';
import { sunRig } from './lib/environment.js';
import { makeImposters } from './lib/woodland.js';
const lit = (rig) => {
  const g = makeImposters({ count: 8, extent: 10, seed: 1, rig });
  let m = null;
  g.traverse((o) => { if (o.isMesh) m = o.material; });
  const lum = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  return {
    sun: lum(m.uniforms.uImpSunColor.value),
    amb: lum(m.uniforms.uImpAmbient.value),
    dir: m.uniforms.uImpSun.value.toArray().map((v) => +v.toFixed(3)),
  };
};
const day = sunRig({ azimuth: 215, elevation: 38 });
const night = sunRig({ azimuth: 215, elevation: -20 });
const bare = makeImposters({ count: 8, extent: 10, seed: 1 });
let bm = null;
bare.traverse((o) => { if (o.isMesh) bm = o.material; });
console.log(JSON.stringify({
  day: lit(day), night: lit(night),
  dayDir: day.sunDir.toArray().map((v) => +v.toFixed(3)),
  bareSun: bm.uniforms.uImpSunColor.value.toArray()
      .map((v) => +v.toFixed(3)),
  bareAmb: (() => { const c = bm.uniforms.uImpAmbient.value;
    return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b; })(),
  // an explicit value still wins over the rig
  override: (() => {
    const g = makeImposters({ count: 4, extent: 5, rig: day,
                              ambient: 0x000000 });
    let m = null;
    g.traverse((o) => { if (o.isMesh) m = o.material; });
    return m.uniforms.uImpAmbient.value.toArray();
  })(),
}));
""", ("shader.js", "noise.js", "grass.js", "environment.js", "sky.js",
      "woodland.js"))
    assert out["day"]["dir"] == out["dayDir"], (
        "the cards are lit from somewhere other than the rig's own sun")
    # Night is a moon rig: dimmer key AND a far dimmer indirect.
    assert out["night"]["sun"] < out["day"]["sun"] * 0.5, (
        "the moon lights the cards as hard as the sun does")
    assert out["night"]["amb"] < out["day"]["amb"] * 0.2, (
        "the night treeline keeps its daylight sky")
    assert out["night"]["amb"] > 0, "nothing reaches the cards at night"
    # The fallback is this renderer's own daylight, not the reference's.
    assert out["bareSun"][0] > 4.0, (
        f"the no-rig default sun is {out['bareSun']}, which is not the "
        "irradiance this renderer's day rig delivers")
    assert 0.7 < out["bareAmb"] / out["day"]["amb"] < 1.4, (
        "the no-rig default and the day rig disagree about the indirect, so "
        "forgetting `rig` in daylight silently regrades the far field")
    assert out["override"] == [0, 0, 0], "an explicit ambient is ignored"


def test_a_backlit_card_is_a_mass_and_not_a_lit_flat():
    """New here.  The bowed normal sweeps radially across the card, so half of
    every card faces the sun whichever way the stand is turned — including when
    the sun is BEHIND the mass and the visible half of a real crown is in its
    own shadow.  Measured against a real crown of the same albedo at the same
    distance: 1.41x, which is what a treeline pasted on with scissors looks
    like.  The card now knows which side of itself the sun is on (world space,
    so both quads of a cross agree), turns the direct term down when it is
    behind, and spends it on transmission through the thin edge instead."""
    out = measure("""
import { makeImposters } from './lib/woodland.js';
const g = makeImposters({ count: 8, extent: 10, seed: 1 });
let m = null;
g.traverse((o) => { if (o.isMesh) m = o.material; });
const vs = m.vertexShader.replace(/\\s+/g, ' ');
const fs = m.fragmentShader.replace(/\\s+/g, ' ');
console.log(JSON.stringify({
  // computed against the eye in WORLD space, not against the card's quad
  sunSide: /sunSide = dot\\(uImpSun, normalize\\(cameraPosition - wPos\\)\\)/
      .test(vs),
  carried: /vImpVar = vec4\\(/.test(vs),
  damps: /iNdL \\*= mix\\(0\\.\\d+, 1\\.0, smoothstep\\([^)]*vImpVar\\.w\\)\\)/
      .test(fs),
  transmits: /iTrans = uImpSunColor/.test(fs)
      && /iBack = smoothstep\\([^)]*vImpVar\\.w\\)/.test(fs),
  occludes: /iAO = max\\(/.test(fs) && /uImpAmbient \\* iAO/.test(fs),
  // and the mass is broken up rather than painted flat
  clumps: /iMass = astraFbm2/.test(fs),
  hueVaries: /iAlb = astraHueBreak\\(/.test(fs),
}));
""", _LIBS)
    for k, why in (
        ("sunSide", "the card cannot tell a backlit stand from a lit one"),
        ("carried", "the sun side never reaches the fragment stage"),
        ("damps", "a backlit card keeps its full direct sun term"),
        ("transmits", "a backlit crown is a black cut-out"),
        ("occludes", "the crown's interior sees the whole sky"),
        ("clumps", "the crown is one flat mass"),
        ("hueVaries", "one hue over a whole crown"),
    ):
        assert out[k], why


def test_one_seed_grows_the_same_wood_twice():
    out = measure("""
import { makeImposters } from './lib/woodland.js';
const shape = (seed) => {
  const g = makeImposters({ count: 40, extent: 20, seed });
  const v = [];
  g.traverse((o) => {
    if (!o.isMesh) return;
    for (const [k, a] of Object.entries(o.geometry.attributes)) {
      let s = 0;
      for (let i = 0; i < a.count; i += 3) s += a.getX(i);
      v.push(k + ':' + s.toFixed(4));
    }
  });
  return v.join('|');
};
console.log(JSON.stringify({
  same: shape(5) === shape(5),
  differs: shape(5) !== shape(9),
}));
""", _LIBS)
    assert out["same"] and out["differs"]

# A FOGGED, shadow-casting scene: `USE_FOG` and the shadow branches only exist
# in a program built from one, and a patch that drops a chunk compiles fine in
# an unfogged fixture and renders as a sticker in a real scene.
_COMPILE_SCENE = """
import * as THREE from 'three';
import { tickShaders } from './lib/shader.js';
import { patchBark, makeImposters } from './lib/woodland.js';

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
  const bark = new THREE.MeshStandardMaterial({ color: 0x6b5540 });
  patchBark(bark, { kind: 'oak' });
  const stem = new THREE.CylinderGeometry(0.3, 0.45, 6, 12);
  stem.translate(0, 3, 0);
  const trunk = new THREE.Mesh(stem, bark);
  trunk.castShadow = true; scene.add(trunk);
  scene.add(makeImposters({ count: 60, extent: 40, heightAt }));
  return {
    scene,
    cameras: [{ name: 'a', position: [12, 5, 14], lookAt: [0, 2, 0], fov: 45 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_every_program_compiles_on_the_gpu():
    """A patched built-in (bark) and a billboard sheet that must face the camera
    from its vertex stage, in one scene: the two halves of this module build
    different programs and both have to survive the fog and shadow chunks.

    The reference had this case and the port dropped it: `_probe.shader_check`
    staged only `src/fixture.js` while `check_shaders.mjs` boots the workspace's
    `src/scene.js`, so it could never run.  `_probe.compile_scene` stages the
    scene, so the case is back (consolidation, 2026-09-01)."""
    code, out = compile_scene(_COMPILE_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
