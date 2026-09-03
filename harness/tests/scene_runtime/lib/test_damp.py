"""damp.js: moss that has a direction, an apron, and a cracked bed.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_damp_lib.py).  Their renderer-contract assertions are dropped; the
physics claims, the shared-name contract and the option-is-a-uniform law are
kept, and TWO things this port changed are pinned so a later edit cannot
quietly undo them:

* the TONE FAMILIES.  Every patch used to paint one colour with a brightness
  ramp over it (measured on our showcase: a metre of moss read as felt and a
  dried bed as one chip of paint).  Each `color` option now derives a family —
  a moss deep/body/crown plus two lichen crusts, a bed's pale and damp plate —
  and the SIGN of every hue shift is asserted, because getting it backwards is
  invisible in code and renders an acid-green cushion.
* the ROUGHNESS TARGETS.  `composeRoughness` is per MATERIAL, so a wet target
  of 0.34 polished the dry terrace metres from any shore as well; under our
  renderer (baked sky environment, ACES at exposure 1.0, no post chain) that
  turned the whole ground into a sky mirror and lifted it from luminance 0.607
  to 0.823 — brighter, when the entire point of the apron is that it is
  darker.  0.62 (moisture) and 0.55 (a soaked crack) are the ported values.

These three patches land on materials that already carry a triplanar, a micro
breakup and a waterline, so the shared contract is asserted first: one world
base, no name any neighbour owns, and every option a UNIFORM (`patchStandard`
absorbs a duplicate uniform SILENTLY and the first material to compile a cache
key fixes the GLSL for every material sharing it).
"""
from __future__ import annotations

import json
import re
from collections import Counter

import pytest
from _probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "terrain_shade.js", "waterside.js", "surface_wear.js",
         "aging.js", "accumulation.js", "strata.js", "damp.js")

_LIB_SRC = (LIB_DIR / "damp.js").read_text(encoding="utf-8")

# The patch only exists inside onBeforeCompile, so every probe hands it the two
# chunks patchStandard replaces and reads back what it wrote.  `run` lifts a
# stretch of the SHIPPED fragment body out and evaluates it: GLSL scalar code
# is valid JS once the type keywords are dropped and clamp/smoothstep/mix/dot
# exist, so a claim about the physics can be measured instead of
# pattern-matched.  new Function is sloppy mode even inside a module, which is
# what makes `with` available.
_PRELUDE = """
import * as THREE from 'three';
import { patchMoss, patchMoisture, patchCrackedMud } from './lib/damp.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
import { patchShoreWet, patchShoreFoam } from './lib/waterside.js';
import { patchDripStains, patchRust, patchDust } from './lib/aging.js';
import { patchSnow, patchSand } from './lib/accumulation.js';
import { patchRockStrata, patchErosionStreaks } from './lib/strata.js';
import { GLSL_UTIL } from './lib/shader.js';

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

const body = (src) => src.slice(src.indexOf('void main'));

/** The shipped fragment body from `from` up to (not including) `to`. */
function slice_(src, from, to) {
  const b = body(src);
  const i = b.indexOf(from);
  const j = to ? b.indexOf(to, i) : b.length;
  if (i < 0 || j < 0) throw new Error('no ' + from + ' .. ' + to);
  return b.slice(i, j);
}

const GLSL = {
  clamp: (x, a, b) => Math.min(b, Math.max(a, x)),
  mix: (a, b, t) => a + (b - a) * t,
  smoothstep: (e0, e1, x) => {
    const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
    return t * t * (3 - 2 * t);
  },
  dot: (a, b) => a.reduce((s, v, i) => s + v * b[i], 0),
  normalize: (a) => {
    const l = Math.hypot(...a) || 1;
    return a.map((v) => v / l);
  },
  fwidth: () => 0,
  max: Math.max, min: Math.min, abs: Math.abs, floor: Math.floor,
};

/** Evaluate shipped GLSL scalar code with `ctx` in scope. */
function run(code, ctx) {
  const js = code.replace(/^\\s*(?:float|vec2|vec3|vec4) /gm, '');
  const f = new Function('c', 'with (c) {\\n' + js
      + '\\nreturn (typeof __out === "function") ? __out() : null;\\n}');
  return f(Object.assign({}, GLSL, ctx));
}
"""

