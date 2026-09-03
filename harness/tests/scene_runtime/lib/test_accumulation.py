"""accumulation.js: one deposit rule, and the chain it lands in.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_accumulation_lib.py).  Their renderer-contract assertions are
dropped; the physics claims, the shared-name contract and the
option-is-a-uniform law are kept, and the grading this port added is pinned
here so a later edit cannot quietly flatten either cover back to one value.

These two patches exist to go ON TOP of everything else — a wall that already
wears `patchTriplanar`, `patchMicroBreakup` and `patchEdgeWear`, a rock that
already wears the aging marks — because a scene reads as weather only when the
SAME stuff is on every surface in it.  `patchStandard` absorbs a duplicate
uniform SILENTLY (the second declaration is dropped and that patch then reads
whatever its neighbour set) while a duplicate main() local is a compile error
and a duplicate helper THROWS, so the shared contract is asserted first: one
world base, no name any neighbour owns, every option a uniform rather than
baked GLSL.

Then the physics each patch claims — deposit along the up vector it was GIVEN,
nothing on a face past the angle snow slides off, a concave lee holding what a
convex edge sheds, sand ripples square to the wind — and finally the whole
five-patch stack on a GPU, which is the only witness that the USE_INSTANCING
branch compiles at all.

THE PORT'S OWN LAW, and the one regression this file exists to stop: the lip
(the shade a cover drops on the material just OUTSIDE its edge) is a band
measured as a FRACTION of the coverage threshold.  The reference took a fixed
0.55 below it, and past a dusting the threshold itself falls under 0.55 — so
zero deposit landed INSIDE the ramp and every bare wall, soffit and boulder
flank in the scene picked up a shade it had no snow to justify (measured on
our host at the shipped 0.05 m depth: 41 184 px of a 1024x576 frame darkened
by up to 21%, with the melt hole printed through the wall behind it).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

import pytest
from _probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "terrain_shade.js", "waterside.js", "surface_wear.js",
         "aging.js", "accumulation.js")

_LIB_SRC = (LIB_DIR / "accumulation.js").read_text(encoding="utf-8")

# The patch only exists inside onBeforeCompile, so every probe hands it
# the two chunks patchStandard replaces and reads back what it wrote.
_PRELUDE = """
import * as THREE from 'three';
import { patchSnow, patchSand } from './lib/accumulation.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
import { patchShoreWet, patchShoreFoam } from './lib/waterside.js';
import { patchDripStains, patchRust, patchDust } from './lib/aging.js';

const std = (o = {}) => new THREE.MeshStandardMaterial(
    Object.assign({ color: 0x8b8478, roughness: 0.7 }, o));

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

# The showcase subject, cut down: a slab, a wall on a footing with a return
# (so there is an INSIDE CORNER), a sill, an asymmetric pitched roof (28 deg
# one side, 62 the other, so the angle snow slides off is IN the fixture), a
# forked branch and two boulders.  The stone material carries ALL FIVE
# patches, and the cobbles are instanced on that same material because a real
# instanced program is the only thing that compiles the USE_INSTANCING branch.
_SCENE = """
import * as THREE from 'three';
import { patchTriplanar } from './lib/terrain_shade.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { patchSnow, patchSand } from './lib/accumulation.js';

export const BOUNDS = { min: [-12, 0, -12], max: [12, 8, 12] };
export function heightAt() { return 0; }

const WIND = [0.35, 0, -0.85];

let s = 12345;
const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);

function boulder(r) {
  const g = new THREE.SphereGeometry(r, 24, 16);
  const p = g.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    const n = Math.sin(v.x * 5.1 + 1.3) * Math.sin(v.y * 4.3)
        * Math.sin(v.z * 6.1 + 0.7);
    v.multiplyScalar(1 + n * 0.16);
    p.setXYZ(i, v.x, v.y * 0.78, v.z);
  }
  g.computeVertexNormals();
  return g;
}

export function build() {
  const g = new THREE.Group();
  const put = (geo, mat, x, y, z, rx = 0) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.rotation.x = rx;
    g.add(m);
    return m;
  };
  const stone = new THREE.MeshStandardMaterial({
    color: 0x6a6155, roughness: 0.85,
  });
  const timber = new THREE.MeshStandardMaterial({
    color: 0x40342a, roughness: 0.80,
  });

  put(new THREE.BoxGeometry(9, 0.25, 7), stone, 0, 0.125, 0);
  put(new THREE.BoxGeometry(5.8, 0.4, 1.15), stone, 0, 0.45, -2.2);
  put(new THREE.BoxGeometry(5.0, 2.7, 0.55), stone, 0, 2.0, -2.2);
  put(new THREE.BoxGeometry(0.5, 1.8, 2.2), stone, -2.2, 1.6, -0.6);
  put(new THREE.BoxGeometry(1.9, 0.14, 0.8), stone, -1.1, 2.05, -1.95);
  put(new THREE.BoxGeometry(1.5, 1.1, 0.30), timber, -1.1, 2.68, -2.05);
  put(new THREE.BoxGeometry(2.1, 0.20, 0.72), stone, -1.1, 3.30, -1.99);

  const ridgeY = 4.35;
  const gable = (deg, len, dir) => {
    const a = deg * Math.PI / 180;
    put(new THREE.BoxGeometry(5.6, 0.16, len), timber,
        0, ridgeY - Math.sin(a) * len / 2,
        -2.2 + dir * Math.cos(a) * len / 2, dir > 0 ? a : -a);
  };
  gable(28, 2.6, 1);
  gable(62, 2.6, -1);
  put(new THREE.BoxGeometry(5.7, 0.18, 0.22), timber, 0, ridgeY + 0.05, -2.2);
  put(boulder(0.85), stone, 2.9, 0.87, 1.1);
  put(boulder(0.42), stone, 1.85, 0.55, 1.75);

  const limb = (a, b, r) => {
    const d = new THREE.Vector3().subVectors(b, a);
    const m = put(new THREE.CylinderGeometry(r * 0.75, r, d.length(), 12),
        timber, 0, 0, 0);
    m.position.copy(a).addScaledVector(d, 0.5);
    m.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0), d.clone().normalize());
  };
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  limb(V(-2.5, 0.2, 0.9), V(-1.9, 1.95, -1.6), 0.075);
  limb(V(-1.9, 1.95, -1.6), V(-2.6, 2.75, -0.9), 0.05);
  limb(V(-2.1, 1.35, -0.4), V(-1.35, 1.75, 0.35), 0.038);

  const cobble = new THREE.InstancedMesh(boulder(0.17), stone, 40);
  const mx = new THREE.Matrix4();
  for (let i = 0; i < 40; i++) {
    mx.compose(
        new THREE.Vector3((rnd() * 2 - 1) * 4.0, 0.30, 0.4 + rnd() * 2.8),
        new THREE.Quaternion().setFromEuler(
            new THREE.Euler(rnd(), rnd() * 3, rnd())),
        new THREE.Vector3(1, 0.7 + rnd() * 0.6, 1));
    cobble.setMatrixAt(i, mx);
  }
  cobble.instanceMatrix.needsUpdate = true;
  g.add(cobble);

  for (const [mat, seed] of [[stone, 3], [timber, 9]]) {
    patchTriplanar(mat, { scale: 0.8 });
    patchMicroBreakup(mat, { seed, strength: 0.12 });
    patchEdgeWear(mat, { seed, strength: 0.30, width: 0.25 });
    patchSnow(mat, {
      depth: 0.06, wind: WIND, seed,
      melt: { amount: 0.2, at: [-1.1, 2.7, -1.75], radius: 1.1 },
    });
    patchSand(mat, { amount: 0.55, wind: WIND, seed });
  }
  return g;
}

export async function createScene() {
  const scene = new THREE.Scene();
  const sun = new THREE.DirectionalLight(0xfff0d8, 3.0);
  sun.position.set(6, 9, 4);
  scene.add(sun);
  scene.add(new THREE.HemisphereLight(0xbfd4ee, 0x6a5a44, 0.6));
  scene.add(build());
  const cameras = [
    { name: 'yard', position: [9, 5, 11], lookAt: [0, 2, 0], fov: 45 },
  ];
  return { scene, cameras, update() {} };
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

    terrain_shade ships astraFbmUnit from two patches behind one guard, so the
    TEXT carries it twice and the preprocessor keeps one.  Only an unguarded
    repeat is a redefinition.
    """
    return re.sub(r"#ifndef\b.*?#endif", "", src, flags=re.S)


