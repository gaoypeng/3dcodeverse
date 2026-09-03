"""The dappled-light patches: gaps that slide along the sun.

Ported from the reference suite 2026-09-01, plus the four properties the
port added on our renderer (no post chain, exposure 1.0): a penumbra-wide
edge, the elongation along the sun's ground track, the crown-scale
clustering, and the shade that the flecks are flecks ON.
"""

from __future__ import annotations

from tests.scene_runtime.lib._probe import compile_scene, measure

_LIBS = ("shader.js", "dapple.js")


def test_the_pattern_slides_along_the_sun_not_straight_down():
    """A gap in a canopy projects its patch of light ALONG the sun, so
    as the canopy drifts the patch travels the way that gap's shadow
    would. Sampling the pattern at the surface's own xz instead — the
    obvious way to write it — makes the flecks slide down the world
    axes, which is the tell that turns dapple into a moving texture.
    The offset is height / sun.y, so a low sun throws the pattern far
    across and a noon sun barely offsets it at all."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const src = (o) => {
  const m = new THREE.MeshStandardMaterial();
  patchDappledLight(m, o);
  const s = { uniforms: {}, vertexShader: 'void main() {}',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return { fs: s.fragmentShader, u: s.uniforms };
};
const a = src({ seed: 1 });
console.log(JSON.stringify({
  // The sample point is offset by the sun's horizontal direction over
  // its vertical one — not by the fragment's own position alone.
  slidesAlongSun: /vAstraDapW\\.xz\\s*\\+\\s*dpS\\.xz\\s*\\*/.test(a.fs),
  usesHeightOverUp: /uDapH\\s*\\/\\s*dpUp/.test(a.fs),
  // Height and sun are uniforms, so two canopies share one program.
  heightIsUniform: a.u.uDapH !== undefined,
  sunIsUniform: a.u.uDapSun !== undefined,
}));
""", _LIBS)
    assert out["slidesAlongSun"], "flecks must travel along the sun"
    assert out["usesHeightOverUp"]
    assert out["heightIsUniform"] and out["sunIsUniform"]


def test_a_sunfleck_is_light_and_not_a_lighter_patch_of_ground():
    """The one hook the fragment stage gives a patch is ALBEDO, and
    lifting albedo caps a fleck at the light the surface already gets —
    which on a shaded forest floor is nearly nothing, exactly where a
    fleck is the only direct light there is. So it lifts the emissive
    term, tinted by the albedo underneath, the same choice windows.js
    and caustics.js document."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const m = new THREE.MeshStandardMaterial();
patchDappledLight(m, {});
const s = { uniforms: {}, vertexShader: 'void main() {}',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
const body = s.fragmentShader.slice(
    s.fragmentShader.indexOf('#include <color_fragment>'));
console.log(JSON.stringify({
  liftsLight: /totalEmissiveRadiance\\s*\\+=/.test(body),
  tintedByAlbedo: /totalEmissiveRadiance[^;]*diffuseColor\\.rgb/.test(body),
  neverWritesAlbedo: !/diffuseColor\\.rgb\\s*[*+]?=/.test(body),
}));
""", _LIBS)
    assert out["liftsLight"], "a fleck is light, not paint"
    assert out["tintedByAlbedo"]
    assert out["neverWritesAlbedo"], (
        "patchDappledLight must leave albedo to whoever owns it")


def test_only_what_faces_the_sun_catches_a_fleck():
    """A fleck lands on what the beam can reach. Without the facing
    term the underside of a rock lights up as brightly as its top, and
    the effect reads as glow rather than as light coming through."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const m = new THREE.MeshStandardMaterial();
patchDappledLight(m, {});
const s = { uniforms: {},
            vertexShader: 'void main() { #include <begin_vertex> }',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
console.log(JSON.stringify({
  facing: /dot\\(normalize\\(vAstraDapN\\), dpS\\)/.test(s.fragmentShader),
  // The normal is carried in WORLD space, matching the sun it meets.
  worldNormal: /mat3\\(modelMatrix\\)/.test(s.vertexShader),
  instanced: /USE_INSTANCING/.test(s.vertexShader),
}));
""", _LIBS)
    assert out["facing"] and out["worldNormal"] and out["instanced"]


