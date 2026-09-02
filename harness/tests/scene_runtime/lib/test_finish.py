"""finish.js: light through a thin solid, and the colour a film splits.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_finish_lib.py).  Their renderer-contract assertions are dropped;
the physics claims, the shared-name contract and the option-is-a-uniform law
are kept.  Two claims the port ADDED are pinned here so a later edit cannot
quietly undo them, both measured on our host (exposure 1.0, ACES in the
fragment tail, no post chain):

* translucency absorbs PER CHANNEL and caps its peak with one scalar knee.
  On the original, a lamp inside a paper shade drove the transmitted term
  past 6 and 48% of the shade's pixels came back pure white (255 on every
  channel), which is the colour that IS the effect thrown away.  After: no
  white pixels at all and the shade's saturation up 75%.
* the film SPLITS the light the scene actually has (ambient + the
  hemisphere fill + a glint share of each sun and lamp) around its own
  mean, instead of adding a fixed white veil.  A veil cannot be seen on a
  puddle already reflecting a bright sky, and it glows in the dark.

The GPU compile is the only witness that counts for either: three's light
uniforms, `getDistanceAttenuation`, the `NUM_*_LIGHTS` branches and three
chained bodies in one main cannot be checked from source.
"""
from __future__ import annotations

import math
import re

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "finish.js")

# One compile of a patched material, as the renderer does it: the patch
# only writes GLSL inside onBeforeCompile, so nothing is readable until a
# shader object has been through it.
_COMPILE = """
const compile = (m) => {
  const s = { uniforms: {},
              vertexShader: 'void main() { #include <begin_vertex> }',
              fragmentShader: '#include <color_fragment>' };
  m.onBeforeCompile(s);
  return s;
};
const bodyOf = (s) => s.fragmentShader.slice(
    s.fragmentShader.indexOf('#include <color_fragment>'));
const flat = (s) => s.replace(/\\s+/g, ' ');
const constOf = (src, name) => parseFloat(
    (src.match(new RegExp(name + '\\\\s*=\\\\s*([0-9.]+)')) || [0, 'NaN'])[1]);
"""