def test_both_patches_plus_the_three_wear_patches_compile_on_one_material():
    """The stack this library exists to join, on our GPU: snow and sand on the
    SAME material that already carries a triplanar, a micro breakup and an edge
    wear, in an asset that also puts that material on an InstancedMesh.  Only a
    real instanced draw compiles the USE_INSTANCING branch of the world base
    (two programs for two materials that share one cache key is exactly that
    second, instanced program), and only a real compile proves four libraries'
    varyings, helpers and main() locals survive concatenation."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["errors"] == [], report
    assert report["compile"]["gpu"], "this claim is only worth a real GPU"
    # Both materials wear the same chain, so they SHARE a cache key: the
    # extra program is the instanced one, and it is the whole point.
    assert report["compile"]["custom_materials"] >= 2, report
    assert report["compile"]["programs"] >= 2, report


def test_the_two_patches_chain_without_losing_each_other():
    """Snow and sand on one material is not exotic — a drift of one and a bank
    of the other meet on the same ground in any half-thawed scene.
    patchStandard chains them, but it repeats whatever it is handed: the shared
    world base and its three helpers must be emitted ONCE (a second function
    body throws), the base must run FIRST or every fragment body reads a
    varying nobody wrote, and re-applying a patch has to retune its uniforms
    rather than inject a second copy of its code."""
    out = _probe("""
const m = std();
patchSnow(m, { depth: 0.03 });
patchSand(m, { amount: 0.2 });
patchSnow(m, { depth: 0.12 });
const s = compile(m);
const sandOnly = std();
patchSand(sandOnly);
console.log(JSON.stringify({
  key: m.customProgramCacheKey(), sandKey: sandOnly.customProgramCacheKey(),
  snow: s.fragmentShader.includes('diffuseColor.rgb = mix(diffuseColor.rgb,'
                                  + ' snCol, snAmt);'),
  sand: s.fragmentShader.includes('diffuseColor.rgb = mix(diffuseColor.rgb,'
                                  + ' sdCol, sdAmt);'),
  depth: m.userData.uniforms.uSnowDepth.value,
  amount: m.userData.uniforms.uSandAmt.value,
  worldBody: count(s.vertexShader, 'vec4 acP ='),
  axesFn: count(s.fragmentShader, 'vec3 astraAccAxes(vec3 n) {'),
  noiseFn: count(s.fragmentShader, 'float astraAccNoise('),
  curvFn: count(s.fragmentShader, 'float astraAccCurv('),
  snowLine: count(s.fragmentShader, 'float snD ='),
  varyVs: count(s.vertexShader, 'varying vec3 vAstraWorld;'),
  varyFs: count(s.fragmentShader, 'varying vec3 vAstraWorldN;'),
  baseFirst: s.fragmentShader.indexOf('vec3 astraAccAxes')
      < s.fragmentShader.indexOf('vec3 snN ='),
}));
""")
    assert out["snow"] and out["sand"], "a patch was lost"
    assert out["worldBody"] == 1 and out["axesFn"] == 1
    assert out["noiseFn"] == 1 and out["curvFn"] == 1
    assert out["varyVs"] == 1 and out["varyFs"] == 1
    assert out["baseFirst"], "a fragment body runs before the base helpers"
    # Every option is a uniform, so re-applying retunes in place.
    assert out["snowLine"] == 1 and out["depth"] == 0.12
    assert out["amount"] == 0.2
    assert out["key"] == "astra:acc:base+acc:snow+acc:sand", out["key"]
    # A longer chain must not collide with a shorter one's program.
    assert out["sandKey"] == "astra:acc:base+acc:sand"


def test_the_deposit_follows_the_up_vector_it_was_given():
    """Gravity is an OPTION here, not world Y: a tilted asset, a listing hull
    or a scene authored Z-up must deposit on the faces that really point up,
    and the sand wedge must be deepest at the foot measured along that same
    axis.  So both `up`s are uniforms — two materials with different ups share
    one compiled program — every cosine and every height is taken against them,
    nothing reads `.y` at all, and a degenerate vector falls back to +Y rather
    than painting NaN."""
    out = _probe("""