def test_the_canopy_shade_and_the_flecks_work_at_different_scales():
    """Crowns are metres across and leaf gaps are centimetres. Given one
    scale the two patterns beat together and the floor reads as one
    lumpy texture instead of shade with light coming through it."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight, patchCanopyShade } from './lib/dapple.js';
const m = new THREE.MeshStandardMaterial();
patchCanopyShade(m, {});
patchDappledLight(m, {});
const s = { uniforms: {}, vertexShader: 'void main() {}',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
console.log(JSON.stringify({
  crown: s.uniforms.uCanScale.value,
  gap: s.uniforms.uDapScale.value,
  bothApplied: /uCanCool/.test(s.fragmentShader)
      && /uDapColor/.test(s.fragmentShader),
  oneMain: (s.fragmentShader.match(/void main/g) || []).length <= 1,
}));
""", _LIBS)
    assert out["crown"] >= out["gap"] * 5, (
        "crown and leaf-gap scales must sit an order apart")
    assert out["bothApplied"] and out["oneMain"]


def test_a_fleck_edge_is_the_suns_penumbra_and_never_finer_than_a_pixel():
    """What separates a sunfleck from a cloud is its EDGE. The sun is
    0.53 degrees across, so a canopy 7 m up throws a 6 cm penumbra —
    under a tenth of a 0.7 m fleck — and the edge has to be built from
    that height, not from a fixed fraction of the noise's swing (which
    is what blurred every fleck into fog before the port). The same
    edge widens to the pixel footprint, so a floor running away from
    the camera greys out instead of crawling."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const m = new THREE.MeshStandardMaterial();
patchDappledLight(m, { height: 7 });
const s = { uniforms: {}, vertexShader: 'void main() {}',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
const fs = s.fragmentShader;
console.log(JSON.stringify({
  penumbraFromHeight: /dpPen\\s*=\\s*0\\.0046\\d*\\s*\\*\\s*uDapH/.test(fs),
  antialiased: /fwidth\\(dpP\\.x\\)/.test(fs),
  widthFeedsTheThreshold: /astraDapGaps\\(dpP, uDapDens, dpW\\)/.test(fs),
}));
""", _LIBS)
    assert out["penumbraFromHeight"], "the edge must come from the height"
    assert out["antialiased"] and out["widthFeedsTheThreshold"]


def test_a_fleck_is_stretched_along_the_suns_ground_track():
    """A round gap seen from a 38 degree sun lands as an ellipse 1.6x
    long, and it is that elongation — not the brightness — that says
    where the sun is. The sample is compressed along the sun's ground
    track by 1/sin(elevation), clamped so a sun on the horizon does not
    smear the pattern into stripes."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const m = new THREE.MeshStandardMaterial();
patchDappledLight(m, {});
const s = { uniforms: {}, vertexShader: 'void main() {}',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
const fs = s.fragmentShader;
console.log(JSON.stringify({
  axisIsTheSunsGroundTrack: /vec2 dpAx = dpS\\.xz;/.test(fs),
  stretch: /dpEl\\s*=\\s*clamp\\(1\\.0 \\/ dpUp, 1\\.0, 3\\.0\\)/.test(fs),
  compressesAlongIt: /dot\\(dpP, dpAx\\)/.test(fs),
}));
""", _LIBS)
    assert out["axisIsTheSunsGroundTrack"]
    assert out["stretch"], "the stretch must be capped for a low sun"
    assert out["compressesAlongIt"]