# A lantern that is also worn and dusty, which is how a scene actually
# uses these: both finishes plus both wear passes on ONE material.  The
# InstancedMesh is not decoration — `vertexBody` runs before
# <project_vertex>, so the USE_INSTANCING branch is a second program —
# and the lamp, the hemisphere fill and the sun make the point, hemi and
# directional branches of BOTH patches compile.
_SCENE = """
import * as THREE from 'three';
import { patchTranslucency, patchIridescence } from './lib/finish.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { tickShaders } from './lib/shader.js';

export const BOUNDS = { min: [-8, 0, -8], max: [8, 6, 8] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  const sun = new THREE.DirectionalLight(0xfff0d8, 5.4);
  sun.position.set(6, 7, 4);
  sun.castShadow = true;
  scene.add(sun);
  scene.add(new THREE.HemisphereLight(0x9db8e8, 0x8a7f6a, 1.4));

  const mat = new THREE.MeshStandardMaterial({ color: 0xd8cdb4,
    roughness: 0.6, side: THREE.DoubleSide });
  patchMicroBreakup(mat, { scale: 0.4, seed: 2 });
  patchEdgeWear(mat, { strength: 0.4, width: 0.2, seed: 5 });
  patchTranslucency(mat, { thickness: 0.01, strength: 1.1, seed: 3 });
  patchIridescence(mat, { strength: 0.35, scale: 0.25, ior: 1.45,
                          seed: 7 });
  const shell = new THREE.Mesh(new THREE.SphereGeometry(0.3, 32, 24), mat);
  shell.position.y = 0.3;
  scene.add(shell);

  const lamp = new THREE.PointLight(0xffb862, 2.4, 6, 2);
  lamp.position.set(0, 0.3, 0);
  scene.add(lamp);

  const posts = new THREE.InstancedMesh(
      new THREE.CylinderGeometry(0.04, 0.05, 0.6, 10), mat, 4);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 4; i++) {
    m4.makeTranslation(0.5 + i * 0.2, 0.3, 0.1 * i);
    posts.setMatrixAt(i, m4);
  }
  posts.instanceMatrix.needsUpdate = true;
  scene.add(posts);

  // A film on a flat, lamp-lit slab: the reflect()/glint branch again,
  // on a surface whose normal never turns.
  const slick = new THREE.MeshStandardMaterial({ color: 0x2a2622,
    roughness: 0.2 });
  patchIridescence(slick, { strength: 0.4, scale: 0.3, seed: 11 });
  const pool = new THREE.Mesh(new THREE.CircleGeometry(1.2, 48), slick);
  pool.rotation.x = -Math.PI / 2;
  pool.position.set(0, 0.01, 1.6);
  pool.receiveShadow = true;
  scene.add(pool);

  return {
    scene,
    cameras: [{ name: 'a', position: [3, 1.6, 4], lookAt: [0, 0.4, 0],
                fov: 45 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def test_the_four_patch_chain_compiles_on_the_real_renderer():
    """Both finishes plus the two wear passes land on ONE material, which
    is how a scene actually uses them — a lantern is also dusty and worn.
    This is the only witness for the light loops: the hemisphere, sun and
    lamp branches of the film's own irradiance estimate exist purely as
    `#if NUM_*_LIGHTS` text until a GL context accepts them."""
    code, out = compile_scene(
        _SCENE, ("shader.js", "finish.js", "surface_wear.js"))
    assert code == 0, out
    assert "every program compiled" in out
    # A patched built-in keeps the built-in's own depth and fog chunks.
    assert "DISCARDED" not in out


def test_a_thick_sample_transmits_less_than_a_thin_one():
    """The whole difference between translucency and a pale repaint is
    that the glow is spent by the material it crossed.  The path is the
    chord through a convex body — the widest crossing times how squarely
    the surface is faced — and what survives is Beer-Lambert over it, so
    doubling the thickness must not merely dim the glow, it must
    extinguish it.  (The port split the reference's one `trT = exp(...)`
    line into the PATH and its transmittance, because the path is now
    read twice: once for the level and once for the colour.)"""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchTranslucency } from './lib/finish.js';
const one = (thickness) => {
  const m = new THREE.MeshStandardMaterial();
  patchTranslucency(m, { thickness });
  const s = compile(m);
  return { thick: s.uniforms.uTrsThick.value, fs: s.fragmentShader };
};
const a = one(0.0005), b = one(0.02), c = one(0.30);
const f = flat(a.fs);
console.log(JSON.stringify({
  thin: a.thick, mid: b.thick, thick: c.thick,
  path: (f.match(/float trPath = [^;]*;/) || [''])[0],
  expr: (f.match(/float trT = [^;]*;/) || [''])[0],
  mfp: constOf(a.fs, 'ASTRA_TRS_MFP'),
}));
""", _LIBS)
    # The path, not just the thickness: without the facing term every
    # pixel of a solid would transmit the same and the object would be a
    # lamp rather than a body with a thin edge and a thick middle.
    assert re.fullmatch(
        r"float trPath = uTrsThick \* astraFacing\(trN, trV\) \* trG;",
        out["path"]), out["path"]
    assert re.fullmatch(r"float trT = exp\(-trPath / ASTRA_TRS_MFP\);",
                        out["expr"]), out["expr"]
    mfp = out["mfp"]
    assert 0.005 < mfp < 0.2, mfp

    def survives(thickness, facing):
        return math.exp(-thickness * facing / mfp)

    # Squarely faced, where the chord is longest.
    thin, mid, thick = (survives(out[k], 0.9)
                        for k in ("thin", "mid", "thick"))
    assert thin > 0.95, thin
    assert mid < thin / 1.5, (mid, thin)
    assert thick < 1e-3, thick
    # And ONE body glows where it is THIN: the chord runs out toward the
    # silhouette, so the block that is opaque through its core still
    # comes back at its edge.  That gradient is the cue itself.
    assert survives(out["thick"], 0.05) > 100 * survives(out["thick"], 0.9)