const yUp = std();
patchSnow(yUp); patchSand(yUp);
const zUp = std();
patchSnow(zUp, { up: new THREE.Vector3(0, 0, 4), depth: 0.2 });
patchSand(zUp, { up: [0, 0, 4], amount: 0.9 });
const arrUp = std();
patchSnow(arrUp, { up: [3, 4, 0] });
const dead = std();
patchSnow(dead, { up: [0, 0, 0] });
patchSand(dead, { up: [0, 0, 0] });
const sa = compile(yUp), sb = compile(zUp);
const u = (m, n) => m.userData.uniforms[n].value.toArray();
console.log(JSON.stringify({
  sameKey: yUp.customProgramCacheKey() === zUp.customProgramCacheKey(),
  sameFs: sa.fragmentShader === sb.fragmentShader,
  snowY: u(yUp, 'uSnowUp'), snowZ: u(zUp, 'uSnowUp'),
  sandY: u(yUp, 'uSandUp'), sandZ: u(zUp, 'uSandUp'),
  arr: u(arrUp, 'uSnowUp'),
  deadSnow: u(dead, 'uSnowUp'), deadSand: u(dead, 'uSandUp'),
  fs: sa.fragmentShader,
}));
""")
    assert out["sameKey"] and out["sameFs"], "the up vector was baked in"
    assert out["snowY"] == [0, 1, 0] and out["sandY"] == [0, 1, 0]
    assert out["snowZ"] == [0, 0, 1], "an unnormalised up must be normalised"
    assert out["sandZ"] == [0, 0, 1]
    assert [round(v, 4) for v in out["arr"]] == [0.6, 0.8, 0]
    assert out["deadSnow"] == [0, 1, 0], "a zero up must fall back, not NaN"
    assert out["deadSand"] == [0, 1, 0]
    body = _main_body(out["fs"])
    # Snow: the deposit cosine and the thaw height are both against up.
    _find(r"vec3 snUp = normalize\(uSnowUp\);", body)
    _find(r"float snC = dot\(snN, snUp\);", body)
    _find(r"float snH = dot\(vAstraWorld, snUp\)", body)
    # Sand: the wedge's height and the lies-on-top term, likewise.
    _find(r"vec3 sdUp = normalize\(uSandUp\);", body)
    _find(r"float sdRise = 1\.0 - smoothstep\(0\.0, sdTop,\s*"
          r"max\(dot\(vAstraWorld, sdUp\), 0\.0\)\);", body)
    _find(r"float sdLie = smoothstep\(-?[\d.]+, [\d.]+,"
          r" dot\(sdN, sdUp\)\);", body)
    # Nothing may take world Y for up, and nothing may need a UV.
    assert "vAstraWorld.y" not in body and ".y" not in body.replace(
        "uSnowWarm.xyz", "").replace("gl_FragCoord.xy", "")
    assert "vUv" not in body


def test_a_steep_face_keeps_no_snow_whatever_the_field_says():
    """Snow slides off.  A face past roughly 58 degrees holds none, and that is
    the single strongest cue that a white roof is snow rather than paint — a
    28-degree pitch white and a 62-degree pitch bare in the same frame.  So the
    gate must be MULTIPLICATIVE: the noise that tears the edge is a positive
    FACTOR on the deposit, never an addend that could push a wall's zero over
    the threshold, and melt may only ever subtract.  A vertical wall and a
    soffit are the same case."""
    out = _probe("""
