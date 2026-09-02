"""surface_wear.js: the two patches that land on EVERY material.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_surface_wear_lib.py).  Its claims are kept as they stood — one
shared world base, no name a neighbour already owns, every option a uniform
rather than baked GLSL, one seed one surface, a hard bound on the albedo
swing, a SIGNED curvature in 1/m for the edge route, and a gloss that
composes on the material because per-pixel roughness is unreachable from
<color_fragment>.

THE PORT'S OWN LAWS, and the two regressions this file exists to stop.

1. A HUE JITTER MAY NOT BE A ROTATION ABOUT THE GREY AXIS.  The reference
   shifted hue with `astraHueShift`, and that operator returns a neutral
   EXACTLY unchanged (c*cos + cross(k,c)*sin + k*dot(k,c)*(1-cos) collapses
   to c when c is parallel to k).  Measured on the albedos this patch is
   sold for: 0.0 change on a grey, 0.0014 peak channel move on our stone
   0x8d8577 at the reference default of 0.03 rad — so the one line meant to
   make the breakup "a change of MATERIAL rather than a brightness dial" did
   nothing at all on stone, plaster, concrete and dirt.  It is a warm/cool
   break now (the transfer `astraHueBreak` already uses), and `hue` is a
   fraction of the albedo.  Measured on the showcase host
   (fx/out/surface_wear, stone drum face, close view): warm/cool spread
   std((R-B)/(R+G+B)) 0.0132 -> 0.0189, +43%, with the drum's mean luminance
   unmoved (0.480 -> 0.477).

2. EDGE WEAR MAY NOT DEGENERATE INTO A BARBER POLE.  Past ~3x the onset
   curvature the ramp is saturated, so on anything much thinner than
   `width` the ONLY structure left is the break-up field — and the
   reference's was one fixed 14 cm blotch field regardless of the feature.
   On a 6 cm painted rail post that is alternating solid bands of flat bone
   white (measured: post region saturation std 0.219, and the same white
   0xe8e2d6 on every material wearing the patch).  The break-up is two
   octaves now, both scaled to the `width` asked for, and the worn tone is
   the object's OWN colour rubbed thin before it is bare substrate, with the
   substrate itself broken in value and warm/cool.  Measured: the worn ring
   kept its hue (saturation 0.257 -> 0.418 against an unworn control's
   0.538) and stopped washing out (luminance 0.496 -> 0.397, p99 0.861 ->
   0.807).

3. A RAIL MAY NOT BE POLISHED INTO A SKY MIRROR.  Same trap roadway.js
   found: `fragmentBody` lands before <roughnessmap_fragment>, so the gloss
   move is the MATERIAL's and must therefore be area-weighted.  Wear covers
   a fraction of a surface; the reference spent 0.35 * strength of polish on
   all of it.  0.12 here.
"""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from tests.scene_runtime.lib._probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "terrain_shade.js", "waterside.js", "surface_wear.js")

_LIB_SRC = (LIB_DIR / "surface_wear.js").read_text(encoding="utf-8")

# The patch only exists inside onBeforeCompile, so every probe hands it the
# two chunks patchStandard replaces and reads back what it wrote.
_PRELUDE = """
import * as THREE from 'three';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
import { patchShoreWet, patchShoreFoam } from './lib/waterside.js';

const std = (o = {}) => new THREE.MeshStandardMaterial(
    Object.assign({ color: 0xb2603a, roughness: 0.6 }, o));

function compile(mat) {
  const shader = {
    vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
    fragmentShader: 'void main() {\\n#include <color_fragment>\\n}',
    uniforms: {},
  };
  mat.onBeforeCompile(shader);
  return shader;
}

const count = (src, needle) => src.split(needle).length - 1;
"""