def test_the_light_has_to_be_behind_the_surface_to_come_through():
    """Backlit is the other half of the cue, and it is a GATE, not a
    weighting: a candle with the sun on its face must gain nothing at
    all, or the patch is an ambient glow with a directional flavour."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchTranslucency } from './lib/finish.js';
const m = new THREE.MeshStandardMaterial();
patchTranslucency(m, {});
const s = compile(m);
const fn = (flat(s.fragmentShader).match(
    /float astraTrsWeight\\([^)]*\\) \\{[^}]*\\}/) || [''])[0];
console.log(JSON.stringify({
  fn,
  gate: /float b = max\\(-dot\\(n, l\\), 0\\.0\\);/.test(fn),
  ret: (fn.match(/return ([^;]*);/) || ['', ''])[1],
  lobe: /vec3 h = normalize\\(-l \\+ n \\* ASTRA_TRS_BEND\\);/.test(fn),
  facesViewer: /trN \\*= sign\\(dot\\(trN, trV\\)/.test(s.fragmentShader),
  bothLights: /directionalLights\\[i\\]\\.direction/.test(s.fragmentShader)
      && /pointLights\\[i\\]\\.position/.test(s.fragmentShader),
}));
""", _LIBS)
    assert out["gate"], out["fn"]
    # Everything the function returns sits inside the gate's one factor,
    # so a light on the viewer's side contributes exactly zero and not a
    # floor.  Checked by balancing the parens, not by a regex that would
    # pass an unbracketed second term.
    ret = out["ret"]
    assert ret.startswith("b * ("), ret
    depth = 0
    for i, ch in enumerate(ret[4:], start=4):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            assert i == len(ret) - 1, ret
            break
    assert out["lobe"], out["fn"]
    assert out["facesViewer"]
    # A lamp inside a shade is a point light; a sun behind a leaf is a
    # directional.  Summing only one of them halves what this can shade.
    assert out["bothLights"]


def test_transmitted_light_is_added_not_painted_onto_the_albedo():
    """A lantern at dusk is brighter than anything falling on its front,
    so an albedo lift would cap it at the ambient and read as pale paper.
    The film is a reflection sitting on top of the surface for the same
    reason.  Neither may touch `diffuseColor`, which belongs to whichever
    wear or aging patch shares the material."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchTranslucency, patchIridescence } from './lib/finish.js';
const bodyFor = (fn) => {
  const m = new THREE.MeshStandardMaterial();
  fn(m, {});
  return bodyOf(compile(m));
};
const t = bodyFor(patchTranslucency), i = bodyFor(patchIridescence);
console.log(JSON.stringify({
  transAdds: /totalEmissiveRadiance\\s*\\+=/.test(t),
  iriAdds: /totalEmissiveRadiance\\s*\\+=/.test(i),
  transKeepsAlbedo: !/diffuseColor[.\\w]*\\s*[*+-]?=/.test(t),
  iriKeepsAlbedo: !/diffuseColor[.\\w]*\\s*[*+-]?=/.test(i),
}));
""", _LIBS)
    assert out["transAdds"] and out["iriAdds"]
    assert out["transKeepsAlbedo"], "translucency must not repaint albedo"
    assert out["iriKeepsAlbedo"], "a film reflects, it does not repaint"