const m = std();
patchSnow(m, { depth: 0.06 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    body = _main_body(out["fs"])
    slide = _find(r"float snLay = smoothstep\((-?[\d.]+), ([\d.]+), snC\)\s*"
                  r"\* mix\(([\d.]+), 1\.0, clamp\(snC, 0\.0, 1\.0\)\);",
                  body)
    lo, hi, floor_ = (float(g) for g in slide.groups())
    # cos of the angle it is all gone by: 0.45 is 63 deg, 0.75 is 41.
    assert 0.45 <= lo <= 0.75, (
        f"snow slides off at {math.degrees(math.acos(lo)):.0f} deg")
    assert lo < hi <= 0.95, "a shallow pitch must reach the full fall"
    assert 0 < floor_ <= 1, "a pitch holds less than a flat, never more"
    # The whole deposit is a PRODUCT, so any zero factor is the end of it;
    # the field is a strictly positive multiplier.
    field = _find(r"float snD = snLay \* snHold \* snScour"
                  r" \* \(([\d.]+) \+ ([\d.]+) \* snG\);", body)
    assert float(field.group(1)) > 0
    # Melt only ever takes away, and the threshold never reaches zero, so a
    # face with no deposit can never cross it.
    _find(r"snD -= snThaw \* [\d.]+ \+ snWarm \* [\d.]+;", body)
    thr = _find(r"float snT = mix\(([\d.]+), ([\d.]+), snCov\);", body)
    assert float(thr.group(1)) > float(thr.group(2)) > 0, thr.groups()
    _find(r"float snK = smoothstep\(snT - [\d.]+ - snAA,"
          r" snT \+ [\d.]+ \+ snAA, snD\);", body)
    # ...and the edge is torn by its own screen gradient, not ruled.
    _find(r"float snAA = clamp\(fwidth\(snD\), 0\.0, [\d.]+\);", body)


def test_a_lee_holds_more_than_an_edge_does():
    """What separates a deposit from a coat of paint is that it is not where
    the surface sticks out.  A convex edge sheds and a concave lee holds, for
    snow and for sand alike — and sand fills the concavities FIRST, which is
    why a lightly sanded floor shows sand only in its corners and joints.  The
    estimate is the surface's own SIGNED curvature in 1/m (the world normal's
    screen derivative over the world-space length of that pixel step), so it
    holds still under distance and resolution; and the radius it looks for
    scales with the deposit, because a deeper fall buries a bigger feature.

    Sand's shed window starts LATER than its fill's on our host: at the
    reference's 0.5/2.5 every cobble in a drift came out bare while the sand
    ran on around it, which reads as a deflation pavement rather than a yard
    that has been blown into."""
    out = _probe("""
const m = std();
patchSnow(m, { depth: 0.06 });
patchSand(m, { amount: 0.45 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    body = _main_body(fs)
    # Curvature: the normal's derivative over the position's, signed.
    _find(r"float astraAccCurv\(vec3 n, vec3 p\) \{\s*"
          r"vec3 dx = dFdx\(p\), dy = dFdy\(p\);\s*"
          r"float d = dot\(dx, dx\) \+ dot\(dy, dy\);\s*"
          r"return \(dot\(dFdx\(n\), dx\) \+ dot\(dFdy\(n\), dy\)\)"
          r" / max\(d, 1e-12\);", fs)
    # Snow: the convex half SUBTRACTS, the concave half ADDS, and the radius
    # it looks for is set by how deep the fall is.
    _find(r"float snR = 1\.0 / max\(uSnowDepth \* [\d.]+, [\d.]+\);", body)
    hold = _find(r"float snHold = 1\.0"
                 r" - ([\d.]+) \* smoothstep\(snR \* [\d.]+, snR \* [\d.]+,"
                 r" snCv\)\s*"
                 r"\+ ([\d.]+) \* smoothstep\(snR \* [\d.]+, snR \* [\d.]+,"
                 r" -snCv\);", body)
    assert float(hold.group(1)) > 0 and float(hold.group(2)) > 0
    _find(r"float snD = snLay \* snHold \*", body)
    # Sand: the same two halves, and the concave one is what lets a low
    # `amount` show sand in the corners and nowhere else.
    _find(r"float sdR = 1\.0 / max\(uSandAmt \* [\d.]+ \+ [\d.]+,"
          r" [\d.]+\);", body)
    fill = _find(r"float sdFill = smoothstep\(sdR \* ([\d.]+),"
                 r" sdR \* ([\d.]+), -sdCv\);", body)
    shed = _find(r"float sdShed = smoothstep\(sdR \* ([\d.]+),"
                 r" sdR \* ([\d.]+), sdCv\);", body)
    # A stone is not a rib: the flank of anything rounder than the fill's own
    # radius must still take sand.
    assert float(shed.group(1)) > float(fill.group(1)), (
        "sand sheds off a curve as readily as it fills one")
    assert float(shed.group(2)) > float(fill.group(2))
    lay = _find(r"float sdLay = clamp\(\(max\(sdLie \* [\d.]+, sdBank\)\s*"
                r"\+ sdFill \* ([\d.]+)\) \* sdRise\s*"
                r"- sdShed \* ([\d.]+), 0\.0, [\d.]+\);", body)
    assert float(lay.group(2)) < 0.5, "the shed must not out-argue the wedge"


def test_sand_ripples_run_across_the_wind_and_not_along_it():
    """Aeolian ripples lie SQUARE to the flow: their crests are lines of
    constant distance along the wind, so the phase must advance along the wind
    axis and along nothing else.  Get that backwards and the sand is combed the
    way a river would comb it, which reads as flow rather than as wind.  The
    crest holds a little more sand than the trough (the ripple is in the
    COVERAGE, not only in the tone), the lee face of each crest is steeper than
    its windward one, the whole thing dies with the wind's strength, and it is
    dropped once a pixel spans one period — past that it is not a ripple, it is
    static."""
    out = _probe("""
const m = std();
patchSand(m, { amount: 0.45, wind: [0.6, 0, 0.8] });
const still = std();
patchSand(still, { wind: [0, 0, 0] });
const long_ = std();
patchSand(long_, { wind: [4, 0, 3] });
console.log(JSON.stringify({
  fs: compile(m).fragmentShader,
  wind: m.userData.uniforms.uSandWind.value.toArray(),
  still: still.userData.uniforms.uSandWind.value.toArray(),
  clamped: long_.userData.uniforms.uSandWind.value.toArray(),
}));
""")
    body = _main_body(out["fs"])
    # The wind axis, its strength its LENGTH, and never a normalize() of a
    # zero vector.
    _find(r"float sdWl = clamp\(length\(uSandWind\), 0\.0, 1\.0\);", body)
    _find(r"vec3 sdWv = uSandWind / max\(length\(uSandWind\), 1e-4\);", body)
    # THE CLAIM: the phase is the distance ALONG the wind, so the crests are
    # the lines square to it.
    _find(r"float sdPh = dot\(vAstraWorld, sdWv\) / sdLam"
          r" \+ \(sdG - 0\.5\) \* [\d.]+;", body)
    lam = _find(r"float sdLam = mix\(([\d.]+), ([\d.]+), uSandAmt\);", body)
    lo, hi = float(lam.group(1)), float(lam.group(2))
    assert 0.02 <= lo < hi <= 1.0, "a ripple is a decimetre thing"
    # Skewed, faded and gone with the wind.
    rip = _find(r"float sdRip = sin\(sdPh \+ ([\d.]+) \* sin\(sdPh\)\)"
                r" \* sdFade \* sdWl\s*\* mix\([\d.]+, 1\.0, sdLie\);", body)
    assert float(rip.group(1)) > 0, "an unskewed ripple is a sine wave"
    _find(r"float sdFade = 1\.0 - smoothstep\([\d.]+, [\d.]+,\s*"
          r"length\(fwidth\(vAstraWorld\)\) / sdLam\);", body)
    # In the coverage AND in the tone: a ripple that is only a stripe of
    # colour is a decal on flat sand.
    _find(r"float sdD = sdLay \* \([\d.]+ \+ [\d.]+ \* sdG\)"
          r" \+ sdRip \* [\d.]+;", body)
    # ...and the crest is sun-bleached WARM against a cool trough, not one
    # brightness up and down: a mono ripple is a painted stripe.
    tint = _find(r"sdCol \*= vec3\(1\.0\) \+ sdRip"
                 r" \* vec3\(([\d.]+), ([\d.]+), ([\d.]+)\);", body)
    r_, g_, b_ = (float(v) for v in tint.groups())
    assert r_ > g_ > b_ > 0, (r_, g_, b_)
    # The wind is a uniform vector with its strength in its length: a unit
    # wind is full strength, a zero wind has no drift axis at all.
    assert [round(v, 4) for v in out["wind"]] == [0.6, 0, 0.8]
    assert out["still"] == [0, 0, 0]
    assert [round(v, 4) for v in out["clamped"]] == [0.8, 0, 0.6]


def test_no_patch_declares_a_name_its_neighbours_own():
    """The silent failure this library is most exposed to: these two ride the
    same materials as surface_wear, terrain_shade, waterside and aging, and
    patchStandard DROPS a repeated uniform or varying and keeps the first — so
    a shared name would leave one patch reading the other's value with nothing
    reported.  A repeated main() local is a compile error instead, and a
    repeated helper throws.  So the uniform sets must be disjoint (uTime
    excepted, the shared clock), and a material wearing all ELEVEN patches must
    declare every name exactly once."""
    out = _probe("""
const acc = std();
patchSnow(acc);
patchSand(acc);
const nbr = std();
patchTriplanar(nbr);
patchSlopeSplat(nbr, { snowLine: 30 });
patchShoreWet(nbr);
patchShoreFoam(nbr);
patchMicroBreakup(nbr);
patchEdgeWear(nbr);
patchDripStains(nbr);
patchRust(nbr);
patchDust(nbr);
const all = std();
patchTriplanar(all);
patchSlopeSplat(all, { snowLine: 30 });
patchShoreWet(all);
patchShoreFoam(all);
patchMicroBreakup(all);
patchEdgeWear(all);
patchDripStains(all);
patchRust(all);
patchDust(all);
patchSnow(all);
patchSand(all);
const s = compile(all);
console.log(JSON.stringify({
  accU: Object.keys(acc.userData.uniforms),
  nbrU: Object.keys(nbr.userData.uniforms),
  vs: s.vertexShader, fs: s.fragmentShader,
}));
""")
    shared = set(out["accU"]) & set(out["nbrU"])
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
    # ...and the base folds in the instance transform, or every copy of a
    # scattered cobble takes its weather from the mesh ORIGIN.
    body = _main_body(out["vs"])
    assert "#ifdef USE_INSTANCING" in body
    _find(r"acP = instanceMatrix \* acP;", body)
    _find(r"acN = mat3\(instanceMatrix\) \* acN;", body)
    _find(r"vAstraWorld = \(modelMatrix \* acP\)\.xyz;", body)
    assert "attribute mat4 instanceMatrix" not in out["vs"]
    # fwidth, dFdx, gl_FragCoord and astraStroke are fragment-only, and the
    # util block ships in both stages: they may be DEFINED in the vertex
    # shader, never called from it.
    assert "fwidth(" not in body and "dFdx(" not in body
    assert "gl_FragCoord" not in body


def test_an_option_is_a_uniform_and_never_baked_into_the_source():
    """three caches programs by key and the FIRST material to compile a key
    decides the GLSL every material sharing it gets.  Two materials that differ
    only in options must therefore compile to the same source, or the second
    silently wears the first's weather — here that would mean one roof's snow
    depth, one desert's wind direction and one thaw line imposed on every other
    surface in the scene.  Same key, byte-identical GLSL, different uniform
    values."""
    out = _probe("""
const a = std();
patchSnow(a, { depth: 0.05, seed: 1 });
patchSand(a, { amount: 0.45, seed: 1 });
const b = std();
patchSnow(b, { depth: 0.42, seed: 99, up: [0, 0, 1], wind: [0.5, 0, 0.5],
               color: new THREE.Color(0x112233),
               melt: { amount: 0.8, at: [3, 2, 1], radius: 2.5 } });
patchSand(b, { amount: 0.95, seed: 99, up: [0, 0, 1], wind: [0, 0, -1],
               color: new THREE.Color(0x445566) });
const sa = compile(a), sb = compile(b);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  sameKey: a.customProgramCacheKey() === b.customProgramCacheKey(),
  sameVs: sa.vertexShader === sb.vertexShader,
  sameFs: sa.fragmentShader === sb.fragmentShader,
  aSnow: [u(a).uSnowDepth.value, u(a).uSnowMelt.value,
          u(a).uSnowWarm.value.toArray()],
  bSnow: [u(b).uSnowDepth.value, u(b).uSnowMelt.value,
          u(b).uSnowWarm.value.toArray()],
  aSand: [u(a).uSandAmt.value, u(a).uSandWind.value.toArray()],
  bSand: [u(b).uSandAmt.value, u(b).uSandWind.value.toArray()],
  colors: [u(b).uSnowColor.value.getHex(), u(b).uSandColor.value.getHex()],
}));
""")
    assert out["sameKey"] and out["sameVs"] and out["sameFs"]
    assert out["aSnow"] == [0.05, 0, [0, 0, 0, 0]]
    # A warm point is a uniform vec4 too, so the flue that melts a hole
    # around itself does not need its own compiled program.
    assert out["bSnow"] == [0.42, 0.8, [3, 2, 1, 2.5]]
    assert out["aSand"] == [0.45, [1, 0, 0]]
    assert out["bSand"] == [0.95, [0, 0, -1]]
    assert out["colors"] == [0x112233, 0x445566]


def test_one_seed_lays_the_same_weather_every_time():
    """A render is re-run — for a fix round, for a video, for the judge — and
    the drifts must not move between takes.  The seed reaches the GPU only as a
    noise-space OFFSET (a uniform, because baking it would hand material one's
    seed to every material sharing the key), so one seed must give one set of
    offsets, two seeds must not land on the same drifts, and snow must be
    decorrelated from sand or every dune would sit under a snowdrift.  The
    offsets must also differ from surface_wear's and aging's for the same seed,
    or the drifts would ride that library's blotches."""
    out = _probe("""
const mk = (seed) => {
  const m = std();
  patchSnow(m, { seed });
  patchSand(m, { seed });
  patchMicroBreakup(m, { seed });
  patchDust(m, { seed });
  return m;
};
const a = mk(7), b = mk(7), c = mk(8);
const u = (m, n) => m.userData.uniforms[n].value.toArray();
console.log(JSON.stringify({
  aSnow: u(a, 'uSnowSeed'), bSnow: u(b, 'uSnowSeed'), cSnow: u(c, 'uSnowSeed'),
  aSand: u(a, 'uSandSeed'), cSand: u(c, 'uSandSeed'),
  aMicro: u(a, 'uMicroSeed'), aDust: u(a, 'uDustSeed'),
  sameSrc: compile(a).fragmentShader === compile(c).fragmentShader,
}));
""")
    assert out["aSnow"] == out["bSnow"], "the same seed moved the drifts"
    assert out["aSnow"] != out["cSnow"] and out["aSand"] != out["cSand"]
    assert out["aSnow"] != out["aSand"], "the dunes sit under the drifts"
    assert out["aSnow"] != out["aMicro"] and out["aSnow"] != out["aDust"]
    assert out["aSand"] != out["aMicro"] and out["aSand"] != out["aDust"]
    assert all(0 <= v <= 56 for v in out["aSnow"] + out["aSand"])
    # Same GLSL for both seeds: the seed is a uniform, not source.
    assert out["sameSrc"]
    assert "Math.random" not in _LIB_SRC