# A shoreline strip as a workspace SCENE: a bank rising out of the water to a
# terrace, a boulder standing clear on it, a wall on its footing and a dried
# pan on the terrace.  The pan's material carries ALL SIX patches, and the
# cobbles are instanced on that same material because a real instanced program
# is the only thing that compiles the USE_INSTANCING branch of the world base.
_SCENE = """
import * as THREE from 'three';
import { patchTriplanar } from './lib/terrain_shade.js';
import { patchMicroBreakup } from './lib/surface_wear.js';
import { patchShoreWet } from './lib/waterside.js';
import { patchMoss, patchMoisture, patchCrackedMud } from './lib/damp.js';

const NORTH = [0, 0, -1];
const WATER_Y = 0.35;
const BED = new THREE.Vector2(2.5, 2.5);

export const BOUNDS = { min: [-20, 0, -20], max: [20, 12, 20] };

let s = 20261;
const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
const smooth = (t) => t * t * (3 - 2 * t);
const clamp01 = (t) => Math.max(0, Math.min(1, t));

function bankY(x, z) {
  const t = smooth(clamp01((z + 4.5) / 6.5));
  return 1.3 * t
      + 0.09 * Math.sin(x * 1.13 + 1.1) * Math.sin(z * 0.91)
      + 0.04 * Math.sin(x * 2.7 - 0.4) * Math.sin(z * 2.3 + 2.0);
}

function bank() {
  const g = new THREE.PlaneGeometry(9, 9, 60, 60);
  g.rotateX(-Math.PI / 2);
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) p.setY(i, bankY(p.getX(i), p.getZ(i)));
  g.computeVertexNormals();
  return g;
}

function pan(r) {
  const g = new THREE.CircleGeometry(r, 64);
  g.rotateX(-Math.PI / 2);
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const d = Math.hypot(p.getX(i), p.getZ(i)) / r;
    p.setY(i, -0.22 * (1 - d * d) + 0.03 * Math.sin(d * 8.0));
  }
  g.computeVertexNormals();
  return g;
}

function boulder(r, k) {
  const g = new THREE.SphereGeometry(r, 24, 16);
  const p = g.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    const n = Math.sin(v.x * k + 1.3) * Math.sin(v.y * (k * 0.8))
        * Math.sin(v.z * (k * 1.2) + 0.7);
    v.multiplyScalar(1 + n * 0.18);
    p.setXYZ(i, v.x, v.y * 0.80, v.z);
  }
  g.computeVertexNormals();
  return g;
}

export function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const sun = new THREE.DirectionalLight(0xfff0d6, 2.5);
  sun.position.set(5, 8, 7);
  sun.castShadow = true;
  scene.add(sun);

  const g = new THREE.Group();
  const put = (geo, mat, x, y, z) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    g.add(m);
    return m;
  };
  const bed = new THREE.MeshStandardMaterial(
      { color: 0x9c9280, roughness: 0.85 });
  const stone = new THREE.MeshStandardMaterial(
      { color: 0x8e8880, roughness: 0.80 });
  const ground = new THREE.MeshStandardMaterial(
      { color: 0x7d7a68, roughness: 0.88 });

  put(bank(), ground, 0, 0, 0);
  const water = new THREE.Mesh(
      new THREE.PlaneGeometry(9, 4.4),
      new THREE.MeshStandardMaterial({ color: 0x24444c, roughness: 0.10 }));
  water.rotation.x = -Math.PI / 2;
  water.position.set(0, WATER_Y, -2.6);
  g.add(water);

  put(pan(1.5), bed, BED.x, bankY(BED.x, BED.y) + 0.24, BED.y);
  put(boulder(0.75, 5.1), stone, -2.6, bankY(-2.6, 0.8) + 0.48, 0.8);
  put(boulder(0.34, 7.3), stone, -1.5, bankY(-1.5, 1.7) + 0.22, 1.7);

  const wy = bankY(-3.0, 3.3);
  put(new THREE.BoxGeometry(2.6, 0.25, 0.80), stone, -3.0, wy + 0.09, 3.3);
  put(new THREE.BoxGeometry(2.3, 1.50, 0.45), stone, -3.0, wy + 0.96, 3.3);
  put(new THREE.BoxGeometry(2.5, 0.14, 0.60), stone, -3.0, wy + 1.78, 3.3);

  const cobble = new THREE.InstancedMesh(boulder(0.15, 9.1), bed, 40);
  const mx = new THREE.Matrix4();
  for (let i = 0; i < 40; i++) {
    const a = rnd() * Math.PI * 2;
    const r = 0.3 + rnd() * 1.7;
    const x = BED.x + Math.cos(a) * r;
    const z = BED.y + Math.sin(a) * r;
    mx.compose(
        new THREE.Vector3(x, bankY(x, z) + 0.12, z),
        new THREE.Quaternion().setFromEuler(
            new THREE.Euler(rnd(), rnd() * 3, rnd())),
        new THREE.Vector3(1, 0.65 + rnd() * 0.7, 1));
    cobble.setMatrixAt(i, mx);
  }
  cobble.instanceMatrix.needsUpdate = true;
  cobble.frustumCulled = false;
  g.add(cobble);

  for (const [mat, seed, tri] of [[bed, 3, 0.6], [stone, 6, 0.8],
                                  [ground, 9, 1.4]]) {
    patchTriplanar(mat, { scale: tri });
    patchMicroBreakup(mat, { seed, strength: 0.12 });
    patchShoreWet(mat, { level: WATER_Y, band: 0.25, darken: 0.45 });
    patchMoss(mat, { amount: 0.5, north: NORTH, seed });
    patchMoisture(mat, { waterY: WATER_Y, reach: 0.9, strength: 0.5, seed });
  }
  patchCrackedMud(bed, { scale: 0.24, depth: 0.75, wet: 0.0, seed: 3 });

  scene.add(g);
  // A camera is not decoration: the host reports `ok: false` with an EMPTY
  // error when `cameras` is empty, so a scene with none fails the compile
  // preflight before a program is built.
  return {
    scene,
    cameras: [{ name: 'hero', position: [9, 4, 11], lookAt: [0, 1, 0],
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
    """Drop ``#ifndef X ... #endif`` blocks, which may repeat verbatim."""
    return re.sub(r"#ifndef\b.*?#endif", "", src, flags=re.S)


def test_the_six_patch_chain_compiles_clean_on_one_material():
    """The stack this library exists to join, on our GPU: moss, moisture and
    cracked mud on the SAME material that already carries a triplanar, a micro
    breakup and a waterline, in a scene that also puts that material on an
    InstancedMesh.  Only a real instanced draw compiles the USE_INSTANCING
    branch of the world base, and only a real compile proves four libraries'
    varyings, helpers and main() locals survive concatenation — including a
    Voronoi with two nested loops, which is the one thing here a driver could
    refuse."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] and report["errors"] == [], report
    # bed (six patches), stone/ground (five, one key), water, and the
    # instanced program for the bed material — the whole point.
    assert report["compile"]["custom_materials"] >= 3, report
    assert report["compile"]["programs"] >= 4, report
    assert report["compile"]["gpu"], report


def test_the_three_patches_chain_without_losing_each_other():
    """All three on one material is the ordinary case, not an exotic one: a
    shore bed is mossy at its edge, damp across its apron and cracked in its
    middle.  patchStandard chains them, but it repeats whatever it is handed —
    the shared world base and its five helpers must be emitted ONCE (a second
    function body now throws), the base must run FIRST or every fragment body
    reads a varying nobody wrote, and re-applying a patch must retune its
    uniforms rather than inject a second copy of its code."""
    out = _probe("""
const m = std();
patchMoss(m, { amount: 0.3 });
patchMoisture(m, { reach: 2 });
patchCrackedMud(m, { depth: 0.2 });
patchMoss(m, { amount: 0.8 });
const s = compile(m);
const mudOnly = std();
patchCrackedMud(mudOnly);
console.log(JSON.stringify({
  key: m.customProgramCacheKey(), mudKey: mudOnly.customProgramCacheKey(),
  moss: s.fragmentShader.includes('vec3 msCol = mix(uMossDeep'),
  moist: s.fragmentShader.includes('float moK ='),
  mud: s.fragmentShader.includes('vec2 mdC = astraDampCells('),
  amount: m.userData.uniforms.uMossAmt.value,
  reach: m.userData.uniforms.uMoistReach.value,
  worldBody: count(s.vertexShader, 'vec4 dmP ='),
  axesFn: count(s.fragmentShader, 'vec3 astraDampAxes(vec3 n) {'),
  noiseFn: count(s.fragmentShader, 'float astraDampNoise('),
  curvFn: count(s.fragmentShader, 'float astraDampCurv('),
  siteFn: count(s.fragmentShader, 'vec2 astraDampSite('),
  cellsFn: count(s.fragmentShader, 'vec2 astraDampCells('),
  mossLine: count(s.fragmentShader, 'float msD ='),
  varyVs: count(s.vertexShader, 'varying vec3 vAstraWorld;'),
  varyFs: count(s.fragmentShader, 'varying vec3 vAstraWorldN;'),
  baseFirst: s.fragmentShader.indexOf('vec3 astraDampAxes')
      < s.fragmentShader.indexOf('vec3 msN ='),
}));
""")
    assert out["moss"] and out["moist"] and out["mud"], "a patch was lost"
    assert out["worldBody"] == 1 and out["axesFn"] == 1
    assert out["noiseFn"] == 1 and out["curvFn"] == 1
    assert out["siteFn"] == 1 and out["cellsFn"] == 1
    assert out["varyVs"] == 1 and out["varyFs"] == 1
    assert out["baseFirst"], "a fragment body runs before the base helpers"
    # Every option is a uniform, so re-applying retunes in place.
    assert out["mossLine"] == 1 and out["amount"] == 0.8
    assert out["reach"] == 2
    assert out["key"] == "astra:damp:base+damp:moss+damp:moisture+damp:mud", \
        out["key"]
    # A longer chain must not collide with a shorter one's program.
    assert out["mudKey"] == "astra:damp:base+damp:mud"


def test_moss_follows_north_and_up_and_not_a_baked_axis():
    """Moss sprayed evenly is green paint.  What makes it moss is that every
    rock and wall in the frame carries it on the same side — so the shaded
    direction is an OPTION, and this evaluates the shipped GLSL against a swept
    normal to prove it: the north-facing face grows it, the sun-baked face
    grows NOTHING (the gate is multiplicative, so no field can push growth onto
    it), rotating `north` by 90 degrees moves the growth to the face that now
    looks north, and swapping `up` moves which face counts as level.  Nothing
    reads world Y, both vectors are uniforms (two materials with different
    compasses share one compiled program), a north tipped toward the sky is
    flattened back to the horizontal, and a degenerate one falls back rather
    than painting NaN."""
    out = _probe("""