# A few primitives plus one instanced set, and ONE material carrying
# patchTriplanar as well: the plate is where the terrain chain and the wear
# chain meet, which is the combination that has to compile.
_SCENE = """
import * as THREE from 'three';
import { patchTriplanar } from './lib/terrain_shade.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';

export function createScene() {
  const scene = new THREE.Scene();
  const g = new THREE.Group();

  const stone = new THREE.MeshStandardMaterial({
    color: 0x8d8577, roughness: 0.9,
  });
  patchTriplanar(stone, { scale: 2.5 });
  const plate = new THREE.Mesh(new THREE.BoxGeometry(6, 0.3, 6), stone);
  plate.position.y = 0.15;
  g.add(plate);

  const paint = new THREE.MeshStandardMaterial({
    color: 0xb2603a, roughness: 0.62,
  });
  // Flat shading on the box: the patches must not lean on vNormal, which
  // three omits when FLAT_SHADED.
  const wall = new THREE.MeshStandardMaterial({
    color: 0xcdc4b2, roughness: 0.8, flatShading: true,
  });

  const put = (mesh, x, z, y) => {
    mesh.position.set(x, 0.3 + y, z);
    g.add(mesh);
    return mesh;
  };
  put(new THREE.Mesh(new THREE.SphereGeometry(0.45, 48, 32), paint),
      -1.5, -1.4, 0.45);
  put(new THREE.Mesh(new THREE.TorusKnotGeometry(0.26, 0.075, 96, 20), paint),
      0.2, 0.1, 0.36);
  put(new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.9, 0.9), wall),
      -1.6, 1.5, 0.45);

  // The instanced set: the only place USE_INSTANCING is ever compiled.
  const pebble = new THREE.InstancedMesh(
      new THREE.IcosahedronGeometry(0.17, 3), wall, 24);
  const m = new THREE.Matrix4();
  let s = 7;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 24; i++) {
    m.compose(
        new THREE.Vector3((rand() * 2 - 1) * 2.6, 0.42,
                          (rand() * 2 - 1) * 2.6),
        new THREE.Quaternion().setFromEuler(
            new THREE.Euler(rand(), rand() * 3, rand())),
        new THREE.Vector3(1, 0.8 + rand() * 0.5, 1));
    pebble.setMatrixAt(i, m);
  }
  pebble.instanceMatrix.needsUpdate = true;
  pebble.frustumCulled = false;
  g.add(pebble);

  for (const [mat, seed] of [[stone, 3], [paint, 11], [wall, 23]]) {
    patchMicroBreakup(mat, { seed });
    patchEdgeWear(mat, { seed });
  }
  scene.add(g);
  // A camera is not decoration here: the host reports `ok: false` with an
  // EMPTY error when `cameras` is empty, so a scene with none fails the
  // compile preflight before a program is built.
  return {
    scene,
    cameras: [{ name: 'hero', position: [12, 5, 14], lookAt: [0, 2, 0],
                fov: 45 }],
    update() {},
  };
}
"""


def _probe(body: str) -> dict:
    return measure(_PRELUDE + body, _LIBS)


def _find(pattern: str, src: str) -> re.Match:
    m = re.search(pattern, src)
    assert m, f"{pattern} not in\n{src}"
    return m


def _main_body(src: str) -> str:
    return src[src.index("void main"):]


def _unguarded(src: str) -> str:
    """Drop `#ifndef X ... #endif` blocks, which may repeat verbatim.

    terrain_shade ships astraFbmUnit from two patches behind one guard, so
    the TEXT carries it twice and the preprocessor keeps one.  Only an
    unguarded repeat is a redefinition.
    """
    return re.sub(r"#ifndef\b.*?#endif", "", src, flags=re.S)


def test_both_patches_and_a_triplanar_compile_on_one_material():
    """The stack this library exists to join: a triplanar terrain patch and
    both wear patches on ONE material, in a scene that also puts a wear
    material on an InstancedMesh.  Only a real instanced program ever
    compiles the USE_INSTANCING branch of the world base, and only a real
    compile proves the two libraries' shared varyings, helpers and locals
    survive being concatenated into one shader."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] and report["errors"] == [], report
    # Three patched built-ins, and more programs than that: the pebble
    # material compiled twice, once per instancing state.
    assert report["compile"]["custom_materials"] >= 3, report
    assert report["compile"]["programs"] >= 4, report
    assert report["compile"]["gpu"], report


def test_the_two_patches_chain_without_losing_each_other():
    """Both patches on one material is the ordinary case — a surface that
    varies but has no worn edges is half the idea.  patchStandard chains
    them, but it repeats whatever it is handed: the shared world base and
    its two helpers must be emitted ONCE (a second function body is a
    compile error), and re-applying a patch has to retune its uniforms
    instead of injecting a second copy of its code."""
    out = _probe("""