def test_the_shade_tracks_density_and_the_fleck_is_paid_back_for_it():
    """`density` is coverage: the crown field is THRESHOLDED by it, not
    scaled by it. Scaling as well (what this did before the port) made
    the default half-canopy a 9 percent darkening — a flat card, and
    nothing for a fleck to be bright against. And because the shade is
    a stand-in for a shadow three will not cast, the albedo it removes
    must be given back to the fleck: light that came through a gap
    never met the canopy at all."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight, patchCanopyShade } from './lib/dapple.js';
const run = (shaded) => {
  const m = new THREE.MeshStandardMaterial();
  if (shaded) patchCanopyShade(m, { density: 0.6 });
  patchDappledLight(m, { strength: 0.55 });
  const s = { uniforms: {}, vertexShader: 'void main() {}',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return s;
};
const bare = run(false), shaded = run(true);
console.log(JSON.stringify({
  bareAmt: bare.uniforms.uDapAmt.value,
  shadedAmt: shaded.uniforms.uDapAmt.value,
  // The darkening is the depth times the coverage mask, with no
  // second density factor riding on the amplitude.
  thresholdedByDensity:
      /cnT = mix\\(0\\.72, 0\\.10, uCanDens\\)/.test(shaded.fragmentShader),
  depthIsAUniform: shaded.uniforms.uCanDepth.value > 0.5,
  ditheredAgainstBanding: /astraHash21\\(gl_FragCoord\\.xy\\)/
      .test(shaded.fragmentShader),
}));
""", _LIBS)
    assert out["bareAmt"] == 0.55, "an unshaded surface pays nothing back"
    assert 0.55 < out["shadedAmt"] <= 0.55 * 1.7, (
        "a shaded surface's flecks are lifted by what the shade took")
    assert out["thresholdedByDensity"] and out["depthIsAUniform"]
    assert out["ditheredAgainstBanding"]


_SCENE = """
import * as THREE from 'three';
import { patchDappledLight, patchCanopyShade } from './lib/dapple.js';
import { patchTriplanar } from './lib/terrain_shade.js';
import { patchMicroBreakup } from './lib/surface_wear.js';
import { tickShaders } from './lib/shader.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 10, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  const sun = new THREE.DirectionalLight(0xfff0d8, 3.0);
  sun.position.set(8, 6, 4);
  scene.add(sun);
  scene.add(new THREE.HemisphereLight(0x9fb6d6, 0x4a4436, 1.0));

  // The floor a forest agent really builds: triplanar dirt, micro
  // breakup, canopy shade, then the flecks — four bodies in one main.
  const mat = new THREE.MeshStandardMaterial({ color: 0x5f5a43,
    roughness: 0.95 });
  patchTriplanar(mat, { scale: 2.0 });
  patchMicroBreakup(mat, {});
  patchCanopyShade(mat, { density: 0.6 });
  patchDappledLight(mat, { height: 7, density: 0.55, seed: 3,
    sunDir: sun.position.clone().normalize() });
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(24, 24, 8, 8), mat);
  floor.rotateX(-Math.PI / 2);
  scene.add(floor);

  // The instancing branch of both vertex bodies, really compiled.
  const posts = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.2, 0.25, 3, 8), mat, 3);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 3; i++) {
    m4.makeTranslation(-3 + i * 3, 1.5, -2);
    posts.setMatrixAt(i, m4);
  }
  posts.castShadow = true;
  scene.add(posts);

  // A camera, because the host reports a scene with none as not booted
  // and never reaches the compile stage at all.
  return {
    scene,
    cameras: [{ name: 'a', position: [9, 4, 11], lookAt: [0, 1, 0],
                fov: 45 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_the_chain_compiles_on_the_real_renderer():
    """The only witness that counts.  Whether the GPU accepts four
    chained bodies in one main, an fwidth() inside a patch body, a
    three-field helper returning a vec2 and the instancing branch of two
    vertex bodies cannot be asserted from source, and a patch that does
    not compile is worth nothing."""
    code, out = compile_scene(
        _SCENE, ("shader.js", "dapple.js", "terrain_shade.js",
                 "surface_wear.js", "noise.js"))
    assert code == 0, out
    assert "every program compiled" in out
    # A patched built-in keeps the built-in's own depth and fog chunks,
    # so nothing here may be flagged as discardable.
    assert "DISCARDED" not in out


def test_one_seed_lays_the_same_canopy_twice():
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const key = (seed) => {
  const m = new THREE.MeshStandardMaterial();
  patchDappledLight(m, { seed });
  const s = { uniforms: {}, vertexShader: 'void main() {}',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return { seed: s.uniforms.uDapSeed.value, src: s.fragmentShader.length };
};
const a = key(3), b = key(3), c = key(9);
console.log(JSON.stringify({
  same: a.seed === b.seed && a.src === b.src,
  differs: a.seed !== c.seed,
  // A different seed must NOT recompile: it is a uniform, not source.
  sameSource: a.src === c.src,
}));
""", _LIBS)
    assert out["same"] and out["differs"] and out["sameSource"]