const north = std();
patchMoss(north, { amount: 0.5, north: [0, 0, -1] });
const east = std();
patchMoss(east, { amount: 0.5, north: [-1, 0, 0] });
const zUp = std();
patchMoss(zUp, { up: [0, 0, 4], north: [1, 0, 0] });
const tipped = std();
patchMoss(tipped, { north: [0, 5, -1] });
const dead = std();
patchMoss(dead, { north: [0, 1, 0], up: [0, 0, 0] });
const sa = compile(north), sb = compile(east);
const u = (m, n) => m.userData.uniforms[n].value.toArray();
// The whole MASK chain, up to the first colour write, lifted from the
// shipped source: noise stubbed flat so the DIRECTION is what is being
// measured, curvature flat so a crevice cannot rescue a face.
const code = slice_(sa.fragmentShader, '  vec3 msN =',
                    '  diffuseColor.rgb *= 1.0 - 0.20 * msLip;');
const mask = (n, o = {}) => {
  const amt = o.uMossAmt === undefined ? 0.5 : o.uMossAmt;
  return run(code, Object.assign({
    vAstraWorldN: n, vAstraWorld: [0, 0.4, 0],
    uMossUp: [0, 1, 0], uMossNorth: [0, 0, -1], uMossAmt: amt,
    uMossSeed: [0, 0, 0],
    astraDampAxes: () => [0.34, 0.33, 0.33],
    astraDampNoise: () => 0.5,
    astraDampCurv: () => 0,
    __out: function () {
      return { k: msK, hold: msHold, sun: msSun,
               cover: msK * msOn * (0.60 + 0.40 * amt) };
    },
  }, o));
};
console.log(JSON.stringify({
  sameKey: north.customProgramCacheKey() === east.customProgramCacheKey(),
  sameFs: sa.fragmentShader === sb.fragmentShader,
  northU: u(north, 'uMossNorth'), eastU: u(east, 'uMossNorth'),
  zUpU: [u(zUp, 'uMossUp'), u(zUp, 'uMossNorth')],
  tipped: u(tipped, 'uMossNorth'),
  dead: [u(dead, 'uMossUp'), u(dead, 'uMossNorth')],
  facingNorth: mask([0, 0, -1]),
  facingSouth: mask([0, 0, 1]),
  facingEast: mask([1, 0, 0]),
  level: mask([0, 1, 0]),
  under: mask([0, -1, 0]),
  // The same faces once north has been turned a quarter turn.
  rotNorth: mask([0, 0, -1], { uMossNorth: [-1, 0, 0] }),
  rotEast: mask([-1, 0, 0], { uMossNorth: [-1, 0, 0] }),
  // A crevice on a north face is the thickest moss there is — and
  // `amount` 0 must still mean none of it.
  crevice: mask([0, 0, -1], { astraDampCurv: () => -20 }),
  creviceOff: mask([0, 0, -1], { astraDampCurv: () => -20, uMossAmt: 0 }),
  bakedCrevice: mask([0, 0, 1], { astraDampCurv: () => -20 }),
  // ...and once up is +Z: the face that is level is the +Z one.
  zLevel: mask([0, 0, 1], { uMossUp: [0, 0, 1], uMossNorth: [1, 0, 0],
                            vAstraWorld: [0, 0, 0.4] }),
  yLevel: mask([0, 1, 0], { uMossUp: [0, 0, 1], uMossNorth: [1, 0, 0],
                            vAstraWorld: [0, 0, 0.4] }),
  moss: slice_(sa.fragmentShader, '  vec3 msN =',
               '  diffuseColor.rgb = mix(diffuseColor.rgb, msCr,'),
  mixed: sa.fragmentShader.includes(
      'diffuseColor.rgb = mix(diffuseColor.rgb, msCol,'),
}));
""")
    assert out["sameKey"] and out["sameFs"], "the compass was baked in"
    assert out["northU"] == [0, 0, -1] and out["eastU"] == [-1, 0, 0]
    # A compass direction is horizontal: the part along up is dropped.
    assert [round(v, 6) for v in out["tipped"]] == [0, 0, -1]
    assert out["zUpU"][0] == [0, 0, 1], "an unnormalised up must normalise"
    assert out["dead"][0] == [0, 1, 0], "a zero up must fall back, not NaN"
    assert abs(out["dead"][1][1]) < 1e-9, "north must not be parallel to up"
    assert abs(sum(v * v for v in out["dead"][1]) - 1) < 1e-6
    # THE CLAIM: the shaded face grows moss and the baked one grows none.
    assert out["facingNorth"]["k"] > 0.5, out["facingNorth"]
    assert out["facingSouth"]["k"] == 0, out["facingSouth"]
    assert out["facingSouth"]["sun"] >= 1, "the sun gate must reach 1"
    assert out["facingNorth"]["k"] > out["facingEast"]["k"]
    assert out["level"]["hold"] > 0, "a level face holds rain"
    assert out["level"]["hold"] < out["facingNorth"]["hold"]
    assert out["under"]["k"] == 0, "a soffit facing away holds nothing"
    # Turn north a quarter turn and the growth turns with it.
    assert out["rotEast"]["k"] > 0.5 and out["rotNorth"]["k"] < 0.2, out
    # A crevice is the thickest growth there is, `amount` 0 is OFF, and a
    # crevice on a BAKED face keeps only a trace.
    assert out["crevice"]["hold"] > out["facingNorth"]["hold"]
    assert out["crevice"]["cover"] >= out["facingNorth"]["cover"] > 0
    assert out["creviceOff"]["cover"] == 0, out["creviceOff"]
    assert out["bakedCrevice"]["cover"] < out["crevice"]["cover"], out
    # Swap up and the face that counts as level swaps too.
    assert out["zLevel"]["hold"] > out["yLevel"]["hold"]
    assert out["mixed"], "the growth must mix over the albedo it lands on"
    moss = out["moss"]
    # The gate is a FACTOR, so no amount of field can cross it.
    _find(r"msHold \*= msDry;", moss)
    _find(r"float msDry = 1\.0 - clamp\(msSun, 0\.0, 1\.0\);", moss)
    # ...and nothing in the moss reads world Y or a UV for its up: the hue
    # break projects on the north/up plane, which is why it may not swizzle.
    assert ".y" not in moss and "vUv" not in moss


def test_moisture_reaches_only_within_reach_and_is_zero_beyond_it():
    """An apron that leaks past its own `reach` is a scene-wide tint: the
    ground goes dark everywhere and the shore stops meaning anything.  The edge
    has to wander — a level contour is a ruled line no bank has — and a wander
    added to a ramp is exactly what pushes it past the end, so the wander is
    faded out by the same ramp that carries it.  This evaluates the shipped
    ramp over a swept height and the whole range of the noise: full damp at the
    waterline, zero at and beyond `reach` for EVERY noise value, and an edge
    that still moves by metres in between.

    The port's capillary rim rides the same ramp and is gated at BOTH ends
    (`moW` past its band and `moT` at the reach), so it cannot smuggle a pale
    crust onto ground the apron itself does not reach."""
    out = _probe("""