const m = std();
patchMicroBreakup(m, { strength: 0.08 });
patchEdgeWear(m, { strength: 0.5 });
patchMicroBreakup(m, { strength: 0.12 });
const s = compile(m);
const microOnly = std();
patchMicroBreakup(microOnly);
console.log(JSON.stringify({
  key: m.customProgramCacheKey(), microKey: microOnly.customProgramCacheKey(),
  micro: s.fragmentShader.includes('mbV * uMicroAmt'),
  edge: s.fragmentShader.includes('ewK * uEdgeAmt'),
  amt: m.userData.uniforms.uMicroAmt.value,
  edgeAmt: m.userData.uniforms.uEdgeAmt.value,
  worldBody: count(s.vertexShader, 'vec4 wrP ='),
  axesFn: count(s.fragmentShader, 'vec3 astraWearAxes(vec3 n) {'),
  noiseFn: count(s.fragmentShader, 'float astraWearNoise(vec3 p, vec3 w) {'),
  mixLine: count(s.fragmentShader, 'float mbV ='),
  varyVs: count(s.vertexShader, 'varying vec3 vAstraWorld;'),
  varyFs: count(s.fragmentShader, 'varying vec3 vAstraWorldN;'),
}));
""")
    assert out["micro"] and out["edge"], "one patch lost the other"
    assert out["worldBody"] == 1 and out["axesFn"] == 1 and out["noiseFn"] == 1
    assert out["varyVs"] == 1 and out["varyFs"] == 1
    # Every option is a uniform, so re-applying retunes in place.
    assert out["mixLine"] == 1 and out["amt"] == 0.12 and out["edgeAmt"] == 0.5
    # The base runs FIRST or both bodies read a varying nobody wrote, and a
    # longer chain must not collide with a shorter one's program.
    assert out["key"] == "astra:wear:base+wear:micro+wear:edge", out["key"]
    assert out["microKey"] == "astra:wear:base+wear:micro"


def test_neither_patch_declares_a_name_its_neighbours_own():
    """The silent failure this library is most exposed to.  patchStandard
    DROPS a repeated uniform or varying declaration and keeps the first, so
    a name shared with terrain_shade or waterside would leave one patch
    reading the other's value with nothing reported; a repeated local inside
    main is a compile error instead.  So: the uniform sets must be disjoint
    (uTime excepted, which is the shared clock), and a material wearing all
    six patches must declare every local, uniform and helper exactly once."""
    out = _probe("""
const wear = std();
patchMicroBreakup(wear);
patchEdgeWear(wear);
const nbr = std();
patchTriplanar(nbr);
patchSlopeSplat(nbr, { snowLine: 30 });
patchShoreWet(nbr);
patchShoreFoam(nbr);
const all = std();
patchTriplanar(all);
patchSlopeSplat(all, { snowLine: 30 });
patchShoreWet(all);
patchShoreFoam(all);
patchMicroBreakup(all);
patchEdgeWear(all);
const s = compile(all);
console.log(JSON.stringify({
  wearU: Object.keys(wear.userData.uniforms),
  nbrU: Object.keys(nbr.userData.uniforms),
  vs: s.vertexShader, fs: s.fragmentShader,
}));
""")
    shared = set(out["wearU"]) & set(out["nbrU"])
    assert shared == {"uTime"}, shared
    for stage in ("vs", "fs"):
        src = out[stage]
        decls = re.findall(
            r"^\s*(?:uniform|varying)\s+(?:lowp |mediump |highp )?"
            r"[a-z0-9]+\s+(\w+)\s*;", src, re.M)
        dupes = [n for n, c in Counter(decls).items() if c > 1]
        assert not dupes, f"{stage} declares {dupes} twice"
        fns = re.findall(r"^(?:float|vec[234]|int)\s+(\w+)\s*\(",
                         _unguarded(src), re.M)
        assert not [n for n, c in Counter(fns).items() if c > 1]
        locals_ = re.findall(
            r"^\s*(?:float|vec[234]|int|mat[234])\s+(\w+)\s*=",
            _main_body(src), re.M)
        clash = [n for n, c in Counter(locals_).items() if c > 1]
        assert not clash, f"{stage} main() declares {clash} twice"
    # Named as the neighbours name them on purpose: ONE world position is
    # written for the whole chain, not one per library.
    assert "vAstraWorld" in out["vs"] and "vAstraWorld" in out["fs"]


def test_an_option_is_a_uniform_and_never_baked_into_the_source():
    """three caches programs by key, and the FIRST material to compile a key
    decides the GLSL every material sharing it gets.  Two materials that
    differ only in options must therefore compile to the same source, or the
    second one silently wears the first one's settings.  Same key,
    byte-identical GLSL, different uniform values."""
    out = _probe("""