def test_both_covers_take_the_gloss_over_through_compose_roughness():
    """`fragmentBody` lands after <color_fragment> and therefore BEFORE
    <roughnessmap_fragment> declares roughnessFactor, so neither patch can
    touch a per-pixel gloss — and a snowed pane of glass that keeps its mirror
    finish is the tell.  Both therefore move the MATERIAL's roughness toward
    the cover's own, by how much of the surface they took over, through
    composeRoughness: one factor each, so any order and any number of
    re-applications land on the same number, and a thawed-out snow patch gives
    the material's finish back."""
    out = _probe("""
const shiny = std({ roughness: 0.2 });
patchSnow(shiny, { depth: 0.05 });
const snowR = shiny.roughness;
patchSnow(shiny, { depth: 0.05 });
const twice = shiny.roughness;
const sandy = std({ roughness: 0.2 });
patchSand(sandy, { amount: 0.45 });
const sandR = sandy.roughness;
const both = std({ roughness: 0.2 });
patchSnow(both, { depth: 0.05 });
patchSand(both, { amount: 0.45 });
const thawed = std({ roughness: 0.2 });
patchSnow(thawed, { depth: 0.05, melt: 1 });
const rough = std({ roughness: 0.95 });
patchSand(rough, { amount: 1 });
patchSnow(rough, { depth: 0.3 });
const buried = std({ roughness: 0.2 });
patchSand(buried, { amount: 1 });
const basic = new THREE.MeshBasicMaterial({ color: 0x445566 });
patchSnow(basic);
patchSand(basic);
console.log(JSON.stringify({
  snowR, twice, sandR, thawed: thawed.roughness, rough: rough.roughness,
  buried: buried.roughness,
  both: both.roughness,
  base: both.userData.astraRoughness.base,
  factors: Object.keys(both.userData.astraRoughness.factors).sort(),
  basicRough: basic.roughness === undefined,
  basicPatched: basic.userData.astraPatches.map((p) => p.name),
}));
""")
    assert out["base"] == 0.2, "the authored value is the one to compose on"
    assert sorted(out["factors"]) == sorted(["acc:snow", "acc:sand"])
    # Snow is a diffuse cover with only the faintest sheen (0.86 here, not the
    # reference's 0.72: with no post chain a glossy white cover adds a specular
    # lobe on top of an already-clipped diffuse and the fall goes to a card);
    # sand is the mattest thing in any scene.  Both move a polished surface
    # toward their own.
    assert abs(out["snowR"] - (0.2 + 0.65 * (0.86 - 0.2))) < 1e-9, out
    assert abs(out["sandR"] - (0.2 + 0.315 * (0.94 - 0.2))) < 1e-9, out
    assert out["twice"] == out["snowR"], "re-applying compounded the gloss"
    assert out["both"] > out["snowR"], "two covers, two factors"
    # A cover that has melted away gives the finish back.
    assert abs(out["thawed"] - 0.2) < 1e-9
    # Two factors compose by MULTIPLYING (composeRoughness's contract), so a
    # fully matte cover on an already-rough material still lands inside the
    # range instead of running off the top of it.
    assert 0.5 <= out["rough"] <= 1.0, out["rough"]
    assert abs(out["buried"] - (0.2 + 0.70 * (0.94 - 0.2))) < 1e-9, out
    # A material with no roughness at all still takes the albedo patch.
    assert out["basicRough"]
    assert out["basicPatched"] == ["acc:base", "acc:snow", "acc:sand"]


