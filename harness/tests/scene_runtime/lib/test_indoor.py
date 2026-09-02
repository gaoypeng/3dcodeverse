"""An interior that is LIT, the surfaces hands have been on, and a crowd.

Ported from the reference suite 2026-09-01.  `indoor.js` answers three
failures.  A room filled with ambient reads as a MODEL of a room: nothing
bleeds, so a white ceiling beside a red wall stays white and the eye is
told there is no light in here, only exposure.  A pane nobody has touched
says the same about the props.  A square with nobody in it is a set.

Each export loses its whole point if one property goes.  The bounce must
fall to EXACTLY zero at its reach — a wash that never ends is ambient
again, the thing it replaces — and it must be LIGHT, because an albedo
lift is capped at what already lands on the surface and the shaded soffit
that needs it most would take none.  The smudge must move GLOSS and not
colour, or it is a stain.  The crowd must stay one draw call, seated on
the ground, and out of the override passes that draw a transparent card
as a solid wall.

THE PORT'S OWN LAWS, each measured on our host (fx/out/indoor/, exposure
1.0, no post chain) against a control render of the same frame with the
patches off:

1. A PLANE'S FORM FACTOR IS THE UN-SQUARED HALF-LAMBERT.  Squaring it is
   exact for neither limit of a Lambertian half-space: a ceiling facing a
   lit floor sees it over its whole hemisphere (factor 1, which squaring
   also gives) and a WALL over exactly half of it (factor 0.5, where
   squaring gives 0.25).  The wall above a floor is the geometry every
   room has, and it was getting half its bleed.  Measured on the white
   plaster of the showcase loggia, mean absolute difference against the
   unpatched control: skirting 3.22 -> 5.74 (close) and 3.87 -> 6.91
   (mid) of 255, red-rug bleed 4.34 -> 7.55, and the SOFFIT — the
   parallel case, already correct — 1.86 -> 2.14, unchanged as the
   theory says it must be.

2. A WASH THAT KEEPS ITS HUE TO THE LAST CENTIMETRE IS A LAMP.  Light
   that has travelled further has bounced off more than one thing, so the
   tint desaturates across the reach (32% at the far edge).

3. BOUNCE IS THE SMOOTHEST GRADIENT IN A ROOM.  On the widest flat it
   lands on it climbs the 8-bit ladder in bands; half a code of hashed
   dither, only where there is bleed, took the gradient strip from 136 to
   156 distinct colours (row-to-row noise 0.452 -> 0.566).

4. GREASE IS A FILM WITH RIDGES, NOT A SET OF STRIPES.  With the arcs
   alone a touched pane is a few bright bands on clean glass and the eye
   reads bands as paint.  The film term took the smudge on the showcase's
   polished counter from 0.91 to 3.01 mean absolute difference and from
   23% to 55% of the surface changed by more than 1/255.

5. A BACKLIT CARD IS A SILHOUETTE WITH A LIT EDGE.  A crowd with the sun
   behind it had a hard `max(dot, 0)` terminator on a coarse bowed
   normal, so every figure printed as one flat ambient tone.  Wrapped
   diffuse plus a rim on the sun-side edge: the darkest crowd pixels rose
   from 15.9 to 24.7 of 255, distinct colours over the crowd from 4337 to
   4726.  Plus per-person VALUE (a street is pale coats and dark ones,
   not one value in two hues) and a `legColor` default off the floor —
   an albedo under about 0.02 linear is a hole no light reopens.
"""

from __future__ import annotations

import re

from tests.scene_runtime.lib._probe import LIB_DIR, compile_scene, measure

_LIBS = ("shader.js", "noise.js", "indoor.js", "surface_wear.js")
_SRC = LIB_DIR / "indoor.js"

# One material carrying BOTH patches under two more from another library —
# the GLSL namespace collision that only shows up when three libraries share
# one material — plus an InstancedMesh (whose world position needs
# instanceMatrix folded in by hand) and the crowd field.
_BODY = """
  const mat = new THREE.MeshStandardMaterial({
    color: 0xd8d2c6, roughness: 0.45, metalness: 0.1 });
  patchMicroBreakup(mat, { scale: 0.6, strength: 0.12, seed: 5 });
  patchEdgeWear(mat, { strength: 0.4, seed: 6 });
  patchBounceLight(mat, {
    sources: [{ position: [-3, 1.4, 0], normal: [1, 0, 0],
                color: 0xa8201a, reach: 3.5 },
              { position: [0, 0, 0], normal: [0, 1, 0],
                color: 0x3f7a2a, reach: 2.2 },
              { position: [2, 0.8, 1], color: 0xffcc88, reach: 1.5 }],
    up: [0, 1, 0], strength: 1.2 });
  patchFingerprints(mat, { amount: 0.6, smear: 0.4, seed: 3 });
  const wall = new THREE.Mesh(new THREE.BoxGeometry(6, 3, 0.3), mat);
  wall.position.set(0, 1.5, -2);
  HOST.add(wall);
  const posts = new THREE.InstancedMesh(
      new THREE.BoxGeometry(0.12, 1.1, 0.12), mat, 8);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 8; i++) {
    m4.makeTranslation(-3 + i * 0.9, 0.55, 0.6);
    posts.setMatrixAt(i, m4);
  }
  posts.instanceMatrix.needsUpdate = true;
  HOST.add(posts);
  HOST.add(makeCrowdImposters({
    count: 40, extent: 20, heightAt: (x, z) => 0.05 * Math.sin(x * 0.3),
    seed: 5 }));
"""