const a = std();
patchMicroBreakup(a, { scale: 0.6, strength: 0.1, hue: 0.15, seed: 1 });
patchEdgeWear(a, { strength: 0.35, width: 0.35, seed: 1 });
const b = std();
patchMicroBreakup(b, { scale: 9, strength: 0.02, hue: 0.4, seed: 99 });
patchEdgeWear(b, {
  strength: 0.9, width: 0.05, seed: 99, color: new THREE.Color(0x112233),
});
const sa = compile(a), sb = compile(b);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  sameKey: a.customProgramCacheKey() === b.customProgramCacheKey(),
  sameVs: sa.vertexShader === sb.vertexShader,
  sameFs: sa.fragmentShader === sb.fragmentShader,
  aScale: u(a).uMicroScale.value, bScale: u(b).uMicroScale.value,
  aWidth: u(a).uEdgeWidth.value, bWidth: u(b).uEdgeWidth.value,
  aAmt: u(a).uEdgeAmt.value, bAmt: u(b).uEdgeAmt.value,
  aHue: u(a).uMicroHue.value, bHue: u(b).uMicroHue.value,
  aColor: u(a).uEdgeColor.value.getHex(),
  bColor: u(b).uEdgeColor.value.getHex(),
}));
""")
    assert out["sameKey"] and out["sameVs"] and out["sameFs"]
    assert out["aScale"] == 0.6 and out["bScale"] == 9
    assert out["aWidth"] == 0.35 and out["bWidth"] == 0.05
    assert out["aAmt"] == 0.35 and out["bAmt"] == 0.9
    assert out["aHue"] == 0.15 and out["bHue"] == 0.4
    assert out["bColor"] == 0x112233


def test_the_bare_substrate_default_sits_inside_the_sane_albedo_range():
    """PORT LAW 3 of the aesthetic brief: a non-emissive albedo belongs in
    0.02..0.8 linear.  The reference's bone white 0xe8e2d6 is 0.816 linear
    on its red channel, and on our host — ACES in the fragment tail, no post
    chain, a bright baked environment — it read as white primer wherever the
    wear saturated.  A warmer, slightly darker bone leaves room for the
    per-patch value break (x0.85..1.15) to stay under the top."""
    out = _probe("""
const m = std();
patchEdgeWear(m);
const c = m.userData.uniforms.uEdgeColor.value;
console.log(JSON.stringify({ hex: c.getHex(), rgb: [c.r, c.g, c.b] }));
""")
    assert out["hex"] == 0xd2cabb, "a warm bone, not paper white"
    # Linear working space, and the patch's own breaks ride on top: the
    # value one peaks at 1.10 and the warm/cool one at 1.12.
    assert max(out["rgb"]) * 1.10 * 1.12 <= 0.80, out["rgb"]
    assert min(out["rgb"]) >= 0.02


def test_one_seed_places_the_same_wear_every_time():
    """A render is re-run — for a fix round, for a video, for the judge —
    and the surface must not move between takes.  The seed reaches the GPU
    only as a noise-space OFFSET (a uniform, because baking it would hand
    material one's seed to every material sharing the key), so the same seed
    must produce the same offsets and the same roughness detune, and two
    seeds must not land on the same blotches.  The two patches also have to
    be decorrelated from each other, or every worn edge would sit on the
    same blob as a light patch."""
    out = _probe("""
const mk = (seed) => {
  const m = std({ roughness: 0.7 });
  patchMicroBreakup(m, { seed });
  patchEdgeWear(m, { seed });
  return m;
};
const a = mk(7), b = mk(7), c = mk(8);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  aMicro: u(a).uMicroSeed.value.toArray(),
  bMicro: u(b).uMicroSeed.value.toArray(),
  cMicro: u(c).uMicroSeed.value.toArray(),
  aEdge: u(a).uEdgeSeed.value.toArray(),
  cEdge: u(c).uEdgeSeed.value.toArray(),
  aRough: a.roughness, bRough: b.roughness, cRough: c.roughness,
  sameSrc: compile(a).fragmentShader === compile(c).fragmentShader,
}));
""")
    assert out["aMicro"] == out["bMicro"] and out["aRough"] == out["bRough"]
    assert out["aMicro"] != out["cMicro"] and out["aEdge"] != out["cEdge"]
    assert out["aRough"] != out["cRough"], "the detune ignores the seed"
    assert out["aMicro"] != out["aEdge"], "wear rides the breakup's blobs"
    assert all(0 <= v <= 64 for v in out["aMicro"] + out["aEdge"])
    # Same GLSL for both seeds: the seed is a uniform, not source.
    assert out["sameSrc"]
    assert "Math.random" not in _LIB_SRC


def test_the_breakup_stays_under_the_strength_it_was_given():
    """Subtlety is the feature: this rides on every material in the scene,
    so an unbounded swing would be scene-wide dirt.  The noise is centred,
    stretched to reach the strength asked for, then CLAMPED before it is
    scaled — so `strength` is a hard bound on the albedo swing however the
    octaves stack.  Each octave also fades out once a pixel spans it, which
    is what keeps a receding surface from turning into static."""
    out = _probe("""
