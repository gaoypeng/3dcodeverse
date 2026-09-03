"""strata.js: the bedding and the rain runs, and the chain they land in.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_strata_lib.py).  Their renderer-contract assertions are dropped;
the geology claims, the shared-name contract and the option-is-a-uniform law
are kept, and the two things this port added are pinned so a later edit
cannot quietly undo them:

* the palette is solved against a MEASURED light.  `sunRig`'s fill plus a
  blue-sky environment puts linear (1, 1.56, 2.45) on a shaded vertical
  face — a neutral grey albedo renders (88, 110, 134) there — so every bed
  and every stain tone has to beat that ratio or the cliff comes back
  cyan-green, which is what the reference palette did on our host;
* every mark varies in HUE, not only in value: the bed grain, the foot of a
  bed, the parting and the two minerals in a run.

Both patches exist to go ON TOP of a cliff that already wears
`patchTriplanar` / `patchSlopeSplat` and usually `patchMicroBreakup`, so the
shared contract is asserted first — one world base, no name a neighbour owns,
every option a uniform rather than baked GLSL — and then the geology.  Two
claims are checked by NUMBERS rather than by reading the source: the bed
stack is walked with the shipped uniforms and the constants pulled out of the
compiled GLSL, because "thickness varies" and "two cliffs share one bedding
plane" are statements about a sequence of metres and a regex sees neither.
"""
from __future__ import annotations

import json
import re
from collections import Counter

import pytest
from _probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "terrain_shade.js", "waterside.js", "surface_wear.js",
         "aging.js", "strata.js")

_LIB_SRC = (LIB_DIR / "strata.js").read_text(encoding="utf-8")

# The patch only exists inside onBeforeCompile, so every probe hands it
# the two chunks patchStandard replaces and reads back what it wrote.
_PRELUDE = """
import * as THREE from 'three';
import { patchRockStrata, patchErosionStreaks } from './lib/strata.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
import { patchShoreWet, patchShoreFoam } from './lib/waterside.js';
import { patchDripStains, patchRust, patchDust } from './lib/aging.js';

const std = (o = {}) => new THREE.MeshStandardMaterial(
    Object.assign({ color: 0x8a8071, roughness: 0.9 }, o));

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

# GLSL_UTIL's noise, transliterated once so a probe can WALK the bed
# stack the shader builds.  Every constant that shapes it is read out of
# the compiled source instead of being repeated here.
_STACK = """
const fr = (x) => x - Math.floor(x);
function hash21(x, y) {
  let a = fr(x * 0.1031), b = fr(y * 0.1031), c = fr(x * 0.1031);
  const d = a * (b + 33.33) + b * (c + 33.33) + c * (a + 33.33);
  a += d; b += d; c += d;
  return fr((a + b) * c);
}
function noise2(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y);
  let fx = x - ix, fy = y - iy;
  fx = fx * fx * (3 - 2 * fx); fy = fy * fy * (3 - 2 * fy);
  const a = hash21(ix, iy), b = hash21(ix + 1, iy);
  const c = hash21(ix, iy + 1), d = hash21(ix + 1, iy + 1);
  return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy;
}
function fbm(x, y) {
  let s = 0, a = 0.5;
  for (let i = 0; i < 3; i++) {
    s += a * noise2(x, y); x *= 2.02; y *= 2.02; a *= 0.5;
  }
  return Math.min(1, Math.max(0, s / 0.875));
}
const grab = (src, re) => {
  const m = re.exec(src);
  if (!m) throw new Error('not in the shipped GLSL: ' + re);
  return m.slice(1).map(Number);
};

/** The bed the shader would pick at a world point, from ITS numbers. */
function bedder(mat) {
  const fs = compile(mat).fragmentShader;
  const [lat] = grab(fs, /astraStrataFbm\\(vAstraWorld\\.xz \\* ([\\d.]+)\\s*\\+ uStrataSeed\\.xz\\) - 0\\.5\\) \\* uStrataJit/);
  const [wf, wa] = grab(fs, /astraStrataFbm\\(vec2\\(stB \\* ([\\d.]+), uStrataSeed\\.y\\)\\) - 0\\.5\\)\\s*\\* ([\\d.]+)/);
  const [off] = grab(fs, /astraStrataFloor\\(float k, float s\\) \\{\\s*return k \\+ \\(astraHash21\\(vec2\\(k, s\\)\\) - 0\\.5\\) \\* ([\\d.]+);/);
  const u = mat.userData.uniforms;
  const up = u.uStrataUp.value, sp = u.uStrataSpacing.value;
  const jit = u.uStrataJit.value, sd = u.uStrataSeed.value;
  const bedFloor = (k) => k + (hash21(k, sd.z) - 0.5) * off;
  return (x, y, z) => {
    let b = (x * up.x + y * up.y + z * up.z) / sp;
    b += (fbm(x * lat + sd.x, z * lat + sd.z) - 0.5) * jit;
    b += (fbm(b * wf, sd.y) - 0.5) * wa;
    let i = Math.floor(b), lo = bedFloor(i);
    if (b < lo) { i -= 1; lo = bedFloor(i); }
    let hi = bedFloor(i + 1);
    if (b >= hi) { i += 1; lo = hi; hi = bedFloor(i + 1); }
    return { b, i, lo, hi };
  };
}

/** Altitudes at which a vertical line at (x, z) crosses a contact. */
function contacts(mat, x, z, top = 60) {
  const at = bedder(mat);
  const out = [];
  let prev = at(x, 0, z).i;
  for (let y = 0; y <= top; y += 0.002) {
    const i = at(x, y, z).i;
    if (i !== prev) { out.push(Math.round(y * 1000) / 1000); prev = i; }
  }
  return out;
}