def test_the_transmitted_colour_deepens_along_the_path():
    """PORT ADDITION.  Absorption is per channel, so the colour light
    comes out as is the colour of the path it crossed: a thin crossing is
    a pale wash of `color` and a long one is `color` itself.  One flat
    tint over the whole body — what the reference had — is why a wax
    candle came back as a white stick with a warm edge.  Measured on the
    close render: the candle's local hue spread went 3.5 -> 5.9 degrees
    and the jade egg's saturation 0.167 -> 0.271 with its luminance DOWN,
    which is the gradient appearing rather than a brightness lift."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchTranslucency } from './lib/finish.js';
const m = new THREE.MeshStandardMaterial();
patchTranslucency(m, {});
const s = compile(m);
const f = flat(s.fragmentShader);
console.log(JSON.stringify({
  tint: (f.match(/vec3 trTint = [^;]*;/) || [''])[0],
  out: (f.match(/vec3 trOut = [^;]*;/) || [''])[0],
  t0: constOf(s.fragmentShader, 'ASTRA_TRS_TINT0'),
  tk: constOf(s.fragmentShader, 'ASTRA_TRS_TINTK'),
  color: s.uniforms.uTrsColor.value.toArray(),
}));
""", _LIBS)
    # The exponent rides the PATH — the same path the level uses, so the
    # two cannot drift — and nothing else.
    assert "pow(uTrsColor" in out["tint"], out["tint"]
    assert "trPath / ASTRA_TRS_MFP" in out["tint"], out["tint"]
    assert "trTint *" in out["out"] or "trTint * (" in out["out"], out["out"]
    t0, tk = out["t0"], out["tk"]
    # Even the thinnest crossing is already tinted (lantern paper is warm
    # at half a millimetre), and a long one is deeper than the option.
    assert 0.2 < t0 < 1.0, t0
    assert tk > 0.5, tk
    # A real gradient, not a rounding: on the default parchment the thin
    # crossing must come back PALER than the medium itself and one mean
    # free path deeper must lose a visible share of that.
    g = out["color"][1]
    near, far = g ** t0, g ** (t0 + tk)
    assert near > g * 1.10, (near, g)
    assert far < 0.75 * near, (near, far)


def test_the_transmitted_peak_is_capped_in_colour_not_clipped_to_white():
    """PORT ADDITION.  A lamp pressed against a paper shade drives the
    transmitted term past 6 on our host, which tone-maps in the fragment
    tail with no post chain: 48% of the shade's pixels came back 255 on
    every channel and the warmth that IS the effect was the first thing
    lost.  The knee is ONE scalar on the peak channel — scaling all three
    by the same number moves the level and leaves the hue exactly where
    it was, which a per-channel clamp does not — and it opens at half the
    ceiling, so every ordinary glow passes through untouched.  Measured
    after: white pixels 0.4834 -> 0.0000 of the shade, saturation 0.0825
    -> 0.1443, and the frame's own blown_frac still 0."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchTranslucency } from './lib/finish.js';
const m = new THREE.MeshStandardMaterial();
patchTranslucency(m, {});
const s = compile(m);
const f = flat(s.fragmentShader);
console.log(JSON.stringify({
  peakLine: (f.match(/float trPk = [^;]*;/) || [''])[0],
  knee: (f.match(/trOut \\*= [^;]*;/) || [''])[0],
  emit: (f.match(/totalEmissiveRadiance \\+= trOut;/) || [''])[0],
  peak: constOf(s.fragmentShader, 'ASTRA_TRS_PEAK'),
}));
""", _LIBS)
    assert "max(max(trOut.r, trOut.g), trOut.b)" in out["peakLine"], out
    assert out["knee"].startswith("trOut *= "), out["knee"]
    assert out["emit"], "the compressed value is what gets added"
    peak = out["peak"]
    # Bloom-friendly: an emissive that peaks at 20 is a white hole on any
    # host, with or without a bloom pass.
    assert 1.5 <= peak <= 4.0, peak

    def knee(x):
        return x * peak / (peak + max(x - 0.5 * peak, 0.0))

    # Ordinary glows are untouched, a lamp against the shade is bounded,
    # and the map never turns over (brighter in must stay brighter out).
    assert knee(0.4) == pytest.approx(0.4)
    assert knee(peak * 0.5) == pytest.approx(peak * 0.5)
    assert knee(6.5) < peak
    assert knee(1e4) < peak * 1.001
    assert knee(9.0) > knee(6.5) > knee(3.0)