# A SCENE, not an asset: the reference's asset mode built its own unfogged,
# point-light-free scene, so USE_FOG was never defined and NUM_POINT_LIGHTS
# was zero — two whole branches of this library never reached a compiler.
# A room is exactly where both exist.  Ours boots a scene either way, so the
# two reference compile tests are one test here.
_SCENE = """
import * as THREE from 'three';
import { makeCrowdImposters, patchBounceLight, patchFingerprints }
    from './lib/indoor.js';
import { patchEdgeWear, patchMicroBreakup } from './lib/surface_wear.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 20, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xb9c6d4, 0.012);
  scene.add(new THREE.HemisphereLight(0xc8d6e6, 0x8a8478, 1.0));
  const sun = new THREE.DirectionalLight(0xfff1da, 2.4);
  sun.position.set(14, 9, 5);
  scene.add(sun);
  const lamp = new THREE.PointLight(0xfff4e2, 30, 0, 2);
  lamp.position.set(1.2, 2.4, 2.0);
  scene.add(lamp);
""" + _BODY.replace("HOST", "scene") + """
  return {
    scene,
    cameras: [{ name: 'a', position: [8, 4, 10], lookAt: [0, 1.5, 0],
                fov: 45 }],
    update() {},
  };
}
"""

# Pull a scalar GLSL helper out of the SHIPPED source and run it as JS.  The
# two dialects agree on everything here except the declarations, so the
# numbers below are the numbers the GPU computes — not a second
# implementation that could drift from the one that renders.
_GLSL_AS_JS = """
const glslFn = (src, name, args) => {
  const re = new RegExp('float ' + name + '\\\\(([\\\\s\\\\S]*?)\\\\)\\\\s*\\\\{');
  const m = re.exec(src);
  if (!m) throw new Error('no ' + name + '() in the shipped source');
  let depth = 0, end = -1;
  for (let i = src.indexOf('{', m.index); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) { end = i; break; }
  }
  const body = src.slice(src.indexOf('{', m.index) + 1, end)
      .replace(/\\bfloat\\b/g, 'let');
  const fn = new Function(...args, 'clamp', 'max', 'min', 'fract',
                          'step', 'mix', 'pow', 'exp', 'sin', 'floor',
                          body);
  return (...v) => fn(...v,
      (x, a, b) => Math.min(Math.max(x, a), b), Math.max, Math.min,
      (x) => x - Math.floor(x),
      (e, x) => (x < e ? 0 : 1),
      (a, b, t) => a + (b - a) * t,
      Math.pow, Math.exp, Math.sin, Math.floor);
};
"""

# The same trick for the ONE helper here that takes vectors: astraFresnel is
# shader.js's, shared by every grazing-angle effect in the library, so the
# smudge's angle response is read out of the shipped GLSL_UTIL.
_FRESNEL_AS_JS = """
const fresnelOf = (src) => {
  const i = src.indexOf('float astraFresnel(');
  if (i < 0) throw new Error('no astraFresnel() in GLSL_UTIL');
  const open = src.indexOf('{', i);
  let depth = 0, end = -1;
  for (let k = open; k < src.length; k++) {
    if (src[k] === '{') depth++;
    else if (src[k] === '}' && --depth === 0) { end = k; break; }
  }
  const fn = new Function('n', 'v', 'power', 'pow', 'clamp', 'dot',
                          'normalize', src.slice(open + 1, end));
  const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const norm3 = (a) => {
    const l = Math.hypot(a[0], a[1], a[2]);
    return [a[0] / l, a[1] / l, a[2] / l];
  };
  return (n, v, p) => fn(n, v, p, Math.pow,
      (x, a, b) => Math.min(Math.max(x, a), b), dot3, norm3);
};
"""


def test_all_three_compile_in_a_room_with_fog_and_a_point_light():
    """Both patches chained under two from another library, on one
    material, plus an InstancedMesh and the crowd field — in a FOGGED
    scene with a point light, because the smudge sums point lights and
    every custom shader here has a fog branch.  `patchStandard` THROWS on
    two patches defining one function name, which only the GPU, or this,
    can settle."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    # A WARN here is the fog trap or the GTAO trap, either of which ships
    # an effect that looks wrong rather than one that errors.
    assert "WARN" not in out, out
    assert "no fog chunks" not in out, out


def test_the_bounce_falls_off_and_is_zero_beyond_its_reach():
    """Read straight out of the GLSL the patch ships.  A bare inverse
    square never reaches zero, so every surface in the scene would keep a
    wash of every bouncer in it — ambient light again, the one thing
    colour bleed exists to replace.  A plane must also lose its light more
    slowly than a point, or a wall bleeds like a bulb."""
    out = measure(_GLSL_AS_JS + """