/** sRGB hex -> the linear triple three uploads for it. */
function lin(hex) {
  return [16, 8, 0].map((s) => {
    const v = ((hex >> s) & 255) / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
}

// What a shaded vertical face gets from this library's own day rig
// (`sunRig`: hemisphere 0x9db8e8 over warm ground + a blue-sky
// environment), measured on our renderer by rendering a neutral 0x808080
// albedo through the showcase host: it came back (88, 110, 134), i.e.
// linear (1, 1.557, 2.445) relative to red.
const SHADE_LIGHT = [1.0, 1.557, 2.445];
const shaded = (c) => [0, 1, 2].map((i) => c[i] * SHADE_LIGHT[i]);
"""

# A butte with a talus, benches whose treads catch the sun, an overhang
# and its ledge; a boulder and a flat-shaded fallen slab at its foot; and
# scree on an InstancedMesh wearing the SAME material as the cliff, which
# is the only way the USE_INSTANCING branch of the world base is compiled.
_SCENE = """
import * as THREE from 'three';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
import { patchMicroBreakup } from './lib/surface_wear.js';
import { patchRockStrata, patchErosionStreaks } from './lib/strata.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 12, 20] };

export function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  scene.add(new THREE.HemisphereLight(0x9db8e8, 0x8a7f6a, 1.4));
  const sun = new THREE.DirectionalLight(0xfff0d8, 5.4);
  sun.position.set(-6, 6, -4);
  sun.castShadow = true;
  scene.add(sun);

  const g = new THREE.Group();
  const rock = new THREE.MeshStandardMaterial({
    color: 0x8a8071, roughness: 0.92,
  });
  const slab = new THREE.MeshStandardMaterial({
    color: 0x7d766b, roughness: 0.95, flatShading: true,
  });
  const prof = [[4.30, 0.00], [3.35, 1.05], [3.28, 2.25], [2.55, 2.48],
                [2.48, 4.10], [1.98, 4.32], [1.92, 6.20], [1.52, 6.42],
                [1.46, 7.30], [0, 7.36]]
      .map(([x, y]) => new THREE.Vector2(x, y));
  const butte = new THREE.Mesh(new THREE.LatheGeometry(prof, 96), rock);
  butte.castShadow = butte.receiveShadow = true;
  g.add(butte);

  const boulder = new THREE.Mesh(new THREE.IcosahedronGeometry(0.85, 3), rock);
  boulder.position.set(3.5, 0.62, 1.9);
  boulder.castShadow = boulder.receiveShadow = true;
  g.add(boulder);

  // Unwelded: curvature reads ZERO on it, so the run field alone carries
  // the streaks there.
  const fallen = new THREE.Mesh(new THREE.BoxGeometry(2.6, 0.55, 1.9), slab);
  fallen.position.set(-3.2, 0.30, 2.7);
  fallen.rotation.set(0.08, 0.7, 0.16);
  fallen.castShadow = fallen.receiveShadow = true;
  g.add(fallen);

  const scree = new THREE.InstancedMesh(
      new THREE.IcosahedronGeometry(0.22, 1), rock, 24);
  const m = new THREE.Matrix4();
  let s = 11;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 24; i++) {
    const a = rand() * Math.PI * 2, rr = 3.7 + rand() * 1.9;
    m.compose(
        new THREE.Vector3(Math.cos(a) * rr,
                          Math.max(0.09, 1.05 - rr * 0.22),
                          Math.sin(a) * rr),
        new THREE.Quaternion().setFromEuler(
            new THREE.Euler(rand() * 3, rand() * 3, rand() * 3)),
        new THREE.Vector3(1, 0.7 + rand() * 0.6, 1));
    scree.setMatrixAt(i, m);
  }
  scree.instanceMatrix.needsUpdate = true;
  scree.frustumCulled = false;
  scree.castShadow = true;
  g.add(scree);

  for (const [mat, seed] of [[rock, 2], [slab, 5]]) {
    patchTriplanar(mat, { scale: 1.6 });
    patchSlopeSplat(mat, { slopeLow: 0.78, slopeHigh: 0.5 });
    patchRockStrata(mat, { spacing: 0.42, tilt: 9, contrast: 0.72, seed: 2 });
    patchErosionStreaks(mat, { strength: 0.5, scale: 0.45, seed: 2 });
    patchMicroBreakup(mat, { seed });
  }
  scene.add(g);
  return {
    scene,
    cameras: [{ name: 'hero', position: [14, 6, 18], lookAt: [0, 2, 0],
                fov: 45 }],
    update() {},
  };
}
"""


def _probe(body: str) -> dict:
    return measure(_PRELUDE + _STACK + body, _LIBS)


def _find(pattern: str, src: str) -> re.Match:
    m = re.search(pattern, src)
    assert m, f"{pattern} not in\n{src}"
    return m


def _main_body(src: str) -> str:
    return src[src.index("void main"):]


def _unguarded(src: str) -> str:
    """Drop `#ifndef X ... #endif` blocks, which may repeat verbatim."""
    return re.sub(r"#ifndef\b.*?#endif", "", src, flags=re.S)


def test_the_five_patch_chain_compiles_on_one_material():
    """The stack this library exists to join, on our GPU: beds and runs on
    the SAME material that already carries `patchTriplanar`,
    `patchSlopeSplat` and `patchMicroBreakup`, in a scene that also wears it
    on an InstancedMesh and on a flat-shaded box.  Only a real instanced
    program compiles the USE_INSTANCING branch of the world base, only a
    flat-shaded mesh takes the curvature path where the normal derivative is
    zero, and only a real compile proves three libraries' varyings, helpers
    and main() locals survive being concatenated into one shader."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] and report["errors"] == [], report
    # Two patched built-ins, and more programs than that: the cliff
    # material compiled twice (once per instancing state) and every caster
    # compiled a depth program as well.
    assert report["compile"]["custom_materials"] >= 2, report
    assert report["compile"]["programs"] >= 3, report
    assert report["compile"]["gpu"], report