def test_the_film_hue_rides_the_view_angle_not_the_surface():
    """A hue that only varies over the surface is a rainbow decal.  The
    ray bends INSIDE the film (Snell off `ior`), so the optical path it
    crosses shortens toward grazing and the fringe order slides as the
    camera moves — that shortening is the sweep.  The film's own
    thickness field varies too, and it must stay the SMALLER of the two
    or the decal wins."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchIridescence } from './lib/finish.js';
const m = new THREE.MeshStandardMaterial();
patchIridescence(m, {});
const s = compile(m);
const fs = flat(s.fragmentShader);
const line = (name) => (fs.match(
    new RegExp('float ' + name + ' = [^;]*;')) || [''])[0];
console.log(JSON.stringify({
  ior: s.uniforms.uIriIor.value,
  d0: constOf(fs, 'ASTRA_IRI_D0'),
  dv: constOf(fs, 'ASTRA_IRI_DV'),
  cosLine: line('irCos'),
  ctLine: line('irCt'),
  opdLine: line('irOpd'),
  thicknessLine: line('irD'),
  filmTakesOpd: /astraIriFilm\\(irOpd\\)/.test(fs),
}));
""", _LIBS)
    assert "dot(irN, irV)" in out["cosLine"], out["cosLine"]
    assert "irCos * irCos" in out["ctLine"] and "uIriIor" in out["ctLine"]
    # Both the bend and the path it crosses ride the option, so a baked
    # index would freeze the sweep for every other caller.
    assert "irCt" in out["opdLine"] and "uIriIor" in out["opdLine"]
    assert out["filmTakesOpd"]
    # The thickness field is position only: if the angle leaked into it
    # the two would be indistinguishable and this test would prove
    # nothing.
    assert "vAstraWorld" in out["thicknessLine"]
    assert "irCos" not in out["thicknessLine"]
    assert "irCt" not in out["thicknessLine"]
    # Now the sizes.  Walking round the object shortens the path by
    # 1 - cos(theta_t) at grazing; walking ACROSS it swings the film by
    # +/- dv.  The angular swing must be the bigger of the two — the port
    # raised dv 45 -> 60 nm so a FLAT film bands at all, and this is the
    # law that bounds how far that could go.
    ior = out["ior"]
    angular = 1 - math.sqrt(1 - 1 / (ior * ior))
    positional = 2 * out["dv"] / out["d0"]
    assert angular > positional, (angular, positional)


def test_the_film_is_strongest_at_grazing_and_its_ior_says_how_much():
    """A film's colour is a reflection, so it is faint face-on and takes
    the surface over at the rim — that ramp is as much of the cue as the
    hue itself.  Both ends come from `ior`: four times the single-surface
    Fresnel face-on (where two-beam interference peaks) rising to total
    reflection at grazing."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchIridescence } from './lib/finish.js';
const one = (ior) => {
  const m = new THREE.MeshStandardMaterial();
  patchIridescence(m, { ior });
  const s = compile(m);
  return { ior: s.uniforms.uIriIor.value, fs: flat(s.fragmentShader) };
};
const a = one(1.33), b = one(1.8);
console.log(JSON.stringify({
  soap: a.ior, chitin: b.ior,
  weight: (a.fs.match(/float irW = [^;]*;/) || [''])[0],
  r0: (a.fs.match(/irR0 = [^;]*;/g) || []).join(' '),
  sameSource: a.fs === b.fs,
}));
""", _LIBS)
    assert "astraFresnel(irN, irV, 4.0)" in out["weight"], out["weight"]
    assert "mix(irR0, 1.0" in out["weight"], out["weight"]
    assert "4.0 * irR0 * irR0" in out["r0"], out["r0"]

    def face_on(n):
        return 4 * ((n - 1) / (n + 1)) ** 2

    # Faint face-on, and a denser film returns more of it.
    assert face_on(out["soap"]) < 0.15
    assert face_on(out["chitin"]) > 2 * face_on(out["soap"])
    # ...all of it by UNIFORM, so two films share one compiled program.
    assert out["sameSource"]