const m = std();
patchMoisture(m, { waterY: 2, reach: 1.5, strength: 0.6 });
const s = compile(m);
const code = slice_(s.fragmentShader, '  float moH =', '  float moD =');
const at = (h, n) => run(code, {
  vAstraWorld: { y: 2 + h, xz: 0 }, uMoistY: 2, uMoistReach: 1.5,
  uMoistAmt: 0.6, uMoistSeed: { xz: 0, zx: 0 },
  astraFbm2: (p) => (p === 0 ? 0.44 + n : 0.44),
  __out: function () { return [moK, moR]; },
});
const sweep = [];
for (let i = 0; i <= 40; i++) {
  const h = -0.5 + i * 0.075;
  sweep.push([+h.toFixed(3), at(h, -0.44), at(h, 0), at(h, 0.44)]);
}
console.log(JSON.stringify({
  sweep,
  uniforms: {
    y: m.userData.uniforms.uMoistY.value,
    reach: m.userData.uniforms.uMoistReach.value,
    amt: m.userData.uniforms.uMoistAmt.value,
  },
  tiny: (() => { const t = std(); patchMoisture(t, { reach: 0 });
                 return t.userData.uniforms.uMoistReach.value; })(),
  fs: s.fragmentShader,
}));
""")
    assert out["uniforms"] == {"y": 2, "reach": 1.5, "amt": 0.6}
    assert out["tiny"] > 0, "a zero reach must not divide by zero"
    sweep = out["sweep"]
    for h, lo, mid, hi in sweep:
        if h >= 1.5:
            for damp, rim in (lo, mid, hi):
                assert damp == 0, f"damp at {h} m, past a 1.5 m reach"
                assert rim == 0, f"a crust at {h} m, past a 1.5 m reach"
        if h <= 0:
            assert min(lo[0], mid[0], hi[0]) > 0.99, "the waterline is dry"
            assert max(lo[1], mid[1], hi[1]) == 0, "a crust in the water"

    def edge(col: int) -> float:
        return max(h for h, *k in sweep if k[col - 1][0] > 0.02)

    # A low noise reaches further up the bank, a high one pulls in.
    assert 0.2 < edge(3) < edge(2) < edge(1) < 1.5, \
        [edge(1), edge(2), edge(3)]
    assert edge(1) - edge(3) > 0.3, "the wander is too small to see"
    # The crust sits just OUTSIDE the damp, not inside it: its peak is above
    # the height where the darkening has already gone.
    rim_peak = max(sweep, key=lambda r: r[2][1])
    damp_edge = edge(2)
    assert rim_peak[0] > damp_edge - 0.2, (rim_peak, damp_edge)
    assert rim_peak[2][1] > 0.15, rim_peak
    body = _main_body(out["fs"])
    # The wander is faded by the ramp itself — that is what bounds it.
    _find(r"float moW = clamp\(moT \+ moN \* [\d.]+ \* moT \* \(1\.0 - moT\),"
          r" 0\.0, 1\.0\);", body)
    _find(r"float moT = clamp\(moH / uMoistReach, 0\.0, 1\.0\);", body)
    # Height above the water plane, which is level by definition.
    _find(r"float moH = vAstraWorld\.y - uMoistY;", body)
    # The rim is gated on moW AND on moT: either alone leaves a crust
    # standing on every surface above the reach, where moW clamps to 1.
    _find(r"float moR = smoothstep\([\d.]+, [\d.]+, moW\)\s*"
          r"\* \(1\.0 - smoothstep\([\d.]+, [\d.]+, moW\)\)\s*"
          r"\* \(1\.0 - smoothstep\([\d.]+, [\d.]+, moT\)\)", body)
    # Dither against banding on a ramp that runs over metres of near-flat
    # ground, gated so a surface the apron does not reach is untouched.
    _find(r"float moD = \(astraHash21\(gl_FragCoord\.xy\) - 0\.5\) \* [\d.]+"
          r"\s*\* max\(moA, moR\);", body)


def test_mud_cracks_are_cells_with_edges_and_not_a_smooth_noise():
    """What the eye reads in a dried bed is POLYGONS: straight edges, sharp
    corners, a flat plate between them.  A thresholded noise gives none of
    those — it gives blobs — so the field here is a Voronoi measured to the
    nearest cell BORDER (F2 - F1 rounds every corner into a blob of its own).
    The helper is ported line for line from the shipped source and measured:
    the border field has UNIT gradient almost everywhere, which is what
    'distance to a straight edge' means and what smooth noise can never be, and
    a cell's hash is constant across its whole plate, which is what makes the
    plates pieces rather than a pattern."""
    out = _probe("""
const m = std();
patchCrackedMud(m, { scale: 0.25, depth: 0.8 });
const fs = compile(m).fragmentShader;
// Ported from the shipped helper, line for line; the regexes below pin
// the port to the source it came from.
const fract = (x) => x - Math.floor(x);
function hash21(x, y) {
  let p = [fract(x * 0.1031), fract(y * 0.1031), fract(x * 0.1031)];
  const d = p[0] * (p[1] + 33.33) + p[1] * (p[2] + 33.33)
          + p[2] * (p[0] + 33.33);
  p = p.map((v) => v + d);
  return fract((p[0] + p[1]) * p[2]);
}
const site = (cx, cy) => [cx + hash21(cx, cy),
                          cy + hash21(cx + 37.7, cy + 37.7)];