def test_the_two_patches_chain_without_losing_each_other():
    """Beds and runs on one material is the ordinary case — a cliff that is
    bedded but never streaked is half a cliff.  patchStandard chains them,
    but it repeats whatever it is handed: the shared world base and its
    helpers must be emitted ONCE (a second function body is a compile
    error), the base must run FIRST or every fragment body reads a varying
    nobody wrote, and re-applying a patch has to retune its uniforms instead
    of injecting a second copy of its code."""
    out = _probe("""
const m = std();
patchRockStrata(m, { spacing: 2 });
patchErosionStreaks(m, { strength: 0.2 });
patchRockStrata(m, { spacing: 1.4, contrast: 0.5 });
const s = compile(m);
const bedsOnly = std();
patchRockStrata(bedsOnly);
console.log(JSON.stringify({
  key: m.customProgramCacheKey(), bedKey: bedsOnly.customProgramCacheKey(),
  beds: s.fragmentShader.includes('stCol, uStrataAmt'),
  runs: s.fragmentShader.includes('erCol, erAmt'),
  spacing: m.userData.uniforms.uStrataSpacing.value,
  contrast: m.userData.uniforms.uStrataAmt.value,
  erosion: m.userData.uniforms.uErosAmt.value,
  worldBody: count(s.vertexShader, 'vec4 stbP ='),
  fbmFn: count(s.fragmentShader, 'float astraStrataFbm('),
  floorFn: count(s.fragmentShader, 'float astraStrataFloor('),
  n3Fn: count(s.fragmentShader, 'float astraStrataN3('),
  runFn: count(s.fragmentShader, 'float astraStrataRun('),
  bendFn: count(s.fragmentShader, 'float astraStrataBend('),
  toneFn: count(s.fragmentShader, 'vec3 astraStrataTone('),
  pickFn: count(s.fragmentShader, 'float astraStrataPick('),
  bedLine: count(s.fragmentShader, 'float stTh ='),
  varyVs: count(s.vertexShader, 'varying vec3 vAstraWorld;'),
  varyFs: count(s.fragmentShader, 'varying vec3 vAstraWorldN;'),
}));
""")
    assert out["beds"] and out["runs"], "a patch was lost"
    assert out["worldBody"] == 1 and out["fbmFn"] == 1 and out["n3Fn"] == 1
    assert out["floorFn"] == 1 and out["runFn"] == 1 and out["bendFn"] == 1
    assert out["toneFn"] == 1 and out["pickFn"] == 1
    assert out["varyVs"] == 1 and out["varyFs"] == 1
    # Every option is a uniform, so re-applying retunes in place.
    assert out["bedLine"] == 1 and out["spacing"] == 1.4
    assert out["contrast"] == 0.5 and out["erosion"] == 0.2
    assert out["key"] == "astra:strata:base+strata:beds+strata:erosion"
    # A longer chain must not collide with a shorter one's program.
    assert out["bedKey"] == "astra:strata:base+strata:beds"


def test_the_beds_ride_world_altitude_so_two_cliffs_share_one_stack():
    """The claim the whole patch is for.  Beds are checked by the eye
    against each other, so the bed at 12 m on this face has to be the bed at
    12 m on the one across the valley — which means the coordinate is
    `dot(worldPosition, beddingUp)` with no UV, no object space and nothing
    a mesh knows in it.  Walked numerically at two places 140 m apart on two
    SEPARATE materials: with the surfaces level the contacts land at the
    same altitudes to the millimetre, and with the default undulation they
    stay within the jitter they were given rather than drifting apart."""
    out = _probe("""
const flatA = std(), flatB = std();
patchRockStrata(flatA, { jitter: 0, tilt: 0 });
patchRockStrata(flatB, { jitter: 0, tilt: 0, colors: [0x223344] });
const wavy = std();
patchRockStrata(wavy, { tilt: 0 });
console.log(JSON.stringify({
  here: contacts(flatA, 6, 3),
  faraway: contacts(flatB, -83, 112),
  wavyHere: contacts(wavy, 6, 3),
  wavyFar: contacts(wavy, -83, 112),
  jitter: wavy.userData.uniforms.uStrataJit.value,
  spacing: wavy.userData.uniforms.uStrataSpacing.value,
  fs: compile(flatA).fragmentShader,
}));
""")
    assert len(out["here"]) > 40
    # One stack for the whole world: a different material, a different
    # palette and a point 140 m away still cut the same contacts.
    assert out["here"] == out["faraway"]
    # With the surfaces undulating they wander, but by the jitter they were
    # given (in beds) and not by a bed or more.
    wander = [abs(a - b) for a, b in zip(out["wavyHere"], out["wavyFar"], strict=False)]
    limit = out["jitter"] * out["spacing"]
    assert max(wander) <= limit, (max(wander), limit)
    assert max(wander) > 0.01, "the surfaces are not undulating at all"
    body = _main_body(out["fs"])
    _find(r"float stB = dot\(vAstraWorld, normalize\(uStrataUp\)\)\s*"
          r"/ uStrataSpacing;", body)
    assert "vUv" not in body and " uv" not in body