def test_the_film_splits_the_light_the_scene_actually_has():
    """PORT ADDITION, and the biggest one.  The reference added the film
    as a fixed white veil, independent of every light: it glowed in the
    dark, it carried no scene colour, and on a puddle already reflecting
    a bright sky it only washed the pool out (measured: hue spread 98 deg
    at saturation 0.054 — noise, not colour).

    What it adds now is the light the scene HAS: ambient, plus the
    hemisphere fill as a wash (a film reflects the whole sky), plus a
    glint share of every sun and lamp — punctual, so what a mirror
    returns of them is a lobe around the reflect() direction, not a wash.
    That last distinction is not cosmetic: as a wash, one lamp two metres
    off a night puddle lit the WHOLE pool rainbow (night render, first
    pass).  Split into a unit-luminance tint and a saturating level, so
    `strength` keeps meaning what the docs say and a near lamp cannot
    turn a slick into a second lamp."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchIridescence } from './lib/finish.js';
const m = new THREE.MeshStandardMaterial();
patchIridescence(m, {});
const s = compile(m);
const fs = flat(s.fragmentShader);
console.log(JSON.stringify({
  seed: (fs.match(/vec3 irLit = [^;]*;/) || [''])[0],
  hemi: /hemisphereLights\\[i\\]\\.skyColor/.test(fs)
      && /hemisphereLights\\[i\\]\\.groundColor/.test(fs),
  hemiGuard: /#if NUM_HEMI_LIGHTS > 0/.test(s.fragmentShader),
  dirGuard: /#if NUM_DIR_LIGHTS > 0/.test(s.fragmentShader),
  pointGuard: /#if NUM_POINT_LIGHTS > 0/.test(s.fragmentShader),
  mirror: (fs.match(/vec3 irRef = [^;]*;/) || [''])[0],
  dirTerm: (fs.match(
      /irLit \\+= directionalLights\\[i\\]\\.color [^;]*;/) || [''])[0],
  pointTerm: (fs.match(
      /irLit \\+= pointLights\\[i\\]\\.color [^;]*;/) || [''])[0],
  lumLine: (fs.match(/float irLum = [^;]*;/) || [''])[0],
  tintLine: (fs.match(/vec3 irTint = [^;]*;/) || [''])[0],
  levelLine: (fs.match(/float irLevel = [^;]*;/) || [''])[0],
  emit: (fs.match(/totalEmissiveRadiance \\+= irTint[^;]*;/) || [''])[0],
  veil: constOf(s.fragmentShader, 'ASTRA_IRI_VEIL'),
  ref: constOf(s.fragmentShader, 'ASTRA_IRI_REF'),
  haze: constOf(s.fragmentShader, 'ASTRA_IRI_HAZE'),
  lobe: constOf(s.fragmentShader, 'ASTRA_IRI_LOBE'),
}));
""", _LIBS)
    # It starts from the scene's ambient and adds the sky the same way
    # three's own hemisphere term does — no hardcoded light colour.
    assert out["seed"] == "vec3 irLit = ambientLightColor;", out["seed"]
    assert out["hemi"], "the hemisphere fill IS the sky for a sunRig scene"
    assert out["hemiGuard"] and out["dirGuard"] and out["pointGuard"], out
    # A punctual light arrives as a GLINT: weighted around the mirror
    # direction, with only a haze share everywhere else.
    assert "reflect(-irV, irN)" in out["mirror"], out["mirror"]
    for term in ("dirTerm", "pointTerm"):
        assert "ASTRA_IRI_HAZE" in out[term], out[term]
        assert "dot(irRef," in out[term], out[term]
        assert "ASTRA_IRI_LOBE" in out[term], out[term]
    assert 0.0 < out["haze"] <= 0.25, out["haze"]
    assert out["lobe"] >= 4.0, out["lobe"]
    # Colour and level are separated, and the level SATURATES.
    assert "0.2126" in out["lumLine"], out["lumLine"]
    assert out["tintLine"].startswith("vec3 irTint = irLit /"), out
    assert "irLum + ASTRA_IRI_REF" in out["levelLine"], out["levelLine"]
    ref = out["ref"]
    level = lambda lum: lum / (lum + ref)  # noqa: E731
    assert level(0.0) == 0.0, "a film in the dark must not glow"
    assert level(4.0) < 1.0 and level(4.0) > 2 * level(0.3)
    assert level(40.0) - level(8.0) < 0.15, "a near lamp must not run away"
    # And what is added is the scene's tint times a swing about the mean,
    # not a white veil: VEIL is only the share left as a plain lift.
    assert out["emit"].startswith("totalEmissiveRadiance += irTint"), out
    assert "astraIriFilm(irOpd) - (1.0 - ASTRA_IRI_VEIL)" in out["emit"]
    assert 0.0 < out["veil"] < 0.6, out["veil"]