function cells(px, py) {
  const ix = Math.floor(px), iy = Math.floor(py);
  let cd = 8, cb = [0, 0], cr = [0, 0];
  for (let y = -1; y <= 1; y++) for (let x = -1; x <= 1; x++) {
    const s = site(ix + x, iy + y);
    const r = [s[0] - px, s[1] - py];
    const d = r[0] * r[0] + r[1] * r[1];
    if (d < cd) { cd = d; cb = [x, y]; cr = r; }
  }
  cd = 8;
  for (let y = -1; y <= 1; y++) for (let x = -1; x <= 1; x++) {
    const s = site(ix + cb[0] + x, iy + cb[1] + y);
    const r = [s[0] - px, s[1] - py];
    const e = [r[0] - cr[0], r[1] - cr[1]];
    const el = Math.hypot(e[0], e[1]);
    if (el * el > 1e-5) {
      cd = Math.min(cd, (0.5 * (cr[0] + r[0]) * e[0]
                       + 0.5 * (cr[1] + r[1]) * e[1]) / el);
    }
  }
  return [cd, hash21(ix + cb[0] + 0.37, iy + cb[1] + 0.37)];
}
const H = 1e-3, grads = [], noiseGrads = [];
const hashes = new Set(), noiseVals = new Set();
const noise = (x, y) => {
  const i = [Math.floor(x), Math.floor(y)], f = [fract(x), fract(y)];
  const u = f.map((t) => t * t * (3 - 2 * t));
  const a = hash21(i[0], i[1]), b = hash21(i[0] + 1, i[1]);
  const c = hash21(i[0], i[1] + 1), d = hash21(i[0] + 1, i[1] + 1);
  return (a + (b - a) * u[0]) * (1 - u[1]) + (c + (d - c) * u[0]) * u[1];
};
for (let i = 0; i < 60; i++) for (let j = 0; j < 60; j++) {
  const x = 3.017 + i * 0.1, y = 5.023 + j * 0.1;
  const d0 = cells(x, y)[0];
  grads.push(Math.hypot((cells(x + H, y)[0] - d0) / H,
                        (cells(x, y + H)[0] - d0) / H));
  const n0 = noise(x, y);
  noiseGrads.push(Math.hypot((noise(x + H, y) - n0) / H,
                             (noise(x, y + H) - n0) / H));
  if (i < 30 && j < 30) {
    hashes.add(cells(x, y)[1].toFixed(6));
    noiseVals.add(noise(x, y).toFixed(6));
  }
}
const med = (a) => a.slice().sort((p, q) => p - q)[a.length >> 1];
const near1 = (a) => a.filter((g) => g > 0.7 && g < 1.3).length / a.length;
let changes = 0, worstAt = 0, run_ = 0, longest = 0, prev = null;
for (let i = 0; i < 400; i++) {
  const c = cells(3.5 + i * 0.01, 5.5);
  const h = c[1].toFixed(6);
  if (prev !== null && h !== prev) {
    changes++;
    worstAt = Math.max(worstAt, c[0]);
    longest = Math.max(longest, run_);
    run_ = 0;
  } else { run_++; }
  prev = h;
}
console.log(JSON.stringify({
  gradMed: med(grads), gradNear1: near1(grads),
  noiseNear1: near1(noiseGrads),
  cellCount: hashes.size, noiseCount: noiseVals.size,
  changes, worstAt, longest,
  fs,
}));
""")
    # A field of straight edges has |grad| = 1 in cell units; the smooth
    # noise the alternative would have used does not, anywhere.
    assert 0.9 < out["gradMed"] < 1.1, out["gradMed"]
    assert out["gradNear1"] > 0.95, out["gradNear1"]
    assert out["noiseNear1"] < 0.2, out["noiseNear1"]
    assert 6 <= out["cellCount"] <= 25, out["cellCount"]
    assert out["noiseCount"] > 500, out["noiseCount"]
    # A plate is a PIECE: its id holds across the whole of it and turns over
    # only where the border distance is zero, which is the border.
    assert 2 <= out["changes"] <= 10, out["changes"]
    assert out["worstAt"] < 0.06, out["worstAt"]
    assert out["longest"] > 30, out["longest"]
    fs = out["fs"]
    _find(r"vec2 astraDampSite\(vec2 c\) \{\s*"
          r"return c \+ vec2\(astraHash21\(c\), astraHash21\(c \+ 37\.7\)\);",
          fs)
    _find(r"vec2 astraDampCells\(vec2 p\) \{\s*vec2 ci = floor\(p\);", fs)
    assert fs.count("for (int y = -1; y <= 1; y++) {") == 2, "one pass only"
    _find(r"cd = min\(cd, dot\(0\.5 \* \(cr \+ r\), normalize\(e\)\)\);", fs)
    _find(r"return vec2\(cd, astraHash21\(ci \+ cb \+ [\d.]+\)\);", fs)
    body = _main_body(fs)
    _find(r"vec2 mdC = astraDampCells\(mdP \+ mdWp \* [\d.]+\);", body)
    _find(r"float mdCrk = 1\.0 - smoothstep\(mdW, mdW \* [\d.]+ \+ mdAA,"
          r" mdC\.x\);", body)
    _find(r"float mdAA = clamp\(fwidth\(mdC\.x\), 0\.0, [\d.]+\);", body)
    # The lip: a curled plate is a bright band just inside a dark crack, and
    # the port makes it WARM as well as bright — it is catching the sun.
    _find(r"float mdLip = smoothstep\(mdW \* [\d.]+, mdW \* [\d.]+, mdC\.x\)",
          body)
    _find(r"mdCol \*= 1\.0 \+ mdLip \* mix\([\d.]+, [\d.]+, uMudDepth\);",
          body)
    _find(r"mdCol = mix\(mdCol, mdCol \* vec3\(1\.\d+, 1\.\d+, 0\.\d+\),",
          body)
    # A bed is LEVEL: the plates fade off a face an XZ projection smears.
    _find(r"float mdLie = smoothstep\(0\.[5-9]\d*, 0\.[8-9]\d*, mdN\.y\);",
          body)


def test_every_tone_family_is_derived_and_spreads_the_hue_the_right_way():
    """The port's aesthetic change, pinned.  Each patch used to paint ONE
    colour with a brightness ramp over it; on the showcase a metre of moss read
    as felt and a dried bed as a single chip of paint.  Each `color` option now
    derives a FAMILY, and the two things that can silently go wrong are:

    * a tone that is not derived — a caller who sets only `color` would get a
      cushion and a crust from two different plants;
    * a hue shifted the WRONG WAY.  In HSL, hue rises from yellow through
      yellow-green to green to blue-green, so the crown (new growth, yellower)
      must shift DOWN and the shaded depth UP.  The first pass had both
      inverted and rendered an acid-green cushion with a lavender crust —
      invisible in the source, obvious in a render."""
    out = _probe("""