def test_the_fleck_takes_its_colour_from_where_the_sun_is():
    """A fleck is a picture of the sun, so a dusk scene cannot have noon
    flecks: with no `color` given, the default is read off `sunDir` —
    warm-white overhead, orange near the horizon. An explicit colour
    still wins, and the leaf-transmission rim keeps the fleck from
    being one flat tone."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const at = (y, o = {}) => {
  const m = new THREE.MeshStandardMaterial();
  const dir = new THREE.Vector3(0.6, y, 0.3).normalize();
  patchDappledLight(m, Object.assign({ sunDir: dir }, o));
  const s = { uniforms: {}, vertexShader: 'void main() {}',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return s;
};
const high = at(0.9).uniforms.uDapColor.value;
const low = at(0.10).uniforms.uDapColor.value;
const forced = at(0.9, { color: 0x336699 }).uniforms.uDapColor.value;
const fs = at(0.9).fragmentShader;
console.log(JSON.stringify({
  highBlue: high.b, lowBlue: low.b,
  highWarmth: high.r - high.b, lowWarmth: low.r - low.b,
  forced: [forced.r, forced.g, forced.b],
  rimIsLeafTransmission: /mix\\(uDapLeaf, uDapColor, dpCore\\)/.test(fs),
  hueVariesWithinTheEffect: /astraHueBreak\\(dpTint/.test(fs),
}));
""", _LIBS)
    assert out["lowWarmth"] > out["highWarmth"] > 0, (
        "a low sun throws warmer flecks than a high one")
    assert out["lowBlue"] < out["highBlue"]
    # Colours live in three's LINEAR working space, so 0x336699 is not
    # (0.2, 0.4, 0.6) here — what matters is that it is the caller's
    # blue and not the warm default.
    assert out["forced"][2] > out["forced"][0], "an explicit colour wins"
    assert out["rimIsLeafTransmission"] and out["hueVariesWithinTheEffect"]


def test_gaps_crowd_where_the_crown_is_thin():
    """An even scatter of flecks reads as camouflage. A canopy is not
    evenly thin, so the gap threshold is modulated at crown scale — an
    order coarser than the leaf gaps and coarser again than the branch
    field — and the flecks cluster into the thin patches the way they
    do under a real tree."""
    out = measure("""
import * as THREE from 'three';
import { patchDappledLight } from './lib/dapple.js';
const m = new THREE.MeshStandardMaterial();
patchDappledLight(m, {});
const s = { uniforms: {}, vertexShader: 'void main() {}',
            fragmentShader: '#include <color_fragment>' };
m.onBeforeCompile(s);
const fs = s.fragmentShader;
const freq = (re) => Number((fs.match(re) || [0, 0])[1]);
console.log(JSON.stringify({
  leaf: 1.0,
  branch: freq(/astraFbm2\\(p \\* (0\\.\\d+) \\+ 11\\.3, 2\\)/),
  crown: freq(/astraFbm2\\(p \\* (0\\.\\d+) \\+ 5\\.1, 2\\)/),
  modulatesTheThreshold: /t = mix\\([^)]*\\) \\+ \\(c - 0\\.375\\)/.test(fs),
  // Core and rim come out of one call: a second gap evaluation per
  // pixel to find the middle of a fleck is not worth its samples.
  oneCall: (fs.match(/astraDapGaps\\(dpP/g) || []).length === 1,
}));
""", _LIBS)
    assert out["crown"] < out["branch"] < out["leaf"], (
        "leaf gaps, branch gaps and crowns must sit at three scales")
    assert out["modulatesTheThreshold"] and out["oneCall"]