def test_the_film_holds_its_mean_so_the_swing_is_hue_not_brightness():
    """A fringe that swings brightness as well as hue reads as a stain,
    and after the mean is subtracted it would also mean the film could
    only ever darken or only ever brighten.  `astraIriFilm` is normalised
    to a mean of one before the order wash mixes it toward white, so the
    swing is symmetric about zero — replicated here in Python from the
    constants the GLSL actually carries."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchIridescence } from './lib/finish.js';
const m = new THREE.MeshStandardMaterial();
patchIridescence(m, { ior: 1.4 });
const s = compile(m);
const fs = flat(s.fragmentShader);
console.log(JSON.stringify({
  norm: (fs.match(/c \\/= [^;]*;/) || [''])[0],
  wash: (fs.match(/return mix\\(vec3\\(1\\.0\\), c, [^;]*;/) || [''])[0],
  lam: (fs.match(/vec3 lam = [^;]*;/) || [''])[0],
  veil: constOf(s.fragmentShader, 'ASTRA_IRI_VEIL'),
  floor: parseFloat((fs.match(
      /c \\/= max\\(\\(c\\.r \\+ c\\.g \\+ c\\.b\\) \\/ 3\\.0, ([0-9.]+)\\)/)
      || [0, 'NaN'])[1]),
}));
""", _LIBS)
    assert "0.5 + 0.5 * cos" not in out["norm"]
    assert "(c.r + c.g + c.b) / 3.0" in out["norm"], out["norm"]
    assert "exp(-0.12 * ord * ord)" in out["wash"], out["wash"]
    lam = [float(v) for v in re.findall(r"[0-9]+\.[0-9]+", out["lam"])]
    assert len(lam) == 3 and min(lam) > 400 and max(lam) < 700, lam

    floor, veil = out["floor"], out["veil"]

    def film(opd):
        c = [0.5 + 0.5 * math.cos(2 * math.pi * opd / w + math.pi)
             for w in lam]
        m = max(sum(c) / 3, floor)
        c = [v / m for v in c]
        ordr = opd / 550.0
        w = math.exp(-0.12 * ordr * ordr)
        return [1 - w + w * v for v in c]

    # Over one sweep of the first two orders the mean channel stays at
    # one, so `- (1 - VEIL)` leaves a swing that is as much below zero as
    # above it plus exactly VEIL of a lift.
    samples = [film(400 + 8 * i) for i in range(150)]
    mean = sum(sum(s) / 3 for s in samples) / len(samples)
    assert 0.9 < mean < 1.12, mean
    lift = mean - (1 - veil)
    assert abs(lift - veil) < 0.12, (lift, veil)
    # And the swing is real: some sample is a long way off its own mean.
    swing = max(max(s) - min(s) for s in samples)
    assert swing > 0.5, swing
    # ...but bounded, or a beetle's back reads as a hologram.  The floor
    # on the normaliser is what caps it (the port raised it 0.18 -> 0.26;
    # measured on the close render, the shell's saturation went 0.234 ->
    # 0.452 while its luminance FELL 0.585 -> 0.457, which is colour
    # arriving rather than a white lift).
    assert max(max(s) for s in samples) < 1 / floor + 0.01
    assert floor >= 0.2, floor