def test_melt_gives_the_material_back_from_the_bottom_up():
    """A wall's footing goes bare while its coping stays white: the foot of
    anything is warmer (ground heat, splash, the traffic past it), so a thaw is
    a LINE that climbs.  A fragment cannot see the object it belongs to, so the
    line is measured from the plane through the world origin — which is exactly
    where the turntable makes an asset rest — and it is ragged, because it
    rides the same field the deposit's edge does.  `melt` also takes an
    `at`/`radius`, which eats a hole around one warm world point: a flue, a
    lamp, a vent.  Both are uniforms, so a thaw can run over a shot without
    recompiling anything."""
    out = _probe("""
const dry = std();
patchSnow(dry);
const half = std();
patchSnow(half, { melt: 0.5 });
const flue = std();
patchSnow(flue, { melt: { amount: 0.1, at: [2, 3, 4], radius: 1.5 } });
const bare = std();
patchSnow(bare, { melt: { amount: 0.4 } });
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  sameFs: compile(dry).fragmentShader === compile(half).fragmentShader,
  fs: compile(dry).fragmentShader,
  dry: [u(dry).uSnowMelt.value, u(dry).uSnowWarm.value.toArray()],
  half: u(half).uSnowMelt.value,
  flue: [u(flue).uSnowMelt.value, u(flue).uSnowWarm.value.toArray()],
  bare: [u(bare).uSnowMelt.value, u(bare).uSnowWarm.value.toArray()],
  over: (() => { const m = std(); patchSnow(m, { melt: 4 });
                 return m.userData.uniforms.uSnowMelt.value; })(),
}));
""")
    assert out["sameFs"], "the thaw line was baked in"
    # No melt is no warm point either: a zero radius is OFF, not a zero-metre
    # hole at the world origin.
    assert out["dry"] == [0, [0, 0, 0, 0]]
    assert out["half"] == 0.5 and out["over"] == 1
    assert out["flue"] == [0.1, [2, 3, 4, 1.5]]
    assert out["bare"] == [0.4, [0, 0, 0, 0]], "no `at` must not melt a hole"
    body = _main_body(out["fs"])
    # The line climbs with melt, from BELOW the resting plane (so melt 0 thaws
    # nothing at all) to well above a storey.
    line = _find(r"float snLine = mix\((-[\d.]+), ([\d.]+), uSnowMelt\);",
                 body)
    assert float(line.group(1)) < 0 < float(line.group(2))
    _find(r"float snThaw = 1\.0 - smoothstep\(snLine - [\d.]+,"
          r" snLine \+ [\d.]+, snH\);", body)
    # Ragged, on the same field that tears the deposit's own edge.
    _find(r"float snH = dot\(vAstraWorld, snUp\) \+ \(snG - 0\.5\)"
          r" \* [\d.]+;", body)
    # The warm hole is a distance from a world point, switched OFF by a zero
    # radius rather than by a branch.
    _find(r"float snWarm = \(1\.0 - smoothstep\(snWr \* [\d.]+, snWr,\s*"
          r"length\(vAstraWorld - uSnowWarm\.xyz\)\)\)\s*"
          r"\* step\(1e-4, uSnowWarm\.w\);", body)