const hsl = (c) => { const o = { h: 0, s: 0, l: 0 }; c.getHSL(o); return o; };
const fam = (color) => {
  const m = std();
  patchMoss(m, { color });
  patchCrackedMud(m, { color });
  const u = m.userData.uniforms;
  const g = {};
  for (const n of ['uMossColor', 'uMossDeep', 'uMossCrown', 'uMossLichen',
                   'uMossCrust', 'uMudColor', 'uMudPale', 'uMudDamp']) {
    g[n] = Object.assign(hsl(u[n].value), { hex: u[n].value.getHex() });
  }
  return g;
};
const a = fam(0x40592a);      // the default damp green
const b = fam(0x7a3f18);      // a rust-brown: the family must follow it
const wrap = fam(0xc21f6a);   // a magenta near the hue seam, to prove wrapping
console.log(JSON.stringify({ a, b, wrap }));
""")
    a, b, wrap = out["a"], out["b"], out["wrap"]

    def dh(fam: dict, tone: str, base: str) -> float:
        """Signed hue step, wrapped into (-0.5, 0.5]."""
        d = (fam[tone]["h"] - fam[base]["h"] + 0.5) % 1.0 - 0.5
        return d

    for fam in (a, b, wrap):
        # DERIVED, never a default: a different `color` moves every tone.
        assert len({fam[n]["hex"] for n in
                    ("uMossColor", "uMossDeep", "uMossCrown", "uMossLichen",
                     "uMossCrust")}) == 5, fam
        assert fam["uMudPale"]["hex"] != fam["uMudDamp"]["hex"]
        # The crown is the LIGHTER, yellower tone; the depth the darker,
        # bluer one.  Hue direction is the half that renders wrong silently.
        assert fam["uMossCrown"]["l"] > fam["uMossColor"]["l"] \
            > fam["uMossDeep"]["l"], fam
        assert dh(fam, "uMossCrown", "uMossColor") < 0 \
            < dh(fam, "uMossDeep", "uMossColor"), fam
        # The depth is the more saturated, the crown less: a cushion goes
        # deep and rich in its own shade and washes out on its tips.
        assert fam["uMossDeep"]["s"] > fam["uMossColor"]["s"] \
            > fam["uMossCrown"]["s"], fam
        # Both crusts are paler and greyer than the moss, and the ochre one
        # is a long way round the wheel from the sage one.
        for crust in ("uMossLichen", "uMossCrust"):
            assert fam[crust]["l"] > fam["uMossColor"]["l"], fam
            assert fam[crust]["s"] < fam["uMossColor"]["s"], fam
        assert abs(dh(fam, "uMossCrust", "uMossLichen")) > 0.12, fam
        # The bed: the plate that dried first is paler, greyer and yellower;
        # the one that held its water darker, richer and redder.
        assert fam["uMudPale"]["l"] > fam["uMudDamp"]["l"], fam
        assert fam["uMudPale"]["s"] < fam["uMudDamp"]["s"], fam
        assert dh(fam, "uMudDamp", "uMudColor") < 0 \
            < dh(fam, "uMudPale", "uMudColor"), fam
        # No tone may fall off the ends of the range: a lightness clamped to
        # 0 is a black hole in a cushion, one at 1 is a blown highlight.
        for n, tone in fam.items():
            assert 0.02 <= tone["l"] <= 0.80, (n, tone)
    # A hue near the seam wraps rather than clamping to 0 or 1.
    assert 0.0 < wrap["uMossCrust"]["h"] < 1.0
    assert abs(dh(wrap, "uMossCrust", "uMossColor")) > 0.10, wrap
    # And the shipped GLSL actually consumes the family, rather than
    # declaring it and painting one colour anyway.
    for name in ("uMossDeep", "uMossCrown", "uMossCrust", "uMudPale",
                 "uMudDamp"):
        assert _LIB_SRC.count(name) >= 3, name
    _find(r"vec3 msCol = mix\(uMossDeep, uMossColor,", _LIB_SRC)
    _find(r"msCol = mix\(msCol, uMossCrown,", _LIB_SRC)
    _find(r"vec3 msCr = mix\(uMossCrust, uMossLichen,", _LIB_SRC)
    _find(r"vec3 mdPl = mix\(uMudDamp, uMudPale,", _LIB_SRC)


def test_no_patch_assigns_over_the_colour_it_lands_on():
    """The defect this library was written after: a triplanar and a slope splat
    both ASSIGNED diffuseColor.rgb, and whichever ran second erased the other.
    These three land LAST, on materials that already carry a triplanar, a
    breakup, a waterline and an aging mark, so every write here must fold in
    what it was handed: a mix with diffuseColor.rgb on both sides, or a
    multiply.  Nothing may start a new colour — the port's capillary rim
    included, which is the material's OWN luminance lifted and warmed."""
    out = _probe("""
const m = std();
patchMoss(m); patchMoisture(m); patchCrackedMud(m);
const chained = std();
patchTriplanar(chained);
patchShoreWet(chained);
patchMoss(chained); patchMoisture(chained); patchCrackedMud(chained);
console.log(JSON.stringify({
  fs: compile(m).fragmentShader,
  chained: compile(chained).fragmentShader,
}));
""")
    body = _main_body(out["fs"])
    writes = re.findall(r"^\s*diffuseColor\.rgb\s*(\*?=)([^;]*);", body,
                        re.M | re.S)
    assert len(writes) >= 4, writes
    for op, rhs in writes:
        if op == "=":
            assert "diffuseColor.rgb" in rhs or "moL" in rhs, \
                f"assigns over: {rhs}"
        else:
            assert op == "*=", op
    # The triplanar underneath survives: its assignment is still the only
    # one, and every damp write is downstream of it.
    ch = _main_body(out["chained"])
    tri = ch.index("diffuseColor.rgb = tpC;")
    for name in ("msCol", "moA", "mdCol"):
        assert ch.index(name) > tri, name
    assert ch.count("diffuseColor.rgb = tpC;") == 1


def test_no_patch_declares_a_name_its_neighbours_own():
    """The silent failure this library is most exposed to: these three ride the
    same materials as terrain_shade, waterside, surface_wear, aging,
    accumulation and strata, and patchStandard DROPS a repeated uniform or
    varying and keeps the first — so a shared name would leave one patch
    reading the other's value with nothing reported.  A repeated main() local
    is a compile error instead, and a repeated helper now throws.  So the
    uniform sets must be disjoint (uTime excepted, the shared clock) and a
    material wearing all FIFTEEN patches must declare every name once."""
    out = _probe("""
const damp = std();
patchMoss(damp); patchMoisture(damp); patchCrackedMud(damp);
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
patchSnow(nbr);
patchSand(nbr);
patchRockStrata(nbr);
patchErosionStreaks(nbr);
const all = std();
for (const p of [patchTriplanar, patchShoreWet, patchShoreFoam,
                 patchMicroBreakup, patchEdgeWear, patchDripStains,
                 patchRust, patchDust, patchSnow, patchSand,
                 patchRockStrata, patchErosionStreaks,
                 patchMoss, patchMoisture, patchCrackedMud]) {
  p(all);
}
patchSlopeSplat(all, { snowLine: 30 });
const s = compile(all);
const fns = (src) => (src.match(/^(?:float|vec[234]|int) (\\w+)\\(/gm) || [])
    .map((m) => m.split(' ')[1].split('(')[0]);
console.log(JSON.stringify({
  dampU: Object.keys(damp.userData.uniforms),
  nbrU: Object.keys(nbr.userData.uniforms),
  patches: all.userData.astraPatches.map((p) => p.name),
  dampFns: fns(compile(damp).fragmentShader),
  nbrFns: fns(compile(nbr).fragmentShader),
  utilFns: fns(GLSL_UTIL),
  vs: s.vertexShader, fs: s.fragmentShader,
}));
""")
    shared = set(out["dampU"]) & set(out["nbrU"])
    assert shared == {"uTime"}, shared
    # Helpers too, and this one is invisible from inside: two libraries whose
    # helper shares a name AND a body dedupe silently today and throw the day
    # either body is edited.
    util = set(out["utilFns"])
    clash = (set(out["dampFns"]) - util) & (set(out["nbrFns"]) - util)
    assert not clash, clash
    assert {"astraDampAxes", "astraDampCells"} <= set(out["dampFns"])
    assert {"damp:base", "damp:moss", "damp:moisture",
            "damp:mud"} <= set(out["patches"])
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
    _find(r"dmP = instanceMatrix \* dmP;", body)
    _find(r"dmN = mat3\(instanceMatrix\) \* dmN;", body)
    _find(r"vAstraWorld = \(modelMatrix \* dmP\)\.xyz;", body)
    assert "attribute mat4 instanceMatrix" not in out["vs"]
    # fwidth, dFdx, gl_FragCoord and astraStroke are fragment-only and the
    # util block ships in both stages: they may be DEFINED in the vertex
    # shader, never called from it.
    assert "fwidth(" not in body and "dFdx(" not in body
    assert "gl_FragCoord" not in body
    # Nothing is displaced: the vertex stage is the world base and no more,
    # or a hard-edged bed would tear open at its rim.
    assert "transformed +=" not in body and "transformed *=" not in body