import * as THREE from 'three';
import { patchBounceLight } from './lib/indoor.js';

const m = new THREE.MeshStandardMaterial({ color: 0x888888 });
patchBounceLight(m, { sources: [[0, 0, 0]], strength: 1 });
const patch = m.userData.astraPatches.find(
    (p) => p.name === 'indoor:bounce');
const fall = glslFn(patch.fragmentHead, 'astraBncFall',
                    ['d', 'r', 'plane']);
const curve = (plane) => {
  const out = [];
  for (let i = 0; i <= 40; i++) out.push(fall(i * 0.1, 3, plane));
  return out;
};
console.log(JSON.stringify({
  point: curve(0), plane: curve(1),
  at0: [fall(0, 3, 0), fall(0, 3, 1)],
  atR: [fall(3, 3, 0), fall(3, 3, 1)],
  beyond: [fall(3.5, 3, 0), fall(9, 3, 1), fall(400, 3, 0)],
  half: [fall(1.5, 3, 0), fall(1.5, 3, 1)],
}));
""", _LIBS)
    assert out["at0"] == [1, 1], out["at0"]
    for name in ("point", "plane"):
        curve = out[name]
        for a, b in zip(curve, curve[1:], strict=False):
            assert b <= a, f"{name} bounce rises with distance: {a} -> {b}"
        inside = [c for c in curve[:30] if c > 0]
        assert len(inside) > 25 and inside[0] > 8 * inside[-1], inside
    # EXACTLY zero at the reach and past it, both shapes.
    assert out["atR"] == [0, 0], out["atR"]
    assert out["beyond"] == [0, 0, 0], out["beyond"]
    # A wall is a broad source and a rug is not: at half the reach the
    # plane must still be carrying several times what the point is.
    assert out["half"][1] > 2.0 * out["half"][0], out["half"]


def test_a_plane_bounces_with_the_form_factor_and_a_point_does_not():
    """THE PORT'S FIRST LAW.  The half-lambert un-squared is the EXACT
    cosine-weighted form factor at both limits of a Lambertian
    half-space — 1.0 for a ceiling facing a lit floor, 0.5 for a wall
    standing on one — and squaring it lands the wall at 0.25.  That is
    half the bleed on the one geometry every room has, and on our host it
    was the difference between 3.9 and 6.9 of 255 on the plaster.  A
    POINT keeps the square: a rug really is directional."""
    out = measure("""
import * as THREE from 'three';
import { patchBounceLight } from './lib/indoor.js';

const m = new THREE.MeshStandardMaterial();
patchBounceLight(m, { sources: [{ position: [0, 0, 0], normal: [0, 1, 0],
                                  reach: 4 }] });
const body = m.userData.astraPatches.find(
    (p) => p.name === 'indoor:bounce').fragmentBody;
// the wrap the shipped body applies, run at the three geometries that
// matter: facing the source, perpendicular to it, turned away.
const wrap = (lam, plane) => {
  const sq = lam * lam;
  return plane * lam + (1 - plane) * sq;
};
console.log(JSON.stringify({
  body,
  plane: [wrap(1, 1), wrap(0.5, 1), wrap(0, 1)],
  point: [wrap(1, 0), wrap(0.5, 0), wrap(0, 0)],
}));
""", _LIBS)
    body = out["body"]
    assert "float bncWrap = mix(bncLam * bncLam, bncLam, bncPl);" in body, body
    assert "bncSum += bncTint * (bncFront * bncWrap" in body, body
    # ceiling over a floor 1.0, wall on a floor 0.5, facing away 0.
    assert out["plane"] == [1, 0.5, 0], out["plane"]
    # and the point is still the tighter, squared response.
    assert out["point"] == [1, 0.25, 0], out["point"]


def test_the_wash_loses_the_bouncer_hue_as_it_spreads_and_is_dithered():
    """A red bleed as red at its far edge as it is under the wall reads as
    a coloured LAMP: light that has come further has bounced off more than
    one thing.  And bounce is the smoothest gradient a room has, on the
    widest flat in it — without a dither it climbs the 8-bit ladder in
    bands (136 -> 156 distinct colours over the showcase's gradient
    strip)."""
    out = measure("""
import * as THREE from 'three';
import { patchBounceLight } from './lib/indoor.js';

const m = new THREE.MeshStandardMaterial();
patchBounceLight(m, { sources: [{ position: [0, 0, 0], normal: [0, 1, 0],
                                  color: 0xc23a2c, reach: 3 }] });
const body = m.userData.astraPatches.find(
    (p) => p.name === 'indoor:bounce').fragmentBody;