def test_the_base_folds_the_instance_transform_into_the_world_point():
    """A scattered boulder is an InstancedMesh, and `vertexBody` lands after
    <begin_vertex> and therefore BEFORE <project_vertex>: nothing has applied
    `instanceMatrix` yet.  Left alone, every copy of a scattered rock reads
    the bedding at the mesh origin and the whole field wears one band."""
    out = _probe("""
const m = std();
patchRockStrata(m);
patchErosionStreaks(m);
console.log(JSON.stringify({ vs: compile(m).vertexShader }));
""")
    body = _main_body(out["vs"])
    assert "#ifdef USE_INSTANCING" in body
    _find(r"stbP = instanceMatrix \* stbP;", body)
    _find(r"stbN = mat3\(instanceMatrix\) \* stbN;", body)
    _find(r"vAstraWorld = \(modelMatrix \* stbP\)\.xyz;", body)
    # fwidth, dFdx and astraStroke are fragment-only, and the util block
    # ships in both stages: they may be DEFINED in the vertex shader, never
    # called.
    assert "fwidth(" not in body and "dFdx(" not in body


def test_tilt_tips_the_beds_off_horizontal():
    """Level beds read as a stripe texture; beds that dip a few degrees read
    as geology, because they then cut ACROSS the topography instead of
    following it.  So `tilt` has to reach the shader as a real bedding
    NORMAL — a uniform, so two cliffs with different dips still share one
    program — the dip has to show up as a change of bed along the GROUND,
    and an absurd dip has to be clamped rather than flipping the stack."""
    out = _probe("""
const level = std(), dipped = std(), silly = std();
patchRockStrata(level, { tilt: 0, jitter: 0 });
patchRockStrata(dipped, { tilt: 12, jitter: 0 });
patchRockStrata(silly, { tilt: 900, jitter: 0 });
const atL = bedder(level), atD = bedder(dipped);
console.log(JSON.stringify({
  sameKey: level.customProgramCacheKey() === dipped.customProgramCacheKey(),
  sameFs: compile(level).fragmentShader === compile(dipped).fragmentShader,
  level: level.userData.uniforms.uStrataUp.value.toArray(),
  dipped: dipped.userData.uniforms.uStrataUp.value.toArray(),
  silly: silly.userData.uniforms.uStrataUp.value.toArray(),
  levelRun: [atL(0, 9, 0).b, atL(40, 9, 0).b, atL(0, 9, 40).b],
  dippedRun: [atD(0, 9, 0).b, atD(40, 9, 0).b, atD(0, 9, 40).b],
}));
""")
    assert out["sameKey"] and out["sameFs"], "the dip was baked in"
    assert out["level"] == [0, 1, 0], "level beds must be level"
    dip = out["dipped"]
    assert abs(dip[1] - 0.9781) < 1e-3, dip           # cos(12 deg)
    assert abs((dip[0] ** 2 + dip[2] ** 2) ** 0.5 - 0.2079) < 1e-3, dip
    # A dip past vertical would turn the stack over; it is clamped.
    assert out["silly"][1] > 0.08, out["silly"]
    # Level beds are the same bed all across the ground at one altitude;
    # dipped ones are not, and by the dip they were given.
    assert out["levelRun"][0] == out["levelRun"][1] == out["levelRun"][2]
    moved = [abs(v - out["dippedRun"][0]) for v in out["dippedRun"][1:]]
    assert max(moved) > 2.0, moved


def test_bed_thickness_varies_rather_than_repeating_at_one_period():
    """A repeating stripe of one width is wallpaper, and it is the tell that
    a shader drew it.  So the stack is walked with the shipped uniforms and
    the constants read out of the compiled GLSL: an average bed has to come
    out at `spacing`, the thinnest and the thickest have to differ by more
    than 3x, and no lag may repeat — a stack alternating two widths would
    pass a spread test and still read as a pattern."""
    out = _probe("""
const m = std();
patchRockStrata(m, { spacing: 0.8, tilt: 0, jitter: 0 });
const wide = std();
patchRockStrata(wide, { spacing: 2.5, tilt: 0, jitter: 0 });
console.log(JSON.stringify({
  contacts: contacts(m, 6, 3),
  wideContacts: contacts(wide, 6, 3),
}));
""")
    th = [round(b - a, 4) for a, b in zip(out["contacts"], out["contacts"][1:], strict=False)]
    assert len(th) > 40, len(th)
    mean = sum(th) / len(th)
    assert abs(mean - 0.8) < 0.12, mean
    assert max(th) / min(th) > 3.0, (min(th), max(th))
    spread = (sum((v - mean) ** 2 for v in th) / len(th)) ** 0.5 / mean
    assert spread > 0.2, spread
    # Nothing repeats: at every short lag the sequence still disagrees with
    # itself by a fifth of a bed on average.
    for lag in range(1, 9):
        pairs = list(zip(th, th[lag:], strict=False))
        drift = sum(abs(a - b) for a, b in pairs) / len(pairs)
        assert drift > 0.2 * mean, (lag, drift)
    # `spacing` is the AVERAGE, and it is a uniform: the same sequence
    # scales with it rather than being re-rolled.
    wide = [round(b - a, 4)
            for a, b in zip(out["wideContacts"], out["wideContacts"][1:],
                            strict=False)]
    assert abs(sum(wide) / len(wide) - 2.5) < 0.4