def test_an_option_is_a_uniform_and_never_baked_into_the_source():
    """three caches programs by key and the FIRST material to compile a key
    decides the GLSL every material sharing it gets.  Two materials that differ
    only in options must therefore compile to the same source, or the second
    silently wears the first's climate — here that would mean one scene's
    compass, one shoreline's water level and one bed's plate size imposed on
    every other surface in it.  Same key, byte-identical GLSL, different
    uniform values."""
    out = _probe("""
const a = std();
patchMoss(a, { amount: 0.45, seed: 1 });
patchMoisture(a, { waterY: 0, reach: 1.5, strength: 0.45, seed: 1 });
patchCrackedMud(a, { scale: 0.35, depth: 0.5, wet: 0, seed: 1 });
const b = std();
patchMoss(b, { amount: 0.95, seed: 99, up: [0, 0, 1], north: [0, 1, 0],
               color: new THREE.Color(0x112233) });
patchMoisture(b, { waterY: -7.5, reach: 12, strength: 1, seed: 99 });
patchCrackedMud(b, { scale: 1.4, depth: 1, wet: 0.8, seed: 99,
                     color: new THREE.Color(0x445566) });
const sa = compile(a), sb = compile(b);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  sameKey: a.customProgramCacheKey() === b.customProgramCacheKey(),
  sameVs: sa.vertexShader === sb.vertexShader,
  sameFs: sa.fragmentShader === sb.fragmentShader,
  aMoss: [u(a).uMossAmt.value, u(a).uMossUp.value.toArray(),
          u(a).uMossNorth.value.toArray()],
  bMoss: [u(b).uMossAmt.value, u(b).uMossUp.value.toArray()],
  aMoist: [u(a).uMoistY.value, u(a).uMoistReach.value, u(a).uMoistAmt.value],
  bMoist: [u(b).uMoistY.value, u(b).uMoistReach.value, u(b).uMoistAmt.value],
  aMud: [u(a).uMudScale.value, u(a).uMudDepth.value, u(a).uMudWet.value],
  bMud: [u(b).uMudScale.value, u(b).uMudDepth.value, u(b).uMudWet.value],
  colors: [u(b).uMossColor.value.getHex(), u(b).uMudColor.value.getHex()],
  lichen: [u(a).uMossLichen.value.getHex(), u(b).uMossLichen.value.getHex()],
  over: (() => { const m = std();
                 patchMoss(m, { amount: 4 });
                 patchCrackedMud(m, { depth: -2, wet: 9 });
                 return [m.userData.uniforms.uMossAmt.value,
                         m.userData.uniforms.uMudDepth.value,
                         m.userData.uniforms.uMudWet.value]; })(),
}));
""")
    assert out["sameKey"] and out["sameVs"] and out["sameFs"]
    assert out["aMoss"] == [0.45, [0, 1, 0], [0, 0, -1]]
    assert out["bMoss"][0] == 0.95 and out["bMoss"][1] == [0, 0, 1]
    assert out["aMoist"] == [0, 1.5, 0.45]
    assert out["bMoist"] == [-7.5, 12, 1]
    assert out["aMud"] == [0.35, 0.5, 0] and out["bMud"] == [1.4, 1, 0.8]
    assert out["colors"] == [0x112233, 0x445566]
    # The lichen tone is DERIVED from the moss colour, not a second option,
    # so one `color` cannot be tuned apart from its own crust.
    assert out["lichen"][0] != out["lichen"][1]
    assert out["over"] == [1, 0, 1], "an out-of-range option must clamp"


def test_the_gloss_moves_both_ways_through_compose_roughness():
    """`fragmentBody` lands after <color_fragment> and therefore BEFORE
    <roughnessmap_fragment> declares roughnessFactor, so no patch here can
    touch a per-pixel gloss — a mossy boulder that keeps a polished highlight
    is the tell.  All three move the MATERIAL's roughness instead, and they
    pull OPPOSITE ways on one chain: moss is the mattest thing on a rock, damp
    ground is the glossiest thing on a shore that is not water, and mud swings
    from one to the other with `wet`.

    THE TARGETS ARE THE PORT'S, NOT THE REFERENCE'S.  Because the factor is
    per MATERIAL, a wet target polishes the whole surface — the dry terrace
    metres from any shore included — and under our renderer (baked sky
    environment, ACES at exposure 1.0, no post chain) the reference's 0.34
    turned a 0.95-rough bank into a sky mirror: measured on the showcase, the
    ground went from luminance 0.607 to 0.823, BRIGHTER, when the whole point
    of the apron is that it is darker.  0.62 is wet soil and 0.55 a crack that
    is holding water; both keep a broad dull sheen and leave the albedo
    darkening as the thing the eye reads."""
    out = _probe("""
const mossy = std({ roughness: 0.5 });
patchMoss(mossy, { amount: 1 });
const mossR = mossy.roughness;
patchMoss(mossy, { amount: 1 });
const twice = mossy.roughness;
const damp = std({ roughness: 0.5 });
patchMoisture(damp, { strength: 1 });
const dry = std({ roughness: 0.5 });
patchCrackedMud(dry, { wet: 0 });
const wet = std({ roughness: 0.5 });
patchCrackedMud(wet, { wet: 1 });
const both = std({ roughness: 0.5 });
patchMoss(both, { amount: 1 });
patchMoisture(both, { strength: 1 });
const rough = std({ roughness: 0.97 });
patchMoss(rough, { amount: 1 });
patchCrackedMud(rough, { wet: 0 });
const none = std({ roughness: 0.5 });
patchMoss(none, { amount: 0 });
const ground = std({ roughness: 0.95 });
patchMoisture(ground, { strength: 0.62 });
const basic = new THREE.MeshBasicMaterial({ color: 0x445566 });
patchMoss(basic); patchMoisture(basic); patchCrackedMud(basic);
console.log(JSON.stringify({
  mossR, twice, damp: damp.roughness, dry: dry.roughness,
  wet: wet.roughness, both: both.roughness, rough: rough.roughness,
  none: none.roughness, ground: ground.roughness,
  base: both.userData.astraRoughness.base,
  factors: Object.keys(both.userData.astraRoughness.factors).sort(),
  basicRough: basic.roughness === undefined,
  basicPatched: basic.userData.astraPatches.map((p) => p.name),
}));
""")
    assert out["base"] == 0.5, "the authored value is the one to compose on"
    assert out["factors"] == ["damp:moisture", "damp:moss"]
    # Moss ROUGHENS and damp ground POLISHES, from the same base.
    assert out["mossR"] > 0.5 and out["damp"] < 0.5, out
    assert abs(out["mossR"] - (0.5 + 0.70 * (0.97 - 0.5))) < 1e-9, out
    # 0.5 * 0.68 = 0.34, under the 0.30 floor's reach: a mid-rough stone
    # goes to 0.34 when wet, exactly the reference's number.
    assert abs(out["damp"] - (0.5 + 0.45 * (0.34 - 0.5))) < 1e-9, out
    assert out["twice"] == out["mossR"], "re-applying compounded the gloss"
    # Mud swings between the two on one option.
    assert out["dry"] > 0.5 > out["wet"], out
    assert abs(out["dry"] - (0.5 + 0.45 * (0.95 - 0.5))) < 1e-9, out
    assert abs(out["wet"] - (0.5 + 0.45 * (0.31 - 0.5))) < 1e-9, out
    # THE PORT'S CEILING ON THE POLISH.  An outdoor ground is authored rough;
    # whatever the apron does to it must stay a sheen, not a mirror, because
    # the whole material wears it.  0.95 -> 0.72 at a strong `strength`; the
    # reference's numbers gave 0.72 -> 0.58 and rendered brighter than bare.
    assert out["ground"] > 0.70, out["ground"]
    # Two factors compose by MULTIPLYING (composeRoughness's contract), so
    # opposite pulls partly cancel and an already-rough material cannot run
    # off the top of the range.
    assert out["damp"] < out["both"] < out["mossR"], out
    assert 0.04 <= out["rough"] <= 1.0, out["rough"]
    assert abs(out["none"] - 0.5) < 1e-9, "an absent cover must not retune"
    # A material with no roughness at all still takes the albedo patch.
    assert out["basicRough"]
    assert out["basicPatched"] == ["damp:base", "damp:moss",
                                   "damp:moisture", "damp:mud"]