const amp = /\\)\\s*\\*\\s*(0\\.\\d+)\\s*\\*\\s*step\\(/.exec(body);
console.log(JSON.stringify({
  body,
  desatAt: /0\\.32\\s*\\*\\s*bncX/.test(body),
  ditherAmp: amp ? parseFloat(amp[1]) : null,
}));
""", _LIBS)
    body = out["body"]
    # the tint is mixed toward its own LUMA (a grey of the same
    # brightness), so the wash goes pale, never dark, as it spreads.
    assert "vec3 bncTint = mix(uBncCol[i]," in body, body
    assert "vec3(0.2126, 0.7152, 0.0722))" in body, body
    assert out["desatAt"], body
    # gated on bncX, which is 0 at the source: the skirting under a red
    # wall keeps the full hue.
    assert "float bncX = clamp(bncDist / max(uBncSpan[i].x, 1e-4)" in body
    # Half a code of hash, and only where there is bleed to band.
    assert "astraHash11(gl_FragCoord.x" in body, body
    assert "step(1e-4, bncLvl)" in body, body
    assert 0.002 <= out["ditherAmp"] <= 0.012, out["ditherAmp"]


def test_the_bounce_is_light_and_never_repaints_the_albedo():
    """A bounce lifted into ALBEDO is capped at the light already landing
    on the surface, so the shaded soffit under an eave — the one place
    colour bleed is the whole cue — would take none of it.  It has to ADD,
    tinted by the receiver's own colour because arriving light is
    reflected, and it must never assign over diffuseColor, which would
    delete every patch that ran before it."""
    out = measure("""
import * as THREE from 'three';
import { patchBounceLight } from './lib/indoor.js';

const m = new THREE.MeshStandardMaterial({ color: 0x445566 });
patchBounceLight(m, {
  sources: [{ position: [0, 2, 0], normal: [0, 1, 0], color: 0x40ff40,
              reach: 4 },
            { position: [3, 0, 0], color: 0xff4040, reach: 2 }],
  strength: 1.5 });
const p = m.userData.astraPatches.find((q) => q.name === 'indoor:bounce');
const base = m.userData.astraPatches.find((q) => q.name === 'indoor:base');
const u = m.userData.uniforms;
console.log(JSON.stringify({
  body: p.fragmentBody,
  vert: base.vertexBody,
  names: m.userData.astraPatches.map((q) => q.name),
  count: u.uBncCount.value,
  gain: u.uBncGain.value,
  spans: u.uBncSpan.value.map((v) => [v.x, v.y]),
  colors: u.uBncCol.value.map((c) => c.getHex()),
  normals: u.uBncNrm.value.map((v) => v.toArray()),
  emissive: m.emissive !== undefined,
}));
""", _LIBS)
    body = out["body"]
    assert "totalEmissiveRadiance +=" in body, body
    # Reads the albedo as a reflectance, never writes it.
    assert "diffuseColor.rgb" in body, body
    assert not re.search(r"diffuseColor(\.rgb)?\s*=[^=]", body), body
    assert "astraBncFall(" in body, body
    # An instanced post needs instanceMatrix folded in by hand, or every
    # copy is lit as though it stood at the mesh origin.
    assert "USE_INSTANCING" in out["vert"], out["vert"]
    assert out["count"] == 2 and out["gain"] == 1.5, out
    # Stating a normal makes a source a PLANE (span.y 1) and leaving it
    # out makes it a POINT (0) — the whole geometry of the falloff.
    assert out["spans"][0] == [4, 1], out["spans"]
    assert out["spans"][1] == [2, 0], out["spans"]
    # An unused slot is black, so a shorter list cannot leak light.
    assert out["colors"][2:] == [0] * 4, out["colors"]
    # A point with no normal of its own throws along `up`.
    assert out["normals"][1] == [0, 1, 0], out["normals"]


def test_a_bouncer_only_lights_the_side_it_faces():
    """A lawn does not light the cellar under it and a wall throws nothing
    through itself: without the front gate, colour bleed leaks to the far
    side of every surface that casts it — which is the one way this reads
    as a coloured fog rather than as light."""
    out = measure("""
import * as THREE from 'three';
import { patchBounceLight } from './lib/indoor.js';

const m = new THREE.MeshStandardMaterial();
patchBounceLight(m, { sources: [{ position: [0, 0, 0],
                                  normal: [0, 1, 0], reach: 3 }] });
const p = m.userData.astraPatches.find((q) => q.name === 'indoor:bounce');
console.log(JSON.stringify({ body: p.fragmentBody,
                             head: p.fragmentHead }));
""", _LIBS)
    body = out["body"]
    assert "float bncSide = dot(bncD, uBncNrm[i]);" in body, body
    assert "step(1e-4, bncSide)" in body, body
    # A plane is measured PERPENDICULAR to itself and a point radially:
    # that is what makes a long wall bleed along a whole band of ceiling
    # while a rug stays under the table.
    assert "mix(bncR, bncSide, bncPl)" in body, body
    assert "mix(-bncD / max(bncR, 1e-4), -uBncNrm[i], bncPl)" in body, body
    # Half lambert: a hard terminator would cut the ceiling off in a line
    # above a wall that in truth lights all of it.
    assert "dot(bncN, bncL) * 0.5 + 0.5" in body, body


def test_fingerprints_move_gloss_and_never_colour():
    """A smudge that changes the COLOUR of glass is a stain, and the pane
    reads as dirty rather than used.  Gloss is per MATERIAL here
    (`<color_fragment>` runs before `<roughnessmap_fragment>`), so the
    reachable half is a roughness factor composed through
    `composeRoughness` — and it must compose, not re-base, or two
    libraries on one material fight over the same number."""
    out = measure("""
import * as THREE from 'three';
import { patchFingerprints } from './lib/indoor.js';

const rough = (amount, smear) => {
  const m = new THREE.MeshStandardMaterial({ color: 0x224466,
                                             roughness: 0.10 });
  patchFingerprints(m, { amount, smear, seed: 2 });
  return { r: m.roughness, color: m.color.getHex() };
};
const m = new THREE.MeshStandardMaterial({ color: 0x224466,
                                           roughness: 0.10 });
patchFingerprints(m, { amount: 0.8, smear: 0.4, seed: 2 });
const once = m.roughness;
patchFingerprints(m, { amount: 0.8, smear: 0.4, seed: 2 });
const p = m.userData.astraPatches.find(
    (q) => q.name === 'indoor:fingerprints');
console.log(JSON.stringify({
  clean: rough(0, 0), light: rough(0.3, 0.4), heavy: rough(1, 1),
  once, twice: m.roughness,
  keys: Object.keys(m.userData.astraRoughness.factors),
  base: m.userData.astraRoughness.base,
  body: p.fragmentBody,
}));
""", _LIBS)
    clean, light, heavy = out["clean"], out["light"], out["heavy"]
    # Grease scatters: gloss goes DOWN (roughness up) with the amount.
    assert clean["r"] == 0.10, clean
    assert light["r"] > clean["r"], (clean, light)
    assert heavy["r"] > light["r"], (light, heavy)
    # And the albedo is untouched at every setting.
    assert clean["color"] == light["color"] == heavy["color"] == 0x224466
    # Re-applying retunes, never re-bases (composeRoughness).
    assert out["twice"] == out["once"], out
    assert out["keys"] == ["indoor:fingerprints"], out["keys"]
    assert out["base"] == 0.10, out["base"]
    body = out["body"]
    assert "totalEmissiveRadiance +=" in body, body
    assert "diffuseColor" not in body, body


def test_the_smudge_is_hidden_head_on_and_comes_up_at_grazing():
    """The whole cue: grease is invisible until the surface starts
    reflecting, then it takes the pane over.  The weight is the
    dielectric's own — a few percent head-on, everything at grazing — read
    out of shader.js's `astraFresnel`, the same helper every grazing-angle
    effect in this library shares."""
    out = measure(_FRESNEL_AS_JS + """
import * as THREE from 'three';
import { GLSL_UTIL } from './lib/shader.js';
import { patchFingerprints } from './lib/indoor.js';

const m = new THREE.MeshStandardMaterial({ roughness: 0.08 });
patchFingerprints(m, { amount: 0.6, smear: 0.4, seed: 3 });
const p = m.userData.astraPatches.find(
    (q) => q.name === 'indoor:fingerprints');
const fres = fresnelOf(GLSL_UTIL);
// A pane facing +Z, seen from `deg` off its normal — the weight the
// shipped body applies to the whole smudge.
const weight = (deg) => {
  const a = deg * Math.PI / 180;
  const v = [Math.sin(a), 0, Math.cos(a)];
  return 0.06 + 0.94 * fres([0, 0, 1], v, 4.0);
};
console.log(JSON.stringify({
  ladder: [0, 30, 60, 75, 82, 88].map((d) => +weight(d).toFixed(4)),
  body: p.fragmentBody, head: p.fragmentHead,
}));
""", _LIBS)
    ladder = out["ladder"]
    # The numbers only mean anything if the shipped body applies them.
    assert "float fprGrz = astraFresnel(fprN, fprV, 4.0);" in out["body"]
    assert "float fprW = mix(0.06, 1.0, fprGrz);" in out["body"]
    assert "fprW" in out["body"].split("totalEmissiveRadiance")[1]
    for a, b in zip(ladder, ladder[1:], strict=False):
        assert b > a, f"the smudge does not rise with the angle: {ladder}"
    assert ladder[0] < 0.07, ladder
    assert ladder[4] > 8 * ladder[0], ladder
    # The scratches are the grazing-only layer, gated a second time.
    assert "fprScr * fprGrz" in out["body"], out["body"]
    # And there is a light to catch, or a smudge has nothing to show:
    # both direct kinds plus the sky, which is what a shopfront reflects.
    for token in ("directionalLights[i].color", "pointLights[i].color",
                  "hemisphereLights[i].skyColor"):
        assert token in out["body"], token


def test_the_grease_is_a_film_and_not_a_set_of_stripes():
    """THE PORT'S FOURTH LAW.  Arcs alone are a few bright bands on clean
    glass, and the eye reads bands as paint.  The film rides the same
    blotch that decides WHERE hands have been — so a wiped pane stays
    wiped — at about half the arcs' weight, with its own slow variation
    so it is cloud and not a wash."""
    out = measure("""
import * as THREE from 'three';
import { patchFingerprints } from './lib/indoor.js';

const m = new THREE.MeshStandardMaterial();
patchFingerprints(m, { amount: 0.6, smear: 0.4, seed: 3 });
const body = m.userData.astraPatches.find(
    (q) => q.name === 'indoor:fingerprints').fragmentBody;
const w = /fprFilm\\s*\\*\\s*(0\\.\\d+)/.exec(body);
console.log(JSON.stringify({ body, filmWeight: w ? parseFloat(w[1]) : null }));
""", _LIBS)
    body = out["body"]
    # It is gated on fprWhere — the same blotch as the arcs — so amount
    # still means "how much of the surface hands have reached".
    assert "float fprFilm = fprWhere * (" in body, body
    assert "astraNoise2(fprQ" in body.split("fprFilm")[1], body
    assert "+ fprFilm * " in body, body
    assert 0.3 <= out["filmWeight"] <= 0.75, out["filmWeight"]
    # and it is inside the mask, so the fresnel weight still hides it
    # head-on: a film that ignored the angle would be a frosted pane.
    mask = body.split("float fprMask")[1].split("\n")[0:3]
    assert any("fprFilm" in ln for ln in mask), mask


def test_the_crowd_is_one_draw_call_seated_on_the_ground():
    """A crowd is only affordable as one draw call, and it is only a crowd
    if its people stand ON the ground with their own heights.  The stated
    bounding SPHERE is the other half: `position` is all zeros (the GTAO
    reason `instancedQuad` documents), so without a real radius three
    culls the whole square the moment the origin leaves frame — and with
    the 10 km default it frames 10 km of empty air."""
    out = measure("""
import { makeCrowdImposters } from './lib/indoor.js';

const heightAt = (x, z) => 1.4 + 0.25 * Math.sin(x * 0.21)
    + 0.18 * Math.cos(z * 0.17);
const g = makeCrowdImposters({ count: 64, extent: 30, height: 1.72,
                               heightAt, seed: 4 });
const meshes = [];
g.traverse((o) => { if (o.isMesh) meshes.push(o); });
const geo = meshes[0].geometry;
const pos = geo.attributes.aPos.array;
const card = geo.attributes.aCrowd.array;
let offGround = 0, outside = 0;
const scales = [], tones = [];
for (let i = 0; i < 64; i++) {
  const x = pos[i * 3], y = pos[i * 3 + 1], z = pos[i * 3 + 2];
  if (Math.abs(y - heightAt(x, z)) > 1e-6) offGround++;
  if (Math.abs(x) > 15 || Math.abs(z) > 15) outside++;
  scales.push(card[i * 4 + 1]);
  tones.push(card[i * 4]);
}
console.log(JSON.stringify({
  meshes: meshes.length,
  vertex: meshes[0].material.vertexShader,
  instanced: !!geo.isInstancedBufferGeometry,
  count: geo.instanceCount,
  zeroPos: geo.attributes.position.array.every((v) => v === 0),
  radius: geo.boundingSphere.radius,
  lowY: geo.boundingBox.min.y, highY: geo.boundingBox.max.y,
  offGround, outside,
  scaleRange: [Math.min(...scales), Math.max(...scales)],
  toneRange: [Math.min(...tones), Math.max(...tones)],
  name: g.name, tick: typeof g.userData.tick,
  ticked: g.userData.tick(3.5),
}));
""", _LIBS)
    assert out["meshes"] == 1, out["meshes"]
    assert out["instanced"] and out["count"] == 64, out
    # The GTAO trap: a real quad left in `position` is drawn at the world
    # origin by the override pass and burns a hole there.
    assert out["zeroPos"], "the cards keep a real quad in position"
    assert out["offGround"] == 0, f"{out['offGround']} people float"
    assert out["outside"] == 0, out["outside"]
    # A REAL radius, not the 10 km default and not a sphere so small three
    # culls the square as soon as its centre leaves frame.
    assert 15 < out["radius"] < 40, out["radius"]
    assert out["lowY"] < 1.2 and out["highY"] > 2.9, out
    # Varied heights and clothing, or a crowd is wallpaper: one in seven
    # is a child, so the range has to reach well under an adult.
    lo, hi = out["scaleRange"]
    assert lo < 0.8 and hi > 1.0, out["scaleRange"]
    assert out["toneRange"][1] - out["toneRange"][0] > 0.8, out
    assert out["name"] == "Crowd" and out["tick"] == "function", out
    assert out["ticked"] == 1, out["ticked"]
    # Facing the camera, in YAW only: a card that also pitches lifts the
    # whole crowd off the ground together as the camera looks down.
    vs = out["vertex"]
    assert "modelViewMatrix[0][0], modelViewMatrix[1][0]" in vs, vs
    assert "camR - up * dot(camR, up)" in vs, vs
    assert "vec3 up = vec3(0.0, 1.0, 0.0);" in vs, vs


def test_a_backlit_card_is_a_silhouette_with_a_lit_edge():
    """THE PORT'S FIFTH LAW.  With the sun behind it, a hard `max(dot, 0)`
    on this card's coarse bowed normal prints the whole crowd as one flat
    ambient tone — the frame our host rendered before this, where 90
    people were 90 identical blue-grey stumps.  Three things fix it and
    all three are in the shipped shader: a WRAP so the terminator is not a
    cliff, a RIM in the light's own colour on the sun-side edge of the
    silhouette (gated on the sun being BEHIND, or a front-lit crowd grows
    a halo), and per-person VALUE, because a street is pale coats and dark
    ones and hue alone is not a crowd."""
    out = measure("""
import { makeCrowdImposters } from './lib/indoor.js';

const g = makeCrowdImposters({ count: 8, extent: 10, seed: 3 });
const mat = g.children[0].material;
console.log(JSON.stringify({
  vs: mat.vertexShader, fs: mat.fragmentShader,
  leg: mat.uniforms.uCrwLeg.value.toArray().map((v) => +v.toFixed(4)),
}));
""", _LIBS)
    vs, fs = out["vs"], out["fs"]
    # The card has no depth for a normal to find the rim in, so the
    # vertex stage hands the fragment which way the light lies ACROSS it
    # and how far behind.
    assert "vCrwRim = vec2(dot(right, uCrwSun), clamp(-dot(fwd, uCrwSun)" in vs
    assert "uniform vec3 uCrwSun;" in vs, vs
    # Wrapped, not clamped.
    assert "float iNdL = clamp((dot(normalize(vCrwN), uCrwSun) + 0.30)" in fs
    assert "max(dot(normalize(vCrwN), uCrwSun), 0.0)" not in fs, fs
    # The rim rides the real silhouette (cW is the body half-width), is
    # gated on the sun side and on backlight, and is the LIGHT's colour.
    rim = fs.split("float iRim")[1].split(";")[0]
    assert "abs(iq.x) / max(cW, 0.02)" in rim, rim
    assert "vCrwRim.x * sign(iq.x)" in rim and "vCrwRim.y" in rim, rim
    assert "uCrwSunColor * (iRim *" in fs, fs
    # Value per person, on the clothing ramp.
    assert "iAlb *= 0.72 + 0.62 * fract(cPh * 0.29);" in fs, fs
    # Trousers, hair and shoes stay above the albedo floor: linear 0.02 is
    # about where a surface stops being a colour and becomes a hole.
    r, gr, b = out["leg"]
    assert min(r, gr, b) > 0.02, out["leg"]
    assert max(r, gr, b) < 0.10, out["leg"]


def test_the_crowd_is_kept_out_of_the_override_passes():
    """GTAOPass redraws the scene with an OPAQUE override material, in
    which a transparent card is a solid wall — measured elsewhere at
    50/255 of false darkening on ground the effect never touched.  The
    same guard stops the mesh casting a shadow, which is right: a card
    that always faces the camera would cast a shadow that turns with
    it."""
    out = measure("""
import { makeCrowdImposters } from './lib/indoor.js';

const rows = [];
makeCrowdImposters({ count: 12, extent: 10, seed: 4 }).traverse((o) => {
  if (!o.isMesh) return;
  rows.push({ name: o.name,
              guarded: o.userData.astraNoOverride === true,
              shadow: !!o.castShadow,
              transparent: !!o.material.transparent,
              depthWrite: !!o.material.depthWrite });
});
console.log(JSON.stringify({ rows }));
""", _LIBS)
    rows = out["rows"]
    assert len(rows) == 1, rows
    for r in rows:
        assert r["transparent"], r
        assert r["guarded"], f"{r['name']} would be a solid wall to GTAO"
        assert not r["shadow"], f"{r['name']} casts a shadow"
        # Still writes depth: a card that does not punches a
        # sky-coloured hole through the one behind it.
        assert r["depthWrite"], r


def test_every_option_is_a_uniform_and_not_baked_into_the_glsl():
    """The first material to compile a cache key decides the GLSL every
    material with that key gets.  So two rooms tuned differently must
    differ only in UNIFORM values — otherwise the second room silently
    renders with the first one's sources, and nothing in the frame says
    so."""
    out = measure("""
import * as THREE from 'three';
import { patchBounceLight, patchFingerprints } from './lib/indoor.js';

const make = (opts, fpr) => {
  const m = new THREE.MeshStandardMaterial();
  patchBounceLight(m, opts);
  patchFingerprints(m, fpr);
  const b = m.userData.astraPatches.find((p) => p.name === 'indoor:bounce');
  const f = m.userData.astraPatches.find(
      (p) => p.name === 'indoor:fingerprints');
  return {
    key: m.customProgramCacheKey(),
    glsl: [b.fragmentHead, b.fragmentBody, b.vertexHead,
           f.fragmentHead, f.fragmentBody].join('|'),
    bounce: [m.userData.uniforms.uBncGain.value,
             m.userData.uniforms.uBncCount.value,
             m.userData.uniforms.uBncNrm.value[0].toArray()],
    smudge: [m.userData.uniforms.uFprAmt.value,
             m.userData.uniforms.uFprSmear.value,
             m.userData.uniforms.uFprSeed.value.toArray()],
  };
};
const a = make({ sources: [[0, 0, 0]], strength: 1 },
               { amount: 0.3, smear: 0.2, seed: 1 });
const b = make({ sources: [[4, 1, 0], [0, 2, 0]], strength: 2.5,
                 up: [0, 0, 1] },
               { amount: 0.9, smear: 0.8, seed: 7 });
console.log(JSON.stringify({ a, b, same: a.glsl === b.glsl }));
""", _LIBS)
    a, b = out["a"], out["b"]
    assert out["same"], "an option was baked into the GLSL"
    assert a["key"] == b["key"], (a["key"], b["key"])
    assert "indoor:base" in a["key"] and "indoor:bounce" in a["key"]
    assert "indoor:fingerprints" in a["key"], a["key"]
    assert a["bounce"] != b["bounce"], a["bounce"]
    assert b["bounce"][2] == [0, 0, 1], b["bounce"]
    assert a["smudge"] != b["smudge"], a["smudge"]


def test_the_same_seed_builds_the_same_crowd_twice():
    """No Math.random anywhere: two runs of one brief must be the same
    frame, and a different seed must actually move the crowd rather than
    renaming it."""
    assert "Math.random" not in _SRC.read_text(encoding="utf-8")
    out = measure("""
import * as THREE from 'three';
import { makeCrowdImposters, patchFingerprints } from './lib/indoor.js';

const field = (seed) => {
  const g = makeCrowdImposters({ count: 30, extent: 20, seed,
                                 heightAt: (x, z) => 0 });
  const a = g.children[0].geometry.attributes;
  return { pos: Array.from(a.aPos.array),
           card: Array.from(a.aCrowd.array) };
};
const seedOf = (s) => {
  const m = new THREE.MeshStandardMaterial();
  patchFingerprints(m, { seed: s });
  return m.userData.uniforms.uFprSeed.value.toArray();
};
const a = field(4), b = field(4), c = field(5);
console.log(JSON.stringify({
  same: a.pos.every((v, i) => v === b.pos[i])
      && a.card.every((v, i) => v === b.card[i]),
  moved: a.pos.some((v, i) => v !== c.pos[i]),
  smudge: [seedOf(1), seedOf(1), seedOf(2)],
}));
""", _LIBS)
    assert out["same"], "the same seed built two different crowds"
    assert out["moved"], "a new seed built the same crowd"
    s = out["smudge"]
    assert s[0] == s[1] and s[0] != s[2], s


def test_both_patches_chain_with_another_library_on_one_material():
    """GLSL has one global namespace and `patchStandard` THROWS when two
    patches define one function name with different bodies — a room's wall
    wears wear, bounce and grease at once, so the prefixes are not
    decoration.  The chain must also run in CALL order and share one
    uniform map."""
    out = measure("""
import * as THREE from 'three';
import { patchBounceLight, patchFingerprints } from './lib/indoor.js';
import { patchEdgeWear, patchMicroBreakup } from './lib/surface_wear.js';

const m = new THREE.MeshStandardMaterial({ roughness: 0.3 });
patchMicroBreakup(m, { scale: 0.6, strength: 0.12, seed: 5 });
patchBounceLight(m, { sources: [[0, 1, 0]] });
patchEdgeWear(m, { strength: 0.4, seed: 6 });
patchFingerprints(m, { amount: 0.5, seed: 3 });
const heads = m.userData.astraPatches
    .map((p) => p.fragmentHead || '').join('\\n');
const fns = [...heads.matchAll(/^\\s*(?:float|vec2|vec3)\\s+(\\w+)\\s*\\(/gm)]
    .map((x) => x[1]);
console.log(JSON.stringify({
  names: m.userData.astraPatches.map((p) => p.name),
  key: m.customProgramCacheKey(),
  fns, dupes: fns.filter((f, i) => fns.indexOf(f) !== i),
  uniforms: Object.keys(m.userData.uniforms).sort(),
  roughness: m.roughness,
  factors: Object.keys(m.userData.astraRoughness.factors).sort(),
}));
""", _LIBS)
    assert out["names"] == [
        "wear:base", "wear:micro", "indoor:base", "indoor:bounce",
        "wear:edge", "indoor:fingerprints"], out["names"]
    assert out["dupes"] == [], out["dupes"]
    # Every helper this library adds is prefixed to its own patch.
    for fn in ("astraBncFall", "astraFprField", "astraFprArc",
               "astraFprPlane", "astraFprLobe"):
        assert fn in out["fns"], out["fns"]
    for u in ("uBncCol", "uBncCount", "uBncGain", "uBncNrm", "uBncPos",
              "uBncSpan", "uFprAmt", "uFprSeed", "uFprSmear"):
        assert u in out["uniforms"], out["uniforms"]
    # Three libraries, three roughness factors, one composed number.
    assert out["factors"] == ["edge", "indoor:fingerprints", "micro"], out
    assert 0.04 <= out["roughness"] <= 1, out["roughness"]