def test_every_option_is_a_uniform_not_baked_source():
    """The first material to compile a cache key decides the GLSL every
    material with that key gets, so an option baked into the source would
    be silently forced on every other caller.  Two very differently tuned
    materials must therefore differ in their uniforms and not by one
    character of shader."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchTranslucency, patchIridescence } from './lib/finish.js';
const one = (t, i) => {
  const m = new THREE.MeshStandardMaterial();
  patchTranslucency(m, t);
  patchIridescence(m, i);
  const s = compile(m);
  const u = {};
  for (const k of Object.keys(s.uniforms)) {
    const v = s.uniforms[k].value;
    u[k] = (v && v.toArray) ? v.toArray()
        : ((v && v.getHex) ? v.getHex() : v);
  }
  return { u, fs: s.fragmentShader, vs: s.vertexShader,
           key: m.customProgramCacheKey() };
};
const a = one({ thickness: 0.001, strength: 0.5, power: 1.5,
                color: 0x223344, seed: 2 },
              { strength: 0.2, scale: 0.1, ior: 1.2, seed: 3 });
const b = one({ thickness: 0.4, strength: 3.0, power: 8,
                color: 0xffeecc, seed: 41 },
              { strength: 0.9, scale: 4.0, ior: 1.9, seed: 77 });
const differs = Object.keys(a.u).filter(
    (k) => JSON.stringify(a.u[k]) !== JSON.stringify(b.u[k]));
console.log(JSON.stringify({
  sameSource: a.fs === b.fs && a.vs === b.vs,
  sameKey: a.key === b.key,
  differs: differs.sort(),
}));
""", _LIBS)
    assert out["sameSource"], "an option was baked into the GLSL"
    assert out["sameKey"]
    assert out["differs"] == [
        "uIriAmt", "uIriIor", "uIriScale", "uIriSeed",
        "uTrsAmt", "uTrsColor", "uTrsPow", "uTrsSeed", "uTrsThick",
    ], out["differs"]


def test_the_patch_names_are_disjoint_from_the_libraries_it_chains_with():
    """Patches chain, and the cache key is every name in the chain.  Two
    libraries that reused a name would collide into one program while the
    second one's GLSL was never compiled — a defect with no error and no
    visible cause."""
    out = measure("""
import * as THREE from 'three';
const names = async (file) => {
  const mod = await import(file);
  const out = [];
  for (const key of Object.keys(mod)) {
    if (!/^patch/.test(key) || typeof mod[key] !== 'function') continue;
    const m = new THREE.MeshStandardMaterial();
    try { mod[key](m, {}); } catch (e) { continue; }
    for (const p of (m.userData.astraPatches || [])) out.push(p.name);
  }
  return out;
};
const mine = await names('./lib/finish.js');
const theirs = {};
for (const f of ['foliage_shade.js', 'surface_wear.js', 'aging.js',
                 'terrain_shade.js', 'waterside.js', 'dapple.js']) {
  theirs[f] = await names('./lib/' + f);
}
console.log(JSON.stringify({ mine, theirs }));
""", _LIBS + ("foliage_shade.js", "surface_wear.js", "aging.js",
              "terrain_shade.js", "waterside.js", "dapple.js"))
    mine = set(out["mine"])
    assert mine == {"finish:base", "finish:translucency",
                    "finish:iridescence"}, sorted(mine)
    for lib, names in out["theirs"].items():
        assert names, f"{lib} registered no patches — probe is blind"
        assert not (mine & set(names)), (lib, sorted(mine & set(names)))


def test_one_seed_lays_the_same_grain_and_the_same_film_twice():
    """A scene is rendered many times and re-rendered after a fix; a
    surface that reshuffled between rounds would make every judge
    comparison meaningless.  The seed is the only randomness here, it is
    a uniform, and it must move the field far rather than by one cell."""
    out = measure(_COMPILE + """
import * as THREE from 'three';
import { patchTranslucency, patchIridescence } from './lib/finish.js';
const one = (seed) => {
  const m = new THREE.MeshStandardMaterial();
  patchTranslucency(m, { seed });
  patchIridescence(m, { seed });
  const s = compile(m);
  return { trs: s.uniforms.uTrsSeed.value.toArray(),
           iri: s.uniforms.uIriSeed.value.toArray(),
           src: s.fragmentShader };
};
const a = one(3), b = one(3), c = one(4);
const dist = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
console.log(JSON.stringify({
  repeatable: JSON.stringify(a.trs) === JSON.stringify(b.trs)
      && JSON.stringify(a.iri) === JSON.stringify(b.iri),
  trsMoved: dist(a.trs, c.trs),
  iriMoved: dist(a.iri, c.iri),
  apart: dist(a.trs, a.iri),
  sameSource: a.src === c.src,
}));
""", _LIBS)
    assert out["repeatable"], "same seed, different field"
    assert out["trsMoved"] > 5, out["trsMoved"]
    assert out["iriMoved"] > 5, out["iriMoved"]
    assert out["apart"] > 5, out["apart"]
    assert out["sameSource"], "the seed reached the GLSL"