def test_the_cover_shades_only_the_material_it_actually_stops_against():
    """THE PORT'S REGRESSION TEST.  Neither patch can displace geometry, so the
    only thickness either layer has is the soft shade it drops on the material
    just OUTSIDE its edge — and "just outside" has to mean it.  The reference
    took a fixed 0.55 below the coverage threshold, but that threshold FALLS as
    the deposit deepens (0.92 for a dusting, 0.18 for a burying fall), so past
    a dusting the band's floor went negative and a surface with ZERO deposit
    sat inside the ramp: on our host at the shipped 0.05 m depth every vertical
    wall, soffit and boulder flank in the frame came back up to 21% darker,
    with the melt hole's own falloff printed through the bare wall behind it.

    So the band is a FRACTION of the threshold: strictly positive at every
    depth, and zero where there is no deposit at all."""
    out = _probe("""
const m = std();
patchSnow(m, { depth: 0.05 });
patchSand(m, { amount: 0.5 });
const s = compile(m);
console.log(JSON.stringify({ fs: s.fragmentShader, vs: s.vertexShader }));
""")
    body = _main_body(out["fs"])
    for pre in ("sn", "sd"):
        lip = _find(rf"float {pre}Lip = \(1\.0 - {pre}K\)"
                    rf" \* smoothstep\({pre}T \* ([\d.]+),"
                    rf" {pre}T - ([\d.]+),\s*{pre}D\);", body)
        frac_, top = float(lip.group(1)), float(lip.group(2))
        assert 0 < frac_ < 1, f"{pre}Lip's floor must scale with {pre}T"
        assert frac_ * 0.18 > 0 and top < 0.18, (
            f"{pre}Lip's band must stay inside the smallest threshold")
        _find(rf"diffuseColor\.rgb \*= 1\.0 - ([\d.]+) \* {pre}Lip;", body)
    # No fixed offset anywhere: that is the shape of the bug.
    assert "smoothstep(snT - 0.55" not in body
    assert "smoothstep(sdT - 0.55" not in body
    # Thin snow lets the substrate through; deep snow does not.
    thin = _find(r"float snThin = mix\(([\d.]+), 1\.0,"
                 r" smoothstep\([\d.]+, [\d.]+, snD\)\);", body)
    assert 0 < float(thin.group(1)) < 1
    _find(r"float snAmt = clamp\(snK \* snThin", body)
    # Nothing is displaced, and the vertex stage does no work beyond the world
    # position: the base is the whole of it.
    vbody = _main_body(out["vs"])
    assert "transformed +=" not in vbody and "transformed *=" not in vbody