def test_the_runs_go_down_the_fall_line_and_not_down_world_y():
    """What separates this from a drip stain.  On a vertical face the two
    agree; on a slope they do not, and a run that keeps going down world Y
    there cuts across the contours, which no water does.  The direction is
    gravity with the surface normal taken out, and the LENGTH of that same
    vector is the sine of the slope, so one expression gives both the way
    the water goes and the gate that keeps it off level ground.  The field
    is then smeared along it — taps upslope, weaker each time, which is what
    gives a run a head at the top and a tail at the bottom."""
    out = _probe("""
const m = std();
patchErosionStreaks(m, { scale: 0.9 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    body = _main_body(fs)
    # Gravity projected into the surface: g - n * dot(g, n), which for
    # g = -Y is exactly this.
    _find(r"vec3 erG = vec3\(0\.0, -1\.0, 0\.0\) \+ erN \* erN\.y;", body)
    _find(r"float erSin = length\(erG\);", body)
    _find(r"vec3 erD = erG / max\(erSin, 1e-4\);", body)
    # The field is smeared along THAT direction, never along world Y — and
    # long enough to read as a track: at 5.5 run widths the runs came out on
    # our host as round blotches, so the smear is pinned at 7 or more with
    # at least five taps, and the fibre smear that tears the edge is
    # weighted hard enough to actually break it.
    m = _find(r"float erA = astraStrataRun\(erP, erD, ([\d.]+), (\d)\);", body)
    assert float(m.group(1)) >= 7.0 and int(m.group(2)) >= 5, m.groups()
    m = _find(r"float erFib = astraStrataRun\(erP \* ([\d.]+), erD,"
              r" [\d.]+, \d\);", body)
    assert float(m.group(1)) >= 3.0, m.group(1)
    m = _find(r"float erF = erA \+ \(erFib - 0\.5\) \* ([\d.]+);", body)
    assert float(m.group(1)) >= 0.35, m.group(1)
    assert "vec3(0.0, 1.0, 0.0)" not in body
    assert "vAstraWorld.y" not in body, "world Y is not the flow axis"
    # The smear: taps UPSLOPE (-d) with a decaying weight, unevenly spaced
    # so they cannot all land on one lattice phase.
    _find(r"float astraStrataRun\(vec3 p, vec3 d, float len, int taps\) \{\s*"
          r"float s = astraStrataN3\(p\), w = 1\.0, a = 1\.0, o = 0\.0;\s*"
          r"for \(int i = 0; i < 6; i\+\+\) \{\s*"
          r"if \(i >= taps\) break;\s*"
          r"o \+= len \* \(([\d.]+) \+ ([\d.]+) \* float\(i\)\);\s*"
          r"a \*= (0\.\d+); w \+= a;\s*"
          r"s \+= a \* astraStrataN3\(p - d \* o\);", fs)
    # Only a face that can shed takes a run: nothing on the level, and
    # nothing on a soffit, which drips clear instead of tracking.
    _find(r"float erFlow = smoothstep\([\d.]+, [\d.]+, erSin\)\s*"
          r"\* \(1\.0 - smoothstep\([\d.]+, [\d.]+, -erN\.y\)\);", body)
    _find(r"clamp\(erK \* erFlow \* uErosAmt, 0\.0, 1\.0\)", body)


def test_the_runs_start_where_water_collects_rather_than_everywhere():
    """Runs sprayed evenly over a face are a texture.  Water collects, and
    the only thing a fragment can see of that is the way the surface bends
    under it — so curvature is split by DIRECTION: concave across the flow
    is a gully and holds its thread the whole way down, convex along the
    flow is a lip and pours what it caught over, convex across is a rib that
    sheds sideways and stays clean.  Both halves are needed because a bump
    is convex in both and means neither.  The response is band-limited in
    run widths, or the pixel-scale wrinkle on any noisy mesh reads as a
    gully; and a source lowers the field's THRESHOLD rather than dimming
    it, so clean rock keeps a few strong runs."""
    out = _probe("""
const m = std();
patchErosionStreaks(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    body = _main_body(fs)
    # Curvature ALONG the flow, and the total minus it, which by Euler is
    # the curvature across it.  Both in run widths.
    _find(r"float erAl = astraStrataBend\(erN, vAstraWorld, erD\)"
          r" \* uErosScale;", body)
    _find(r"float erAc = astraStrataCurv\(erN, vAstraWorld\) \* uErosScale\s*"
          r"- erAl;", body)
    _find(r"float erSrc = clamp\(([\d.]+) \+ ([\d.]+)"
          r" \* astraStrataBand\(-erAc\)\s*"
          r"\+ ([\d.]+) \* astraStrataBand\(erAl\)\s*"
          r"- ([\d.]+) \* astraStrataBand\(erAc\), 0\.0, 1\.0\);", body)
    # The band: a bend the size of a run counts, a wrinkle does not.
    m = _find(r"float astraStrataBand\(float k\) \{\s*"
              r"return smoothstep\(([\d.]+), ([\d.]+), k\)"
              r" \* \(1\.0 - smoothstep\(([\d.]+), ([\d.]+), k\)\);", fs)
    lo, hi, off0, off1 = (float(v) for v in m.groups())
    assert lo < hi <= off0 < off1, m.groups()
    assert off1 <= 8.0, "a pixel-scale wrinkle would still read as a gully"
    # The source moves the threshold, and downward: more source, more of the
    # field passes.
    t = _find(r"float erT = mix\(([\d.]+), ([\d.]+), erSrc\);", body)
    assert float(t.group(2)) < float(t.group(1)), t.groups()
    # Two levels on one field, kept as SEPARATE terms because the port
    # tints them differently, both widened by the field's own gradient.
    _find(r"float erAA = clamp\(fwidth\(erF\), 0\.0, [\d.]+\);", body)
    _find(r"float erBroad = smoothstep\(erT - [\d.]+ - erAA,\s*"
          r"erT \+ [\d.]+ \+ erAA, erF\);", body)
    _find(r"float erCore = smoothstep\(erT \+ [\d.]+ - erAA,\s*"
          r"erT \+ [\d.]+ \+ erAA, erF\);", body)
    _find(r"float erK = [\d.]+ \* erBroad \+ [\d.]+ \* erCore;", body)
    # Curvature is the surface's own derivative, so it reads ZERO across a
    # hard unwelded edge — the field has to carry those alone.
    _find(r"float astraStrataCurv\(vec3 n, vec3 p\) \{\s*"
          r"vec3 dx = dFdx\(p\), dy = dFdy\(p\);", fs)


def test_a_contact_and_a_seam_stop_short_of_aliasing():
    """Bedding is the one structure here with a PERIOD, which is what
    aliases: at 60 m a pixel spans several beds, and a hard tone step per
    bed would crawl into moire (MSAA never sees a shader's own edge).  Three
    defences, all keyed to the pixel footprint of the bed rather than to
    distance: the contact between two beds is resolved over the pixel it
    covers, the parting rides `astraStroke`, which fades a stroke out once a
    pixel spans more than the stroke itself, and past a pixel per bed the
    whole stack dissolves into its own mean tone — so a far cliff keeps its
    rock colour instead of reverting to whatever was underneath."""
    out = _probe("""
const m = std();
patchRockStrata(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    body = _main_body(fs)
    # The footprint is measured in BEDS, so a thin bed defends itself sooner
    # than a thick one.
    _find(r"float stAA = clamp\(fwidth\(stB\) / stTh, 1e-4, [\d.]+\);", body)
    _find(r"0\.5 - 0\.5 \* smoothstep\(0\.0, stAA,"
          r" min\(stF, 1\.0 - stF\)\)\);", body)
    _find(r"stCol = mix\(stCol, stAvg,"
          r" smoothstep\([\d.]+, [\d.]+, stAA\)\);", body)
    _find(r"astraStroke\(stP \+ 0\.5, mix\([\d.]+, [\d.]+, stS\)\)", body)
    # The stroke's argument has to be CONTINUOUS across the contact or its
    # own fwidth reads the jump and kills the seam there.
    _find(r"float stP = stI \+ stF;", body)
    assert "astraStroke" in fs


def test_every_option_is_a_uniform_and_never_baked_into_the_source():
    """three caches programs by key and the FIRST material to compile a key
    decides the GLSL every material sharing it gets.  Two materials that
    differ only in options must therefore compile to the same source, or the
    second silently wears the first's geology — one cliff's bed spacing, dip
    and palette imposed on every other cliff in the scene.  Same key,
    byte-identical GLSL, different uniform values; and a palette of any
    length has to land in the same four."""
    out = _probe("""
const a = std();
patchRockStrata(a, { spacing: 0.8, tilt: 7, contrast: 0.7, jitter: 0.35,
                     seed: 1 });
patchErosionStreaks(a, { strength: 0.45, scale: 0.8, seed: 1 });
const b = std();
patchRockStrata(b, { spacing: 4.5, tilt: -21, contrast: 0.2, jitter: 1.4,
                     seed: 77, colors: [0x112233, 0x445566] });
patchErosionStreaks(b, { strength: 0.9, scale: 3.2, seed: 77,
                         color: new THREE.Color(0x778899) });
const one = std();
patchRockStrata(one, { colors: [0x203040] });
const many = std();
patchRockStrata(many, { colors: [0x101010, 0x202020, 0x303030, 0x404040,
                                 0x505050, 0x606060] });
const sa = compile(a), sb = compile(b);
const u = (m) => m.userData.uniforms;
const pal = (m) => [0, 1, 2, 3].map(
    (i) => u(m)['uStrataC' + i].value.getHex());
console.log(JSON.stringify({
  sameKey: a.customProgramCacheKey() === b.customProgramCacheKey(),
  sameVs: sa.vertexShader === sb.vertexShader,
  sameFs: sa.fragmentShader === sb.fragmentShader,
  aBeds: [u(a).uStrataSpacing.value, u(a).uStrataAmt.value,
          u(a).uStrataJit.value],
  bBeds: [u(b).uStrataSpacing.value, u(b).uStrataAmt.value,
          u(b).uStrataJit.value],
  aEros: [u(a).uErosAmt.value, u(a).uErosScale.value],
  bEros: [u(b).uErosAmt.value, u(b).uErosScale.value],
  bDip: u(b).uStrataUp.value.y,
  bPal: pal(b), onePal: pal(one), manyPal: pal(many),
  erosColor: u(b).uErosColor.value.getHex(),
}));
""")
    assert out["sameKey"] and out["sameVs"] and out["sameFs"]
    assert out["aBeds"] == [0.8, 0.7, 0.35]
    assert out["bBeds"] == [4.5, 0.2, 1.4]
    assert out["aEros"] == [0.45, 0.8] and out["bEros"] == [0.9, 3.2]
    assert abs(out["bDip"] - 0.9336) < 1e-3, out["bDip"]   # cos(21 deg)
    assert out["erosColor"] == 0x778899
    # Two colours are resampled across the four, not repeated.
    assert out["bPal"][0] == 0x112233 and out["bPal"][3] == 0x445566
    assert len(set(out["bPal"])) == 4
    assert set(out["onePal"]) == {0x203040}
    assert out["manyPal"][0] == 0x101010 and out["manyPal"][3] == 0x606060


def test_no_patch_declares_a_name_its_neighbours_own():
    """The silent failure this library is most exposed to: a cliff wears
    terrain_shade and surface_wear as a matter of course, and often aging
    and waterside too, and patchStandard DROPS a repeated uniform or varying
    and keeps the first — so a shared name would leave one patch reading the
    other's value with nothing reported.  A repeated local inside main is a
    compile error instead, and a repeated helper with a different body
    throws by name.  So the uniform sets must be disjoint (uTime excepted,
    the shared clock), and a material wearing all eleven patches must
    declare every name exactly once."""
    out = _probe("""
const mine = std();
patchRockStrata(mine);
patchErosionStreaks(mine);
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
patchRockStrata(all);
patchErosionStreaks(all);
const s = compile(all);
console.log(JSON.stringify({
  mineU: Object.keys(mine.userData.uniforms),
  nbrU: Object.keys(nbr.userData.uniforms),
  vs: s.vertexShader, fs: s.fragmentShader,
}));
""")
    shared = set(out["mineU"]) & set(out["nbrU"])
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
    # Every helper this library adds is prefixed, since a neighbour defining
    # the same name with a different body throws.
    mine = re.findall(r"^(?:float|vec[234])\s+(\w+)\s*\(",
                      _LIB_SRC.replace("  '", ""), re.M)
    assert all(n.startswith("astraStrata") for n in mine), mine


def test_one_seed_lays_the_same_geology_every_time():
    """A render is re-run — for a fix round, for a video, for the judge —
    and the rock must not move between takes.  The seed reaches the GPU only
    as a noise-space OFFSET (a uniform, because baking it would hand
    material one's seed to every material sharing the key), so one seed must
    give one offset, two seeds must not land on the same beds, the beds and
    the runs must be decorrelated from each other, and both must differ from
    aging's and surface_wear's for the same seed or the runs would ride that
    library's stains."""
    out = _probe("""
const mk = (seed) => {
  const m = std();
  patchRockStrata(m, { seed });
  patchErosionStreaks(m, { seed });
  patchDripStains(m, { seed });
  patchMicroBreakup(m, { seed });
  return m;
};
const a = mk(7), b = mk(7), c = mk(8);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  aBeds: u(a).uStrataSeed.value.toArray(),
  bBeds: u(b).uStrataSeed.value.toArray(),
  cBeds: u(c).uStrataSeed.value.toArray(),
  aEros: u(a).uErosSeed.value.toArray(),
  cEros: u(c).uErosSeed.value.toArray(),
  aDrip: u(a).uDripSeed.value.toArray(),
  aMicro: u(a).uMicroSeed.value.toArray(),
  aUp: u(a).uStrataUp.value.toArray(),
  cUp: u(c).uStrataUp.value.toArray(),
  sameSrc: compile(a).fragmentShader === compile(c).fragmentShader,
  contactsA: contacts(a, 4, 4, 20),
  contactsB: contacts(b, 4, 4, 20),
  contactsC: contacts(c, 4, 4, 20),
}));
""")
    assert out["aBeds"] == out["bBeds"], "the same seed moved the beds"
    assert out["aBeds"] != out["cBeds"] and out["aEros"] != out["cEros"]
    assert out["aBeds"] != out["aEros"], "the runs ride the bed noise"
    assert out["aBeds"] != out["aDrip"] and out["aBeds"] != out["aMicro"]
    assert all(0 <= v <= 52 for v in out["aBeds"] + out["aEros"])
    # The dip DIRECTION is seeded too, so one scene keeps one dip.
    assert out["aUp"] != out["cUp"]
    assert abs(out["aUp"][1] - out["cUp"][1]) < 1e-9, "the dip angle moved"
    # Same GLSL for both seeds: the seed is a uniform, not source.
    assert out["sameSrc"]
    assert out["contactsA"] == out["contactsB"]
    assert out["contactsA"] != out["contactsC"]
    assert "Math.random" not in _LIB_SRC


def test_the_wash_polishes_the_material_once_however_often_it_lands():
    """`fragmentBody` lands after <color_fragment> and therefore BEFORE
    <roughnessmap_fragment> declares roughnessFactor, so no patch here can
    touch a per-pixel gloss.  A face with water tracks is a face that gets
    wet, so the runs take a small polish off the material — through
    composeRoughness, so a refine round that re-runs the same build cannot
    compound it into a mirror, and a neighbour's matte factor still composes
    with it.  Beds are not a finish and must not touch it at all."""
    out = _probe("""
const m = std({ roughness: 0.8 });
patchRockStrata(m, { contrast: 1 });
const beds = m.roughness;
patchErosionStreaks(m, { strength: 1 });
const r1 = m.roughness;
patchErosionStreaks(m, { strength: 1 });
const r2 = m.roughness;
patchErosionStreaks(m, { strength: 0 });
const r3 = m.roughness;
const basic = new THREE.MeshBasicMaterial({ color: 0x445566 });
patchRockStrata(basic);
patchErosionStreaks(basic);
console.log(JSON.stringify({
  beds, r1, r2, r3, base: m.userData.astraRoughness.base,
  factors: Object.keys(m.userData.astraRoughness.factors).sort(),
  basicRough: basic.roughness === undefined,
  basicPatched: basic.userData.astraPatches.length,
}));
""")
    assert out["beds"] == 0.8, "bedding is albedo, not finish"
    assert out["base"] == 0.8, "the authored value is the one to compose on"
    assert out["factors"] == ["strata:erosion"]
    assert out["r1"] < 0.8, "a washed face is not as matte as a dry one"
    assert out["r2"] == out["r1"], "re-applying compounded the polish"
    assert abs(out["r3"] - 0.8) < 1e-9
    # A material with no roughness at all still takes the albedo patches.
    assert out["basicRough"] and out["basicPatched"] == 3


def test_the_palette_survives_this_renderer_s_blue_sky_fill():
    """The port's own colour law, and the reason the palette changed.

    Most of a cliff is in its own shade, and `sunRig`'s day fill plus a
    blue-sky environment put linear (1, 1.56, 2.45) on a shaded vertical
    face here — measured by rendering a neutral 0x808080 albedo through the
    showcase host, which came back (88, 110, 134).  A bed whose albedo does
    not beat that ratio does not read as a cooler ROCK, it reads as a
    cyan-green band of some other material: the reference palette's four
    tones all landed at linear G/R around 0.8, well past the 0.64 the light
    allows, which is exactly how the cliff rendered.  So every shipped bed
    tone, and the default stain and both minerals derived from it, are
    solved against that measurement — and they are spread by SATURATION
    rather than toward yellow, because a bed that reaches G/R = 1 in the
    render is one grain-multiply away from green."""
    out = _probe("""
const beds = [];
const m = std();
patchRockStrata(m);
const u = m.userData.uniforms;
for (let i = 0; i < 4; i++) beds.push(u['uStrataC' + i].value.toArray());
const e = std();
patchErosionStreaks(e);
console.log(JSON.stringify({
  beds: beds.map(shaded),
  bedLin: beds,
  eros: shaded(e.userData.uniforms.uErosColor.value.toArray()),
  fs: compile(m).fragmentShader,
  efs: compile(e).fragmentShader,
}));
""")
    # `THREE.Color.toArray` is already the LINEAR working-space triple, so
    # these are the numbers the GPU multiplies.
    for i, (r, g, b) in enumerate(out["beds"]):
        assert g < r and b < r, f"bed {i} renders {(r, g, b)}: not rock"
        # Warm, but never so warm the bed reads as painted terracotta.
        assert 0.55 <= g / r <= 0.95, (i, g / r)
        assert 0.30 <= b / r <= 0.85, (i, b / r)
    r, g, b = out["eros"]
    assert g < r and b < r, f"the default stain renders {(r, g, b)}"
    # The two minerals a run can carry are BOTH kept off neutral, or a dark
    # stain under a blue sky comes back as a slate-blue patch.
    for name in ("erIron", "erMang"):
        m = re.search(
            rf"vec3 {name} = uErosColor \* vec3\(([\d.]+), ([\d.]+),"
            rf" ([\d.]+)\);", out["efs"])
        assert m, name
        mr, mg, mb = (float(v) for v in m.groups())
        assert (r * mr) > (g * mg) and (r * mr) > (b * mb), (name, m.groups())
    # Albedo stays inside the linear range a lit surface can hold: nothing
    # near black (which the sky's own specular then paints blue) and nothing
    # so bright it blows on a sunlit ledge.
    lum = [0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
           for c in out["bedLin"]]
    assert min(lum) > 0.02, lum
    assert max(lum) < 0.8, lum
    # ...and a real spread, or the stack has no beds in it.
    assert max(lum) / min(lum) > 3.0, lum


def test_every_mark_varies_in_hue_and_not_only_in_value():
    """The aesthetic contract this port added, pinned so an edit cannot
    flatten it back.  A bed multiplied by a scalar grain is ONE colour over
    a whole face, which is what makes a shader stack read as printed
    stripes; the reference did that for the grain, for the foot of a bed and
    for the parting, and replaced the albedo outright inside a run.  Here:
    the grain is a vec3 warm-to-bleached, the foot of a bed goes warm, the
    parting keeps its hue and a floor instead of multiplying toward black,
    and a run DARKENS the rock under it (so the bedding still shows through)
    before being tinted toward one of two minerals."""
    out = _probe("""
const m = std();
patchRockStrata(m);
patchErosionStreaks(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    body = _main_body(out["fs"])
    # The grain is a colour, warm at one end and bleached at the other, and
    # the bleached end must not be cool enough to tip a bed into green.
    m = _find(r"stCol \*= mix\(vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),"
              r" vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),\s*stG\);", body)
    wr, wg, wb, cr, cg, cb = (float(v) for v in m.groups())
    assert wr > wg > wb, ("the warm end is not warm", m.groups())
    assert cb / cr < 1.10, ("the bleached end is cool enough to go green",
                            m.groups())
    # A second, finer mottle, so the metre-scale grain is not the smallest
    # thing on the rock at arm's length.
    _find(r"float stG2 = astraStrataFbm\(vAstraWorld\.xz \* [\d.]+", body)
    # The foot of a bed is warm, not just dark.
    m = _find(r"stCol \*= mix\(vec3\(1\.0\), vec3\(([\d.]+), ([\d.]+),"
              r" ([\d.]+)\),\s*1\.0 - smoothstep", body)
    fr, fg, fb = (float(v) for v in m.groups())
    assert fr > fg > fb, m.groups()
    # The parting fades along the bed rather than being a ruled line, and it
    # lands on a floored, hue-keeping mix instead of a multiply to black.
    _find(r"float stSeam = astraStroke\([\s\S]*?\* mix\([\d.]+, 1\.0, stG\);",
          body)
    m = _find(r"stCol = mix\(stCol,\s*stCol \* vec3\([\d.]+, [\d.]+, [\d.]+\)"
              r"\s*\+ vec3\(([\d.]+), ([\d.]+), ([\d.]+)\), [\d.]+ \* stSeam\);",
              body)
    assert all(float(v) > 0 for v in m.groups()), "the seam can reach black"
    # A run darkens what is under it before it tints, so the beds keep
    # showing through the runs the way the module's header promises.
    _find(r"vec3 erWet = diffuseColor\.rgb \* mix\([\d.]+, [\d.]+, erA\);",
          body)
    _find(r"vec3 erCol = mix\(erWet, erTint \* mix\([\d.]+, [\d.]+, erA\),"
          r" [\d.]+\);", body)
    # ...and the mineral it carries is chosen by a field far COARSER than
    # the run, so one run is one mineral the whole way down.
    m = _find(r"float erMin = astraStrataN3\(erP \* ([\d.]+)", body)
    assert float(m.group(1)) < 0.5, m.group(1)
    _find(r"vec3 erTint = mix\(erIron, erMang,\s*"
          r"erCore \* mix\([\d.]+, [\d.]+, erMin\)\);", body)