const m = std();
patchMicroBreakup(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    mix = _find(r"float (\w+) = clamp\(\(mbLo - 0\.5\) \* [\d.]+\s*"
                r"\+ \(mbMi - 0\.5\) \* [\d.]+ \* (\w+)\s*"
                r"\+ \(mbHi - 0\.5\) \* [\d.]+ \* (\w+), -1\.0, 1\.0\);", fs)
    _find(rf"diffuseColor\.rgb \*= 1\.0 \+ {mix.group(1)} \* uMicroAmt;", fs)
    # One fade per octave, measured in cycles per pixel, not in metres.
    fw = _find(r"float (\w+) = length\(fwidth\(mbP\)\);", fs).group(1)
    for var, step in ((mix.group(2), "2.03"), (mix.group(3), "4.1")):
        _find(rf"float {var} = 1\.0 - smoothstep\([\d.]+, [\d.]+,"
              rf" {fw} \* {step}\);", fs)
        _find(rf"float mb(?:Mi|Hi) = astraWearNoise\(mbP \* {step}, mbW\);",
              fs)


def test_the_breakup_varies_colour_and_not_only_brightness():
    """PORT LAW 1.  `astraHueShift` is a rotation about the grey axis and
    therefore the identity on a neutral — 0.0 change on a grey, 0.0014 peak
    channel move on the stone albedo 0x8d8577 at the reference's 0.03 rad —
    and stone, plaster, concrete and dirt are most of what this patch is
    ever asked to break up.  The warm/cool transfer that replaces it moves
    R up and B down (and G a little, as `astraHueBreak` does), riding the
    DIFFERENCE of two octaves so the colour swing is not the brightness
    field wearing a second label.  A dust term takes saturation with it on
    the dark half, so the patch changes MATERIAL rather than exposure."""
    out = _probe("""
const m = std();
patchMicroBreakup(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    tint = _find(r"float (\w+) = clamp\(\(mbLo - mbHi\) \* [\d.]+, "
                 r"-1\.0, 1\.0\) \* uMicroHue;", fs).group(1)
    _find(rf"diffuseColor\.rgb \*= vec3\(1\.0 \+ {tint}, "
          rf"1\.0 \+ {tint} \* [\d.]+,\s*1\.0 - {tint}\);", fs)
    # saturation rides the same field, bounded by strength
    lum = _find(r"float (\w+) = dot\(diffuseColor\.rgb, "
                r"vec3\(0\.2126, 0\.7152, 0\.0722\)\);", fs).group(1)
    _find(rf"diffuseColor\.rgb = mix\(diffuseColor\.rgb, vec3\({lum}\),\s*"
          r"max\(-mbV, 0\.0\) \* uMicroAmt \* [\d.]+\);", fs)
    # GLSL_UTIL still DEFINES the rotation for whoever wants it; what this
    # patch may not do is call it, because on a neutral it is the identity.
    assert "astraHueShift(" not in _main_body(fs)


def test_edge_wear_reads_signed_curvature_in_metres_not_screen_pixels():
    """The whole edge estimate.  The normal's screen derivative is divided
    by the world length of the same pixel step, which makes it curvature in
    1/m: the same edge wears the same amount at any distance, where a raw
    fwidth(normal) would draw a line that grew as the camera pulled back.
    And the ratio is SIGNED — the ramp starts at a positive curvature, so a
    concave corner (negative) gets nothing, which is right: dirt collects
    there, wear does not."""
    out = _probe("""
const m = std();
patchEdgeWear(m, { width: 0.35 });
console.log(JSON.stringify({
  fs: compile(m).fragmentShader,
  width: m.userData.uniforms.uEdgeWidth.value,
}));
""")
    fs = out["fs"]
    curv = _find(r"float (\w+) = \(dot\(ewNx, ewPx\) \+ dot\(ewNy, ewPy\)\)\s*"
                 r"/ max\((\w+), 1e-12\);", fs)
    _find(rf"float {curv.group(2)} = dot\(ewPx, ewPx\) \+ dot\(ewPy, ewPy\);",
          fs)
    _find(r"vec3 ewPx = dFdx\(vAstraWorld\);", fs)
    _find(r"vec3 ewNx = dFdx\(ewN\);", fs)
    # Onset at 1/width and full at three times that: a positive-only ramp,
    # so concave curvature never leaves zero.
    radius = _find(r"float (\w+) = 1\.0 / uEdgeWidth;", fs)
    _find(rf"float (\w+) = smoothstep\({radius.group(1)},"
          rf" {radius.group(1)} \* 3\.0, {curv.group(1)}\);", fs)
    assert out["width"] == 0.35


def test_the_break_up_is_two_octaves_scaled_to_the_width_asked_for():
    """PORT LAW 2, the barber pole.  The curvature ramp saturates at 3x the
    onset, so on anything much thinner than `width` the break-up field is
    the only structure the patch has left — and one fixed-frequency blotch
    field on a 6 cm rail post is alternating solid BANDS, a painted pattern
    rather than wear.  Both octaves are tied to 1/width, so the pattern
    scales with the feature the author declared instead of with the world;
    and the fine one fades out once a pixel spans it, or a receding rail
    turns into static.

    The frequency may NOT be taken from the measured curvature, tempting as
    that is: that estimate is a screen-space average of the two principal
    curvatures, so on a cylinder it changes with the camera and the chips
    would swim across the surface as it orbits."""
    out = _probe("""
const m = std();
patchEdgeWear(m, { width: 0.35 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    ramp = _find(r"float (\w+) = smoothstep\(ewR, ewR \* 3\.0, ewC\);",
                 fs).group(1)
    p = _find(r"vec3 (\w+) = vAstraWorld \* \(ewR \* [\d.]+\) \+ uEdgeSeed;",
              fs).group(1)
    coarse = _find(rf"float (\w+) = astraWearNoise\({p}, ewAx\);", fs).group(1)
    fine = _find(rf"float (\w+) = astraWearNoise\({p} \* ([\d.]+) \+ [\d.]+,"
                 rf" ewAx\);", fs)
    fade = _find(rf"float (\w+) = 1\.0 - smoothstep\([\d.]+, [\d.]+,\s*"
                 rf"length\(fwidth\({p}\)\) \* {fine.group(2)}\);", fs)
    _find(rf"{ramp} \*= smoothstep\([\d.]+, [\d.]+, {coarse} \+ "
          rf"\({fine.group(1)} - 0\.5\) \* {fade.group(1)}\);", fs)
    assert "dFdx(ewC)" not in fs and "* ewC +" not in fs, \
        "a view-dependent frequency makes the chips swim"


def test_the_worn_tone_is_the_object_before_it_is_the_substrate():
    """PORT LAW 2's other half.  The reference mixed straight to ONE flat
    colour, so every material in a scene wearing this patch wore the same
    bone white in the same flat value — the exact evenness the sibling patch
    exists to destroy.  Light wear is the finish rubbed THIN: the surface's
    own colour, lightened and desaturated, which keeps the wear belonging to
    the thing it is on.  Only where the ramp is nearly saturated does it go
    through to the substrate, and the substrate carries its own value break
    and its own warm/cool break so no two worn patches match."""
    out = _probe("""
const m = std();
patchEdgeWear(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    lum = _find(r"float (\w+) = dot\(diffuseColor\.rgb, "
                r"vec3\(0\.2126, 0\.7152, 0\.0722\)\);", fs).group(1)
    thin = _find(rf"vec3 (\w+) = mix\(diffuseColor\.rgb, vec3\({lum}\), "
                 rf"[\d.]+\) \* [\d.]+;", fs).group(1)
    tint = _find(r"float (\w+) = \(ewF - 0\.5\) \* [\d.]+;", fs).group(1)
    bare = _find(r"vec3 (\w+) = uEdgeColor \* \([\d.]+ \+ [\d.]+ \* ewP\)\s*"
                 rf"\* vec3\(1\.0 \+ {tint}, 1\.0 \+ {tint} \* [\d.]+,"
                 rf" 1\.0 - {tint}\);", fs).group(1)
    tone = _find(rf"vec3 (\w+) = mix\({thin}, {bare}, "
                 r"smoothstep\([\d.]+, [\d.]+, ewK\)\);", fs).group(1)
    _find(rf"diffuseColor\.rgb = mix\(diffuseColor\.rgb, {tone},\s*"
          r"clamp\(ewK \* uEdgeAmt, 0\.0, 1\.0\)\);", fs)


def test_gloss_composes_on_the_material_because_per_pixel_is_unreachable():
    """`fragmentBody` lands after <color_fragment> and therefore BEFORE
    <roughnessmap_fragment> declares roughnessFactor, so neither patch can
    touch a per-pixel gloss and both move the material's own roughness
    instead.  Two patches sharing one number is the trap: each owns ONE
    factor over the authored value, so they compose, and re-applying either
    replaces its factor rather than compounding it — a refine round that
    re-runs the same build would otherwise polish the scene to a mirror.

    PORT LAW 3: because the move is the whole material's it has to be
    AREA-WEIGHTED.  Worn edges are a fraction of a surface, and on a
    renderer with no post chain and a bright environment a painted rail
    polished by the worn patch's own value stops reading as paint at all.
    0.12 * strength, not 0.35."""
    out = _probe("""
const m = std({ roughness: 0.8 });
patchMicroBreakup(m, { seed: 4 });
const r1 = m.roughness;
patchEdgeWear(m, { strength: 0.4 });
const r2 = m.roughness;
patchEdgeWear(m, { strength: 0.4 });
const r3 = m.roughness;
patchEdgeWear(m, { strength: 0 });
const r4 = m.roughness;
const basic = new THREE.MeshBasicMaterial({ color: 0x445566 });
patchMicroBreakup(basic);
patchEdgeWear(basic);
console.log(JSON.stringify({
  r1, r2, r3, r4, base: m.userData.astraRoughness.base,
  basicRough: basic.roughness === undefined,
  basicPatched: !!basic.userData.astraPatches.length,
}));
""")
    assert out["base"] == 0.8, "the authored value is the one to compose on"
    # The breakup detunes by up to +/- strength; a scene where every
    # material shares one exact roughness shares one exact highlight.
    assert out["r1"] != 0.8 and abs(out["r1"] - 0.8) <= 0.8 * 0.1 + 1e-9
    assert abs(out["r2"] - out["r1"] * (1 - 0.12 * 0.4)) < 1e-9
    assert out["r3"] == out["r2"], "re-applying compounded the gloss"
    assert abs(out["r4"] - out["r1"]) < 1e-9
    # A material with no roughness at all still takes the albedo patch.
    assert out["basicRough"] and out["basicPatched"]


def test_the_world_base_folds_in_the_instance_transform():
    """`vertexBody` runs after <begin_vertex>, which is before
    <project_vertex>, so `transformed` has not had instanceMatrix applied —
    and this engine builds instanced meshes for most of what it scatters.
    Without the guarded fold, every copy of a scattered rock would take its
    blotches and its wear from the mesh ORIGIN and two hundred pebbles would
    wear one identical pattern.  three declares the attribute itself, so
    declaring it again fails every instanced material."""
    out = _probe("""
const m = std();
patchMicroBreakup(m);
patchEdgeWear(m);
console.log(JSON.stringify({ vs: compile(m).vertexShader }));
""")
    vs = out["vs"]
    body = _main_body(vs)
    assert "#ifdef USE_INSTANCING" in body
    _find(r"wrP = instanceMatrix \* wrP;", body)
    _find(r"wrN = mat3\(instanceMatrix\) \* wrN;", body)
    _find(r"vAstraWorld = \(modelMatrix \* wrP\)\.xyz;", body)
    assert "attribute mat4 instanceMatrix" not in vs
    # fwidth and astraStroke are fragment-only: the util block ships in both
    # stages, so they may be DEFINED here, never called.
    assert vs.count("astraStroke(") == 1
    assert "fwidth(" not in body
    assert "#define ASTRA_FRAG" not in vs