def test_neither_cover_is_one_flat_value():
    """The aesthetic law this port added, pinned so an edit cannot flatten it.
    Our host tone-maps ACES at exposure 1.0 with NO post chain, so a cover
    shaded across the top of the curve renders as one card whatever field is
    under it (measured: the reference's 0.85-linear white gave a snowed roof
    8 bits of luminance spread over 60 px and a mean saturation of 0.017).  So
    each cover carries: a default albedo under the shoulder, a tonal swing wide
    enough to survive the curve, a SECOND finer field for the tone that is
    faded out before it can alias, hue break at TWO scales, and a dither
    against the banding the first three would otherwise put on a big flat
    panel."""
    out = _probe("""
const m = std();
patchSnow(m, { depth: 0.06 });
patchSand(m, { amount: 0.5 });
console.log(JSON.stringify({
  fs: compile(m).fragmentShader,
  snow: m.userData.uniforms.uSnowColor.value.toArray(),
  sand: m.userData.uniforms.uSandColor.value.toArray(),
}));
""")
    body = _main_body(out["fs"])
    # Linear albedos under the shoulder: snow is the brightest thing a scene
    # holds and still has to stay inside the range a dielectric can have.
    assert 0.45 <= max(out["snow"]) <= 0.80, out["snow"]
    assert min(out["snow"]) > 0.4, "a snow default must not read grey"
    assert 0.20 <= max(out["sand"]) <= 0.60, out["sand"]
    assert out["sand"][0] > out["sand"][1] > out["sand"][2], "ochre, not grey"
    for pre, col, lo_max in (("sn", "uSnowColor", 0.60),
                             ("sd", "uSandColor", 0.75)):
        tone = _find(rf"vec3 {pre}Col = mix\({col} \* ([\d.]+),"
                     rf" {col} \* ([\d.]+),\s*"
                     rf"smoothstep\([\d.]+, [\d.]+, {pre}Tn\)\);", body)
        lo, hi = float(tone.group(1)), float(tone.group(2))
        assert lo <= lo_max and hi >= 1.0 and hi / lo >= 1.6, (pre, lo, hi)
        # The tone's own finer field, faded out before it can alias.
        _find(rf"float {pre}Fad = 1\.0 - smoothstep\([\d.]+, [\d.]+,\s*"
              rf"length\(fwidth\(vAstraWorld\)\)\);", body)
        _find(rf"float {pre}F = astraAccNoise\({pre}P \* [\d.]+"
              rf" \+ [\d.]+, {pre}W\);", body)
        _find(rf"float {pre}Tn = clamp\(mix\({pre}G,"
              rf" {pre}G \* [\d.]+ \+ {pre}F \* [\d.]+, {pre}Fad\),\s*"
              rf"0\.0, 1\.0\);", body)
        # Hue break at TWO scales: one field the width of a yard is still one
        # flat tint on a roof.
        breaks = re.findall(
            rf"{pre}Col = astraHueBreak\({pre}Col, vAstraWorld\.xz"
            rf"(?: \+ [\d.]+)?, ([\d.]+), ([\d.]+)\);", body)
        assert len(breaks) == 2, breaks
        (s0, w0), (s1, w1) = ((float(a), float(b)) for a, b in breaks)
        assert s1 > s0 * 4, "the second break must be a tighter field"
        assert 0 < w1 < w0, "the tight one swings less, or it stops reading"
        # ...and a dither, because there is no post chain to add one.
        _find(rf"{pre}Col \+= \(astraHash21\(gl_FragCoord\.xy\) - 0\.5\)"
              rf" \* [\d.]+;", body)
    # Snow's cold half rides the PACK, not just the rare concave lee: a drift
    # scatters sky into itself while a scoured lane hands the substrate back.
    _find(r"float snPack = clamp\(smoothstep\([\d.]+, [\d.]+, snD\)\s*"
          r"\+ clamp\(snHold - 1\.0, 0\.0, 1\.0\) \* [\d.]+,\s*"
          r"0\.0, 1\.0\);", body)
    warm_cold = _find(
        r"snCol \*= mix\(vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),"
        r" vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),\s*snPack\);", body)
    wr, wg, wb, cr, cg, cb = (float(v) for v in warm_cold.groups())
    assert wr > wg > wb, "the scoured lane must be the WARM end"
    assert cb > cg > cr, "the deep pack must be the COLD end"