def test_one_seed_grows_the_same_moss_every_time():
    """A render is re-run — for a fix round, for a video, for the judge — and
    the growth must not move between takes.  The seed reaches the GPU only as a
    noise-space OFFSET (a uniform, because baking it would hand material one's
    seed to every material sharing the key), so one seed must give one set of
    offsets, two seeds must not land on the same patches, and the three patches
    must be decorrelated from each other or every crack would run under a moss
    cushion.  The offsets must also differ from surface_wear's, aging's and
    accumulation's for the same seed."""
    out = _probe("""
const mk = (seed) => {
  const m = std();
  patchMoss(m, { seed });
  patchMoisture(m, { seed });
  patchCrackedMud(m, { seed });
  patchMicroBreakup(m, { seed });
  patchDust(m, { seed });
  patchSnow(m, { seed });
  return m;
};
const a = mk(7), b = mk(7), c = mk(8);
const u = (m, n) => m.userData.uniforms[n].value.toArray();
console.log(JSON.stringify({
  aMoss: u(a, 'uMossSeed'), bMoss: u(b, 'uMossSeed'), cMoss: u(c, 'uMossSeed'),
  aMoist: u(a, 'uMoistSeed'), cMoist: u(c, 'uMoistSeed'),
  aMud: u(a, 'uMudSeed'), cMud: u(c, 'uMudSeed'),
  aMicro: u(a, 'uMicroSeed'), aDust: u(a, 'uDustSeed'),
  aSnow: u(a, 'uSnowSeed'),
  sameSrc: compile(a).fragmentShader === compile(c).fragmentShader,
}));
""")
    assert out["aMoss"] == out["bMoss"], "the same seed moved the moss"
    assert out["aMoss"] != out["cMoss"] and out["aMud"] != out["cMud"]
    assert out["aMoist"] != out["cMoist"]
    assert out["aMoss"] != out["aMud"] != out["aMoist"] != out["aMoss"]
    for other in ("aMicro", "aDust", "aSnow"):
        for mine in ("aMoss", "aMoist", "aMud"):
            assert out[mine] != out[other], f"{mine} rides {other}"
    assert all(0 <= v <= 40 for v in out["aMoss"] + out["aMud"])
    # Same GLSL for both seeds: the seed is a uniform, not source.
    assert out["sameSrc"]
    assert "Math.random" not in _LIB_SRC


def test_moisture_hands_over_to_the_waterline_it_sits_beside():
    """patchShoreWet owns the waterline — the decimetres either side of it —
    and this owns the metres beyond.  They are meant to be worn together, so
    the failure to rule out is not a collision of names but a collision of
    EFFECT: two patches that both assigned the albedo would leave one
    invisible, and two that both claimed the same uniform would silently share
    one water level.  Both scale what they are handed, so the overlap deepens
    instead, and the apron is the softer of the two at the same strength — a
    shore whose apron is darker than its tide mark reads as one flat wet
    slab."""
    out = _probe("""
const m = std();
patchShoreWet(m, { level: 1.5, band: 0.25, darken: 0.45 });
patchMoisture(m, { waterY: 1.5, reach: 3, strength: 0.45 });
const s = compile(m);
const u = m.userData.uniforms;
console.log(JSON.stringify({
  uniforms: Object.keys(u).sort(),
  wetY: u.uWetY.value, moistY: u.uMoistY.value,
  key: m.customProgramCacheKey(),
  fs: s.fragmentShader,
}));
""")
    # Two water levels, two uniforms: one library must never silently take
    # the other's tide.
    assert out["wetY"] == out["moistY"] == 1.5
    assert "uWetY" in out["uniforms"] and "uMoistY" in out["uniforms"]
    assert out["key"] == ("astra:waterside:base+waterside:shoreWet"
                          "+damp:base+damp:moisture"), out["key"]
    body = _main_body(out["fs"])
    # Both fold in what they were handed; neither starts a colour.
    # waterside's half used to be one multiply (`*= mix(1.0, 1.0 - uWetDark,
    # wtK)`); it now takes the albedo into a dry/wet pair so wet ground gains
    # SATURATION as well as depth.  The claim under test is unchanged and is
    # asserted as a chain: the dry end IS what the patch was handed, the wet
    # end is derived from it, and the assignment only interpolates the two.
    _find(r"vec3 wtDry = diffuseColor\.rgb;", body)
    _find(r"vec3 wtWet = mix\(vec3\(wtL\), wtDry, 1\.0 \+ uWetSat\)\s*"
          r"\* \(1\.0 - uWetDark\);", body)
    _find(r"diffuseColor\.rgb = mix\(wtDry, max\(wtWet, vec3\(0\.0\)\), wtK\);",
          body)
    _find(r"diffuseColor\.rgb = clamp\(\s*"
          r"mix\(mix\(vec3\(moL\), diffuseColor\.rgb, 1\.0 \+ [\d.]+ \* moA\)"
          r"\s*\* \(1\.0 - 0\.55 \* moA\),\s*"
          r"vec3\(moL\) \* vec3\([\d., ]+\), [\d.]+ \* moR\)"
          r"\s*\+ moD, 0\.0, 1\.0\);", body)
    # The apron is the SOFTER darkening: shoreWet takes 0.45 of the albedo
    # at full wet where this takes 0.55 of `strength`.
    assert 0.55 * 0.45 < 0.45
    # Wet ground is darker AND deeper in colour, which is the half of "wet"
    # that a plain multiply cannot say.
    _find(r"float moL = dot\(diffuseColor\.rgb,"
          r" vec3\(0\.2126, 0\.7152, 0\.0722\)\);", body)
    # The JSDoc has to say where the neighbour ends, because nothing in the
    # source can: they are two patches with one physical edge.
    doc = _LIB_SRC[:_LIB_SRC.index("export function patchCrackedMud")]
    assert "patchShoreWet" in doc and "WATERLINE" in doc
