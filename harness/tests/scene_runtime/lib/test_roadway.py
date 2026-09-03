"""roadway.js: the road's own frame, and the chain it lands in.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_roadway_lib.py).  Their claims are kept as they stood — one road
frame shared by three patches, ruts along the ROAD's own axis and not a world
one, whole lanes so the wheel strips land on the road, a seam band bounded by
the width it was given, a track count and kind that are uniforms, nobody
assigning over the albedo they landed on, one seed one road, and every field
finer than its pixel faded out before it can alias.

THE PORT'S OWN LAW, and the regression this file exists to stop:

A ROAD MAY NOT BE POLISHED INTO A SKY MIRROR.  `fragmentBody` lands after
<color_fragment> and therefore before <roughnessmap_fragment> declares
roughnessFactor, so the one gloss move these patches have is the MATERIAL's —
which makes it an area-weighted average, not the polished rut's own value.
The reference spent the rut's full polish on the whole surface (-0.26 * wear,
-0.18 * gutter, -0.22 * depth).  On our renderer that is not a wet look, it is
a white card: no post chain, a bright sky environment as the specular source,
and a road is nearly always seen at a grazing angle where a smooth dielectric
is almost all Fresnel.  Measured on the showcase host (fx/out/roadway,
2026-09-01, tarmac authored at roughness 0.85): the old factors composed it to
0.63 and the carriageway read (171,172,175) with mean luminance 0.68 — every
repair, wheel strip and drip line the patch drew was washed out of it.  The
area-weighted factors here compose the same material to 0.86 and it reads
(136,138,144) at 0.54, with the repairs, the pale wheel strips and the dark
drip line all back.  Half this file's new assertions are that bound.
"""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from tests.scene_runtime.lib._probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "materials.js", "terrain_shade.js",
         "surface_wear.js", "aging.js", "accumulation.js", "strata.js",
         "roadway.js")

_LIB_SRC = (LIB_DIR / "roadway.js").read_text(encoding="utf-8")

# The patch only exists inside onBeforeCompile, so every probe hands it the
# two chunks patchStandard replaces and reads back what it wrote.
_PRELUDE = """
import * as THREE from 'three';
import { patchRoadSurface, patchSeamBand, patchTracks }
    from './lib/roadway.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { patchTriplanar, patchSlopeSplat } from './lib/terrain_shade.js';
import { patchDripStains, patchRust, patchDust } from './lib/aging.js';
import { patchSnow, patchSand } from './lib/accumulation.js';
import { patchRockStrata, patchErosionStreaks } from './lib/strata.js';

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

# The showcase scene, as a compile fixture: a two-lane road TURNED off the
# world axes (a rut that ran down +Z would still look right on a road laid
# along +Z), its kerb stones INSTANCED on the road's own material — the only
# thing that compiles the USE_INSTANCING branch of the frame — and a mud verge
# and a footpath beside it that share one program because `kind` and `offset`
# are uniforms.
_SCENE = """
import * as THREE from 'three';
import { patchTriplanar } from './lib/terrain_shade.js';
import { patchMicroBreakup, patchEdgeWear } from './lib/surface_wear.js';
import { patchRoadSurface, patchSeamBand, patchTracks }
    from './lib/roadway.js';

const HALF = 3.5;
const LEN = 110;

function roadGeo() {
  const g = new THREE.PlaneGeometry(HALF * 2, LEN, 40, 200);
  g.rotateX(-Math.PI / 2);
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const t = p.getX(i) / HALF;
    p.setY(i, 0.26 - 0.085 * t * t + Math.sin(p.getZ(i) * 0.13) * 0.02);
  }
  g.computeVertexNormals();
  return g;
}

function slab(x0, x1, top) {
  const g = new THREE.BoxGeometry(x1 - x0, top, LEN);
  g.translate((x0 + x1) / 2, top / 2, 0);
  return g;
}

function build() {
  const root = new THREE.Group();
  const g = new THREE.Group();
  g.rotation.y = 38 * Math.PI / 180;
  root.add(g);

  const tar = new THREE.MeshStandardMaterial(
      { color: 0x585856, roughness: 0.85, name: 'Tarmac' });
  const verge = new THREE.MeshStandardMaterial(
      { color: 0x5d6b34, roughness: 0.95, name: 'Verge' });
  const mud = new THREE.MeshStandardMaterial(
      { color: 0x5b4a35, roughness: 0.92, name: 'Mud' });
  const path = new THREE.MeshStandardMaterial(
      { color: 0x6b5a42, roughness: 0.93, name: 'Path' });

  const add = (geo, mat) => { g.add(new THREE.Mesh(geo, mat)); };
  add(roadGeo(), tar);
  add(slab(-HALF, HALF, 0.17), tar);
  add(slab(-15, -HALF - 0.04, 0.30), verge);
  add(slab(HALF + 0.02, 6.4, 0.20), mud);
  add(slab(6.4, 15, 0.28), verge);
  add(slab(7.2, 8.6, 0.30), path);

  const kerb = new THREE.InstancedMesh(
      new THREE.BoxGeometry(0.16, 0.40, 0.88), tar, 40);
  const mx = new THREE.Matrix4();
  for (let i = 0; i < 40; i++) {
    mx.makeTranslation(-HALF - 0.10, 0.19, -18 + i * 0.92);
    kerb.setMatrixAt(i, mx);
  }
  kerb.instanceMatrix.needsUpdate = true;
  kerb.frustumCulled = false;
  g.add(kerb);

  patchTriplanar(tar, { scale: 1.4, colorA: 0x44443f, colorB: 0x6a6a63 });
  patchMicroBreakup(tar, { seed: 3, strength: 0.10 });
  patchEdgeWear(tar, { seed: 3, strength: 0.25, width: 0.30 });
  patchRoadSurface(tar, {
    aggregate: 0.30, wear: 0.75, patches: 0.35, gutter: 0.70,
    color: 0x4c4c4e, halfWidth: HALF, lane: 3.4, center: [0, 0, 0], seed: 4,
  });
  patchSeamBand(tar, { width: 0.75, weeds: 0.5, seed: 4 });
  patchTracks(tar, { count: 2, depth: 0.32, kind: 'tyre', seed: 4 });
  patchSeamBand(verge, {
    width: 1.1, weeds: 0.85, halfWidth: HALF + 0.14, center: [0, 0, 0],
    seed: 6,
  });
  patchTracks(mud, {
    count: 2, depth: 0.85, kind: 'tyre', gauge: 1.55, offset: 4.9,
    halfWidth: HALF, center: [0, 0, 0], seed: 8,
  });
  patchTracks(path, {
    count: 1, depth: 0.8, kind: 'foot', offset: 7.9,
    halfWidth: HALF, center: [0, 0, 0], seed: 9,
  });
  return root;
}

export async function createScene() {
  const scene = new THREE.Scene();
  const sun = new THREE.DirectionalLight(0xfff0d8, 3.0);
  sun.position.set(-6, 9, -4);
  scene.add(sun);
  scene.add(new THREE.HemisphereLight(0xbfd4ee, 0x6a5a44, 0.6));
  scene.add(build());
  const cameras = [
    { name: 'road', position: [14, 6, 18], lookAt: [0, 2, 0], fov: 45 },
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


def test_the_whole_road_stack_compiles_on_our_gpu():
    """The stack this library exists to join, on a real GPU: the road surface,
    its seam band and its tracks on the SAME material that already carries a
    triplanar, a micro breakup and an edge wear, in a scene that also puts
    that material on an InstancedMesh.  Only a real instanced draw compiles
    the USE_INSTANCING branch of the frame, and only a real compile proves
    three libraries' varyings, helpers and main() locals survive being
    concatenated into one program."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
    assert "ERROR" not in out, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["errors"] == [], report
    assert report["compile"]["gpu"], "this claim is only worth a real GPU"
    # The mud and the path differ only by `kind` and `offset`, so they SHARE
    # one cache key and one program — the whole reason those are uniforms.
    # The extra program is the instanced kerb on the road's own material.
    assert report["compile"]["custom_materials"] >= 4, report
    assert report["compile"]["programs"] >= 4, report


def test_the_three_patches_chain_without_losing_each_other():
    """A road wears all three at once — the surface, the gravel where it
    stops, and what drove over it — and patchStandard repeats whatever it is
    handed: the shared frame and its three helpers must be emitted ONCE (a
    second function body throws), the frame must run FIRST or every fragment
    body reads a varying nobody wrote, and re-applying a patch has to retune
    its uniforms rather than inject a second copy of its code."""
    out = _probe("""
const m = std();
patchRoadSurface(m, { wear: 0.2 });
patchSeamBand(m, { width: 0.4 });
patchTracks(m, { count: 3 });
patchRoadSurface(m, { wear: 0.9 });
const s = compile(m);
const seamOnly = std();
patchSeamBand(seamOnly);
console.log(JSON.stringify({
  key: m.customProgramCacheKey(), seamKey: seamOnly.customProgramCacheKey(),
  road: s.fragmentShader.includes('diffuseColor.rgb = roCol;'),
  seam: s.fragmentShader.includes('diffuseColor.rgb = mix(diffuseColor.rgb,'
                                  + ' smCol, smK * 0.88);'),
  track: s.fragmentShader.includes('diffuseColor.rgb = mix(diffuseColor.rgb,'
                                   + ' tkCol,'),
  wear: m.userData.uniforms.uRoadWear.value,
  frameBody: count(s.vertexShader, 'vec4 rwP ='),
  axesFn: count(s.fragmentShader, 'vec3 astraRoadAxes(vec3 n) {'),
  noiseFn: count(s.fragmentShader, 'float astraRoadNoise('),
  fadeFn: count(s.fragmentShader, 'float astraRoadFade('),
  roadLine: count(s.fragmentShader, 'float roRut ='),
  varyVs: count(s.vertexShader, 'varying vec4 vAstraRoad;'),
  varyFs: count(s.fragmentShader, 'varying vec4 vAstraRoad;'),
  frameFirst: s.fragmentShader.indexOf('vec3 astraRoadAxes')
      < s.fragmentShader.indexOf('float roU ='),
}));
""")
    assert out["road"] and out["seam"] and out["track"], "a patch was lost"
    assert out["frameBody"] == 1 and out["axesFn"] == 1
    assert out["noiseFn"] == 1 and out["fadeFn"] == 1
    assert out["varyVs"] == 1 and out["varyFs"] == 1
    assert out["frameFirst"], "a fragment body runs before the frame helpers"
    # Every option is a uniform, so re-applying retunes in place.
    assert out["roadLine"] == 1 and out["wear"] == 0.9
    assert out["key"] == ("astra:road:frame+road:surface+road:seam"
                          "+road:tracks"), out["key"]
    # A longer chain must not collide with a shorter one's program.
    assert out["seamKey"] == "astra:road:frame+road:seam"


def test_the_ruts_run_along_the_roads_own_direction_not_a_world_axis():
    """The one claim that makes this a roadway library.  A rut is a line of
    constant ACROSS, so ruts drawn on a world axis stay put while the road
    turns away from them — and every road in these scenes is laid at some
    angle, or curves.  So the direction is the mesh's OWN axis carried through
    modelMatrix (and instanceMatrix, or every copy of a segment kit takes the
    mesh origin's road), across is the horizontal perpendicular measured from
    the mesh's own origin, and NOTHING in the fragment stage reads world .x or
    .z.  `dir` is a uniform, so two roads at two angles still share one
    compiled program."""
    out = _probe("""
const a = std();
patchRoadSurface(a, { wear: 0.8 });
const b = std();
patchRoadSurface(b, { wear: 0.8, dir: [1, 0, 0] });
const arr = std();
patchRoadSurface(arr, { dir: new THREE.Vector3(0, 0, 9) });
const dead = std();
patchRoadSurface(dead, { dir: [0, 0, 0] });
const sa = compile(a), sb = compile(b);
const u = (m, n) => m.userData.uniforms[n].value.toArray();
console.log(JSON.stringify({
  sameKey: a.customProgramCacheKey() === b.customProgramCacheKey(),
  sameFs: sa.fragmentShader === sb.fragmentShader,
  sameVs: sa.vertexShader === sb.vertexShader,
  dirA: u(a, 'uRoadDir'), dirB: u(b, 'uRoadDir'),
  unit: u(arr, 'uRoadDir'), dead: u(dead, 'uRoadDir'),
  vs: sa.vertexShader, fs: sa.fragmentShader,
}));
""")
    assert out["sameKey"] and out["sameFs"] and out["sameVs"], \
        "the direction was baked into the source"
    assert out["dirA"] == [0, 0, 1] and out["dirB"] == [1, 0, 0]
    assert out["unit"] == [0, 0, 1], "an unnormalised dir must be normalised"
    assert out["dead"] == [0, 0, 1], "a zero dir must fall back, not NaN"
    vbody = _main_body(out["vs"])
    _find(r"vec4 rwD = vec4\(uRoadDir, 0\.0\);", vbody)
    _find(r"vec3 rwA = normalize\(\(modelMatrix \* rwD\)\.xyz\);", vbody)
    _find(r"rwD = mix\(instanceMatrix \* rwD, rwD, uRoadCenter\.w\);", vbody)
    _find(r"rwO = mix\(instanceMatrix \* rwO, rwO, uRoadCenter\.w\);", vbody)
    assert "#ifdef USE_INSTANCING" in vbody
    assert "attribute mat4 instanceMatrix" not in out["vs"]
    _find(r"vec3 rwX = cross\(vec3\(0\.0, 1\.0, 0\.0\), rwA\);", vbody)
    _find(r"vAstraRoad = vec4\(rwA, dot\(vAstraWorld - rwC, rwX\)\);", vbody)
    body = _main_body(out["fs"])
    # THE CLAIM: along is a projection on that axis and the rut is a line of
    # constant across.  Neither is a world coordinate.
    _find(r"float roU = dot\(vAstraWorld, vAstraRoad\.xyz\);", body)
    _find(r"float roRut = \(1\.0 - smoothstep\([\d.]+, [\d.]+,\s*"
          r"abs\(abs\(vAstraRoad\.w - roCen\) - roTr \+ roWob\)\)\)\s*"
          r"\* uRoadWear;", body)
    # The wheel lines wander along the road, or the pair reads as rails.
    _find(r"float roWob = \(astraNoise2\(vec2\(roU \* [\d.]+, [\d.]+\)"
          r" \+ uRoadSeed\.yz\)\s*- 0\.5\) \* [\d.]+;", body)
    for axis in ("vAstraWorld.x", "vAstraWorld.z", "vAstraWorld.y"):
        assert axis not in body, f"{axis} — the frame is not the world axes"
    assert "vUv" not in body


def test_the_road_is_divided_into_whole_lanes_so_the_ruts_land_inside():
    """A road is worn in TWO STRIPS PER LANE — that asymmetry is the whole cue
    — so the wheel lines have to land where wheels go: a single-track lane
    keeps ONE pair near its middle, a two-lane road gets four strips, and no
    rut may sit off the edge of the surface."""
    out = _probe("""
const m = std();
patchRoadSurface(m, { halfWidth: 2.1, lane: 2.2, wear: 0.9 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader,
                             half: m.userData.uniforms.uRoadHalf.value,
                             lane: m.userData.uniforms.uRoadLane.value }));
""")
    body = _main_body(out["fs"])
    _find(r"float roN = max\(floor\(2\.0 \* uRoadHalf"
          r" / max\(uRoadLane, [\d.]+\)\), 1\.0\);", body)
    _find(r"float roLw = 2\.0 \* uRoadHalf / roN;", body)
    _find(r"float roK = clamp\(floor\(\(vAstraRoad\.w \+ uRoadHalf\)"
          r" / roLw\),\s*0\.0, roN - 1\.0\);", body)
    _find(r"float roCen = \(roK \+ 0\.5\) \* roLw - uRoadHalf;", body)
    track = _find(r"float roTr = min\(([\d.]+), roLw \* ([\d.]+)\);", body)
    assert 0.6 <= float(track.group(1)) <= 0.9, "a car's half-track is ~0.75 m"
    assert float(track.group(2)) < 0.5, "both wheels must fit in the lane"

    # The same arithmetic, run here: every rut inside the road, and a narrow
    # lane keeping one pair rather than a phantom second one.
    def ruts(half, lane):
        n = max(1.0, (2 * half) // max(lane, 0.5))
        width = 2 * half / n
        tr = min(0.75, width * 0.32)
        out_ = []
        for k in range(int(n)):
            c = (k + 0.5) * width - half
            out_ += [round(c - tr, 3), round(c + tr, 3)]
        return out_
    assert ruts(2.1, 2.2) == [-0.75, 0.75], "a single lane keeps one pair"
    assert ruts(3.5, 3.4) == [-2.5, -1.0, 1.0, 2.5], "two lanes, four strips"
    for half, lane in ((2.1, 2.2), (3.5, 3.4), (5.4, 3.4), (1.2, 3.4)):
        assert all(abs(r) < half for r in ruts(half, lane)), (half, lane)


def test_the_seam_band_is_bounded_by_the_width_it_was_given():
    """A gravel band that wanders is right and one that wanders OUT of the
    width it was given is not: a scene that placed a kerb, a verge or a puddle
    against it needs the number it passed to mean something.  So the
    raggedness is in the band's HALF-WIDTH, never in its position, and the
    hairline of shadow in the joint fades out by its own pixel size."""
    out = _probe("""
const m = std();
patchSeamBand(m, { width: 0.8, weeds: 0.6, halfWidth: 3.0 });
const thin = std();
patchSeamBand(thin, { width: 0 });
console.log(JSON.stringify({
  fs: compile(m).fragmentShader,
  width: m.userData.uniforms.uSeamWidth.value,
  degenerate: thin.userData.uniforms.uSeamWidth.value,
}));
""")
    assert out["width"] == 0.8
    assert out["degenerate"] > 0, "a zero width must not divide by zero"
    body = _main_body(out["fs"])
    _find(r"float smD = abs\(vAstraRoad\.w\) - uRoadHalf;", body)
    _find(r"float smH = uSeamWidth \* 0\.5;", body)
    rag = _find(r"float smR = mix\(([\d.]+), ([\d.]+), astraNoise2\(", body)
    lo, hi = float(rag.group(1)), float(rag.group(2))
    assert 0 < lo < hi <= 1.0, "the wander must not widen the band"
    mask = _find(r"float smK = 1\.0 - smoothstep\(smR \* smH \* ([\d.]+),"
                 r" smR \* smH,\s*abs\(smD\)\);", body)
    assert float(mask.group(1)) < 1, "the band needs a soft inner edge"
    # THE BOUND: the outer smoothstep edge is smR * smH <= smH, so the mask is
    # 0 for |smD| >= width / 2 for every value the noise takes.
    assert "smoothstep(smR * smH * " in body
    # Weeds take the joint and the ground side of it, never the tarmac.
    _find(r"float smOut = smoothstep\(-smH \* [\d.]+, smH \* [\d.]+,"
          r" smD\);", body)
    _find(r"\* mix\([\d.]+, 1\.0, smOut\) \* uSeamWeeds;", body)
    _find(r"float smJt = \(1\.0 - smoothstep\(0\.0, smH \* [\d.]+,"
          r" abs\(smD\)\)\)\s*\* astraRoadFade\(vAstraWorld,"
          r" smH \* [\d.]+\);", body)


def test_tracks_follow_the_direction_and_the_count_they_were_given():
    """`count` tracks means count tracks: one walker leaves one trail, a
    vehicle leaves two, a twin-axle rig four — and they run along the road
    frame's direction, spaced by a gauge, so a pair is a vehicle rather than a
    railway.  The count and the gauge are UNIFORMS (the loop is bounded by a
    constant and breaks on the count), the count is clamped to what the loop
    can reach, and `offset` moves the whole group across the frame so tracks
    and a seam band can share one material."""
    out = _probe("""
const mk = (o) => { const m = std(); patchTracks(m, o); return m; };
const two = mk({ count: 2 });
const four = mk({ count: 4, gauge: 0.9 });
const many = mk({ count: 40 });
const none = mk({ count: 0 });
const half = mk({ count: 2.6 });
const foot = mk({ kind: 'foot' });
const off = mk({ count: 2, offset: 4.85 });
const u = (m, n) => m.userData.uniforms[n].value;
console.log(JSON.stringify({
  sameKey: two.customProgramCacheKey() === four.customProgramCacheKey(),
  sameFs: compile(two).fragmentShader === compile(four).fragmentShader,
  counts: [u(two, 'uTrkCount'), u(four, 'uTrkCount'), u(many, 'uTrkCount'),
           u(none, 'uTrkCount'), u(half, 'uTrkCount')],
  gauges: [u(two, 'uTrkGauge'), u(four, 'uTrkGauge'), u(foot, 'uTrkGauge')],
  offsets: [u(two, 'uTrkOffset'), u(off, 'uTrkOffset')],
  fs: compile(two).fragmentShader,
}));
""")
    assert out["sameKey"] and out["sameFs"], "the count was baked in"
    assert out["counts"] == [2, 4, 8, 1, 3], out["counts"]
    # A walker's feet are 30 cm apart and a car's wheels 1.6 m: the default
    # follows the KIND, and both are uniforms.
    assert out["gauges"] == [1.6, 0.9, 0.3]
    assert out["offsets"] == [0, 4.85]
    body = _main_body(out["fs"])
    loop = _find(r"for \(int i = 0; i < (\d+); i\+\+\) \{\s*"
                 r"if \(float\(i\) >= uTrkCount\) break;", body)
    assert int(loop.group(1)) == 8, "the clamp must match the loop bound"
    _find(r"float tkO = \(float\(i\) - \(uTrkCount - 1\.0\) \* 0\.5\)"
          r" \* uTrkGauge\s*\+ uTrkOffset;", body)
    _find(r"float tkU = dot\(vAstraWorld, vAstraRoad\.xyz\);", body)
    _find(r"float tkWob = \(astraNoise2\(vec2\(tkU \* [\d.]+,"
          r" float\(i\) \* [\d.]+\)\s*\+ uTrkSeed\.xy\) - 0\.5\)"
          r" \* [\d.]+;", body)
    _find(r"float tkX = vAstraRoad\.w - tkO - tkWob;", body)
    assert "vAstraWorld.x" not in body and "vAstraWorld.z" not in body


def test_kind_is_a_uniform_and_both_kinds_are_one_shader():
    """'tyre' and 'foot' are two different marks, and baking either into the
    GLSL would hand the first material to compile the cache key its own kind
    for every material that shares it.  So the kind is a uniform the shader
    mixes on, the tyre's tread rides `astraStroke` (which kills itself once a
    pixel spans a rib), the foot's prints step to alternating sides of their
    line, and neither survives past the distance it can be resolved."""
    out = _probe("""
const tyre = std();
patchTracks(tyre, { kind: 'tyre', depth: 0.6 });
const foot = std();
patchTracks(foot, { kind: 'foot', depth: 0.6 });
const odd = std();
patchTracks(odd, { kind: 'hovercraft' });
const st = compile(tyre), sf = compile(foot);
console.log(JSON.stringify({
  sameKey: tyre.customProgramCacheKey() === foot.customProgramCacheKey(),
  sameFs: st.fragmentShader === sf.fragmentShader,
  kinds: [tyre.userData.uniforms.uTrkKind.value,
          foot.userData.uniforms.uTrkKind.value,
          odd.userData.uniforms.uTrkKind.value],
  fs: st.fragmentShader,
}));
""")
    assert out["sameKey"] and out["sameFs"], "the kind was baked in"
    assert out["kinds"] == [0, 1, 0], "an unknown kind must fall back to tyre"
    body = _main_body(out["fs"])
    _find(r"float tkHw = mix\(([\d.]+), ([\d.]+), uTrkKind\);", body)
    rib = _find(r"float tkRb = astraStroke\(tkU / ([\d.]+)"
                r" \+ abs\(tkX\) \* [\d.]+,\s*([\d.]+)\);", body)
    # astraStroke clamps its own antialias width at 0.30 and only kills itself
    # past w * 1.5, so a rib wider than 0.20 would grey out and stay there.
    assert float(rib.group(2)) < 0.20, rib.group(2)
    _find(r"float tkC = floor\(tkU / ([\d.]+)\);", body)
    _find(r"float tkAc = \(tkX - \(mod\(tkC, 2\.0\) - 0\.5\) \* [\d.]+\)"
          r" / [\d.]+;", body)
    _find(r"float tkFd = astraRoadFade\(vAstraWorld, [\d.]+\);", body)
    _find(r"float tkPr = mix\(tkTr, 1\.0 - smoothstep\([\d.]+, 1\.0, tkE\),"
          r" tkFd\);", body)
    for line in ("tkPress = max(tkPress, mix(tkB, tkPr, uTrkKind));",
                 "tkWide = max(tkWide, mix(tkBw, tkPw, uTrkKind));"):
        assert line in body, line
    assert "if (uTrkKind" not in body


def test_no_patch_assigns_over_the_colour_it_landed_on():
    """These three land on materials that already carry a triplanar, a
    breakup, an edge wear, rust or snow — every one of which wrote the albedo
    first.  So each must READ what is there before it writes: the seam and the
    tracks mix over it by their own mask, and the road, which does rule the
    hue of a made surface, carries the incoming albedo through as its
    light/dark variation."""
    out = _probe("""
const m = std();
patchTriplanar(m);
patchMicroBreakup(m);
patchRoadSurface(m);
patchSeamBand(m);
patchTracks(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    body = _main_body(out["fs"])
    # Every write THIS library makes either mixes with the albedo, scales it,
    # or assigns a local that was built from it.
    writes = re.findall(r"diffuseColor\.rgb\s*([*+]?=)\s*(.+?);",
                        _LIB_SRC, re.S)
    assert len(writes) >= 5, writes
    for op, rhs in writes:
        ok = op != "=" or "diffuseColor" in rhs or rhs.strip() == "roCol"
        assert ok, f"assigns over what it landed on: {op} {rhs}"
    keep = body.index("float roKeep = dot(diffuseColor.rgb")
    assert body.index("diffuseColor.rgb = tpC;") < keep
    assert keep < body.index("diffuseColor.rgb = roCol;")
    _find(r"vec3 roCol = uRoadColor\s*"
          r"\* mix\([\d.]+, [\d.]+, clamp\(roKeep \* [\d.]+,"
          r" 0\.0, 1\.0\)\);", body)
    # The tracks are made OF the surface they are pressed into, so their
    # colour is that same albedo darkened — never a colour of their own.
    _find(r"vec3 tkCol = diffuseColor\.rgb \* mix\(1\.0, [\d.]+,"
          r" uTrkDepth\)", body)
    _find(r"diffuseColor\.rgb = mix\(diffuseColor\.rgb, smCol,"
          r" smK \* [\d.]+\);", body)
    _find(r"diffuseColor\.rgb = mix\(diffuseColor\.rgb, tkCol,\s*"
          r"clamp\(tkPress \* uTrkDepth, 0\.0, 1\.0\)\);", body)
    # Nothing here displaces geometry: albedo and gloss only, because the
    # vertex hook cannot reach a per-pixel depth and a displacement would tear
    # a hard-edged kerb open at its rim.
    vs = _main_body(_probe("""
const m = std();
patchRoadSurface(m); patchTracks(m);
console.log(JSON.stringify({ fs: compile(m).vertexShader }));
""")["fs"])
    assert "transformed +=" not in vs and "transformed *=" not in vs


def test_no_patch_declares_a_name_its_neighbours_own():
    """The silent failure this library is most exposed to: a road rides the
    same materials as surface_wear, terrain_shade, aging, accumulation and
    strata, and patchStandard DROPS a repeated uniform or varying and keeps
    the first — so a shared name would leave one patch reading the other's
    value with nothing reported.  A repeated main() local is a compile error
    instead, and a repeated helper throws."""
    out = _probe("""
const road = std();
patchRoadSurface(road);
patchSeamBand(road);
patchTracks(road);
const nbr = std();
patchTriplanar(nbr);
patchSlopeSplat(nbr, { snowLine: 30 });
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
patchTriplanar(all);
patchSlopeSplat(all, { snowLine: 30 });
patchMicroBreakup(all);
patchEdgeWear(all);
patchDripStains(all);
patchRust(all);
patchDust(all);
patchSnow(all);
patchSand(all);
patchRockStrata(all);
patchErosionStreaks(all);
patchRoadSurface(all);
patchSeamBand(all);
patchTracks(all);
const s = compile(all);
console.log(JSON.stringify({
  roadU: Object.keys(road.userData.uniforms),
  nbrU: Object.keys(nbr.userData.uniforms),
  vs: s.vertexShader, fs: s.fragmentShader,
}));
""")
    shared = set(out["roadU"]) & set(out["nbrU"])
    assert shared == {"uTime"}, shared
    for stage in ("vs", "fs"):
        src = out[stage]
        decls = re.findall(
            r"^\s*(?:uniform|varying)\s+(?:lowp |mediump |highp )?"
            r"[a-z0-9]+\s+(\w+)\s*;", src, re.M)
        dupes = [n for n, c in Counter(decls).items() if c > 1]
        assert not dupes, f"{stage} declares {dupes} twice"
        fns = re.findall(r"^(?:float|vec[234]|int|mat[234])\s+(\w+)\s*\(",
                         _unguarded(src), re.M)
        assert not [n for n, c in Counter(fns).items() if c > 1]
        locals_ = re.findall(
            r"^\s*(?:float|vec[234]|int|mat[234])\s+(\w+)\s*=",
            _main_body(src), re.M)
        clash = [n for n, c in Counter(locals_).items() if c > 1]
        assert not clash, f"{stage} main() declares {clash} twice"
    assert "vAstraWorld" in out["vs"] and "vAstraRoad" in out["fs"]
    # fwidth, dFdx and astraStroke are fragment-only, and the util block ships
    # in both stages: they may be DEFINED in the vertex shader, never called.
    vbody = _main_body(out["vs"])
    assert "fwidth(" not in vbody and "dFdx(" not in vbody
    assert "astraStroke(" not in vbody
    # Every helper this library adds is astraRoad-prefixed, so a neighbour's
    # astraWear/astraAge/astraAcc/astraStrata body survives.
    mine = re.findall(r"'(?:float|vec[234])\s+(astra\w+)\s*\(", _LIB_SRC)
    assert mine and all(n.startswith("astraRoad") for n in mine), mine


def test_an_option_is_a_uniform_and_never_baked_into_the_source():
    """three caches programs by key and the FIRST material to compile a key
    decides the GLSL every material sharing it gets.  Two materials that
    differ only in options must therefore compile to the same source, or the
    second silently wears the first's road — one lane width, one gutter, one
    seam colour and one track kind imposed on every other surface."""
    out = _probe("""
const a = std();
patchRoadSurface(a, { aggregate: 0.25, wear: 0.5, seed: 1 });
patchSeamBand(a, { width: 0.5, seed: 1 });
patchTracks(a, { count: 2, seed: 1 });
const b = std();
patchRoadSurface(b, { aggregate: 1, wear: 0.05, patches: 0.9, gutter: 1,
                      color: 0x112233, lane: 6.2, halfWidth: 9,
                      dir: [1, 0, 0], center: [4, 0, 2], seed: 99 });
patchSeamBand(b, { width: 2.5, weeds: 1, color: 0x445566, seed: 99 });
patchTracks(b, { count: 5, depth: 1, kind: 'foot', gauge: 0.44,
                 offset: -3.1, seed: 99 });
const sa = compile(a), sb = compile(b);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  sameKey: a.customProgramCacheKey() === b.customProgramCacheKey(),
  sameVs: sa.vertexShader === sb.vertexShader,
  sameFs: sa.fragmentShader === sb.fragmentShader,
  aRoad: [u(a).uRoadAgg.value, u(a).uRoadWear.value, u(a).uRoadLane.value,
          u(a).uRoadHalf.value, u(a).uRoadCenter.value.toArray()],
  bRoad: [u(b).uRoadAgg.value, u(b).uRoadWear.value, u(b).uRoadLane.value,
          u(b).uRoadHalf.value, u(b).uRoadCenter.value.toArray()],
  bSeam: [u(b).uSeamWidth.value, u(b).uSeamWeeds.value],
  bTrk: [u(b).uTrkCount.value, u(b).uTrkDepth.value, u(b).uTrkKind.value,
         u(b).uTrkGauge.value, u(b).uTrkOffset.value],
  colors: [u(b).uRoadColor.value.getHex(), u(b).uSeamColor.value.getHex()],
  clamped: [(() => { const m = std();
                     patchRoadSurface(m, { wear: 4, gutter: -2 });
                     return [m.userData.uniforms.uRoadWear.value,
                             m.userData.uniforms.uRoadGutter.value]; })()],
}));
""")
    assert out["sameKey"] and out["sameVs"] and out["sameFs"]
    assert out["aRoad"] == [0.25, 0.5, 3.4, 3.5, [0, 0, 0, 0]]
    # The centreline carries an ENABLE in its w, so "the mesh's own origin"
    # needs no second uniform and no second program.
    assert out["bRoad"] == [1, 0.05, 6.2, 9, [4, 0, 2, 1]]
    assert out["bSeam"] == [2.5, 1]
    assert out["bTrk"] == [5, 1, 1, 0.44, -3.1]
    assert out["colors"] == [0x112233, 0x445566]
    assert out["clamped"] == [[1, 0]], "an option out of range must clamp"


def test_the_frame_is_the_roads_and_an_omitted_option_inherits_it():
    """`dir`, `halfWidth` and `center` describe the ROAD, not one patch, so a
    seam band applied after a road surface must not silently reset the road to
    a 3.5 m default along +Z, which would move the ruts, the gutter and the
    band all at once."""
    out = _probe("""
const m = std();
patchRoadSurface(m, { halfWidth: 2.4, dir: [1, 0, 0], center: [3, 0, 0] });
const after = () => [m.userData.uniforms.uRoadHalf.value,
                     m.userData.uniforms.uRoadDir.value.toArray(),
                     m.userData.uniforms.uRoadCenter.value.toArray()];
const first = after();
patchSeamBand(m, { width: 0.6 });
const inherited = after();
patchTracks(m, { count: 2, halfWidth: 8 });
const overridden = after();
console.log(JSON.stringify({ first, inherited, overridden,
  frames: m.userData.astraPatches.filter((p) => p.name === 'road:frame').length,
  names: m.userData.astraPatches.map((p) => p.name),
}));
""")
    assert out["first"] == [2.4, [1, 0, 0], [3, 0, 0, 1]]
    assert out["inherited"] == out["first"], "the seam band reset the road"
    assert out["overridden"][0] == 8, "a stated option must win"
    assert out["overridden"][1] == [1, 0, 0], "and only that option moves"
    assert out["frames"] == 1, "the frame must be applied once"
    assert out["names"] == ["road:frame", "road:surface", "road:seam",
                            "road:tracks"]


def test_a_made_road_is_never_polished_into_a_sky_mirror():
    """THE PORT'S OWN LAW.  `fragmentBody` lands after <color_fragment> and
    therefore before <roughnessmap_fragment> declares roughnessFactor, so the
    one gloss move here is the MATERIAL's — which makes it the area-weighted
    average of the surface, not the polished rut's own value.  Wheel strips
    cover about a quarter of a lane and the gutter a twelfth of the width.

    Our renderer has no post chain, takes its specular from a bright sky
    environment, and shows a road at a grazing angle where a smooth dielectric
    is almost all Fresnel: measured on the showcase host 2026-09-01, tarmac
    authored at 0.85 and composed down to 0.63 by the reference's factors read
    (171,172,175) — a white card with every mark washed off it — where the
    same albedo at 0.86 reads (136,138,144).  So the bound is on the FACTOR: a
    fully worn, fully wet road may not lose more than a sixth of the roughness
    it was authored with, and a track may not lose more than a tenth."""
    out = _probe("""
const worn = std({ roughness: 0.8 });
patchRoadSurface(worn, { wear: 1, gutter: 1, aggregate: 0 });
const wornR = worn.roughness;
patchRoadSurface(worn, { wear: 1, gutter: 1, aggregate: 0 });
const twice = worn.roughness;
const gravel = std({ roughness: 0.8 });
patchRoadSurface(gravel, { wear: 0, gutter: 0, aggregate: 1 });
const seam = std({ roughness: 0.8 });
patchSeamBand(seam, { weeds: 1 });
const rutted = std({ roughness: 0.8 });
patchTracks(rutted, { depth: 1 });
const all = std({ roughness: 0.8 });
patchRoadSurface(all, { wear: 1, gutter: 1 });
patchSeamBand(all);
patchTracks(all, { depth: 1 });
const rough = std({ roughness: 0.98 });
patchSeamBand(rough, { weeds: 1 });
const basic = new THREE.MeshBasicMaterial({ color: 0x445566 });
patchRoadSurface(basic);
patchSeamBand(basic);
patchTracks(basic);
console.log(JSON.stringify({
  wornR, twice, gravel: gravel.roughness, seam: seam.roughness,
  rutted: rutted.roughness, all: all.roughness, rough: rough.roughness,
  base: all.userData.astraRoughness.base,
  factors: all.userData.astraRoughness.factors,
  basicRough: basic.roughness === undefined,
  basicPatched: basic.userData.astraPatches.map((p) => p.name),
}));
""")
    assert out["base"] == 0.8, "the authored value is the one to compose on"
    assert sorted(out["factors"]) == ["road:seam", "road:surface",
                                      "road:tracks"]
    # A polished, wet road is glossier than the mix it was laid from; a loose
    # gravel one is matter; a pressed track packs smooth.
    assert out["wornR"] < 0.8, out["wornR"]
    assert out["gravel"] > 0.8, out["gravel"]
    assert out["seam"] > 0.8 and out["rutted"] < 0.8
    assert out["twice"] == out["wornR"], "re-applying compounded the gloss"
    # THE BOUND, on the worst case each patch can be asked for.
    assert out["factors"]["road:surface"] >= 0.84, out["factors"]
    assert out["factors"]["road:tracks"] >= 0.90, out["factors"]
    # One factor each, so the material is exactly their product.
    product = 0.8
    for f in out["factors"].values():
        product *= f
    assert abs(out["all"] - product) < 1e-9, (out["all"], product)
    assert out["all"] < 0.8, "three factors, one product, still a road"
    # Roughness only means anything up to 1, and a matte patch on an already
    # rough material must not run off the top of the range.
    assert 0.9 <= out["rough"] <= 1.0, out["rough"]
    # A material with no roughness at all still takes the albedo patch.
    assert out["basicRough"]
    assert out["basicPatched"] == ["road:frame", "road:surface", "road:seam",
                                   "road:tracks"]


def test_the_road_keeps_a_colour_after_its_grain_has_faded_out():
    """The other half of the port.  Every fine field on this surface is
    faded out by its own pixel size a few metres from the camera — which is
    right, and left the carriageway ONE flat value past that: a grey card with
    a repair grid on it, at exactly the distance most of the road lives.  So
    the road also carries a metre-scale field, in its own (along, across)
    frame so it turns with the road, and that field moves the HUE as well as
    the level — warm dust and bleached binder against cool fresh bitumen — for
    the variance the brief asks for inside every effect.  The tint mix is
    centred on neutral, so a road still renders the tone it was given, and a
    sub-LSB dither rides it because a smooth gradient over a near-neutral grey
    bands at eight bits once the grain that hid it has gone."""
    out = _probe("""
const m = std();
patchRoadSurface(m, { aggregate: 0.3 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    body = _main_body(out["fs"])
    # In the ROAD's frame, not the world's, and slow enough to survive: the
    # two octaves are ~9 m and ~29 m cycles.
    _find(r"vec2 roMq = vec2\(roU, vAstraRoad\.w\) \+ uRoadSeed\.yx;", body)
    blot = _find(r"float roBlot = \(astraNoise2\(roMq \* ([\d.]+)\) - 0\.5\)"
                 r" \* ([\d.]+)\s*\+ \(astraNoise2\(roMq \* ([\d.]+)"
                 r" \+ [\d.]+\) - 0\.5\) \* ([\d.]+);", body)
    for freq in (float(blot.group(1)), float(blot.group(3))):
        assert 0.02 <= freq <= 0.2, f"1/{freq} m is not a metre-scale cycle"
    # A hue that walks WITH the field, and a mix centred on neutral so the
    # road's mean tone is still the one it was given.
    tint = _find(r"\* mix\(vec3\((0\.9\d+), (0\.9\d+), (1\.0\d+)\),\s*"
                 r"vec3\((1\.0\d+), (1\.0\d+), (0\.9\d+)\), roHue\);", body)
    cool = [float(tint.group(i)) for i in (1, 2, 3)]
    warm = [float(tint.group(i)) for i in (4, 5, 6)]
    for c, w in zip(cool, warm, strict=True):
        assert abs((c + w) / 2 - 1.0) < 0.02, (cool, warm)
    assert cool[2] > cool[0] and warm[0] > warm[2], "the hue must actually turn"
    _find(r"float roHue = clamp\(0\.5 \+ roBlot \* [\d.]+, 0\.0, 1\.0\);",
          body)
    # The dither is sub-LSB at 8 bits (1/255 = 0.0039 of a mid grey) and per
    # pixel, so it breaks a band without reading as noise.
    dither = _find(r"roCol \*= 1\.0 \+ \(astraHash21\(gl_FragCoord\.xy\)"
                   r" - 0\.5\) \* ([\d.]+);", body)
    assert 0.004 <= float(dither.group(1)) <= 0.02, dither.group(1)
    # The aggregate is stone, and a road is not mixed from one stone.
    _find(r"roStone \*= mix\(vec3\([\d.]+, [\d.]+, [\d.]+\),\s*"
          r"vec3\([\d.]+, [\d.]+, [\d.]+\), roG2\);", body)
    # The gutter is two bands: a cool damp strip, and the silt washed to the
    # kerb line ON TOP of it, in patches along the road — a flat pale line at
    # the kerb is road marking, not dirt.
    _find(r"float roSilt = smoothstep\(uRoadHalf - roGw \* [\d.]+ \+ roGj,"
          r"\s*uRoadHalf \+ roGj, roA\) \* uRoadGutter\s*"
          r"\* smoothstep\([\d.]+, [\d.]+,\s*astraNoise2\(", body)
    _find(r"roCol = mix\(roCol, roCol \* vec3\([\d.]+, [\d.]+, [\d.]+\),\s*"
          r"roGut \* \(1\.0 - roSilt \* [\d.]+\)\);", body)
    # And the seam's gravel and weeds carry a hue spread of their own.
    seam = _probe("""
const m = std();
patchSeamBand(m, { weeds: 0.6 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    sbody = _main_body(seam["fs"])
    _find(r"float smHue = astraNoise2\(vec2\(smU, vAstraRoad\.w\)"
          r" \* [\d.]+\s*\+ uSeamSeed\.zx\);", sbody)
    _find(r"smCol \*= mix\(vec3\([\d.]+, [\d.]+, [\d.]+\),"
          r" vec3\([\d.]+, [\d.]+, [\d.]+\), smHue\);", sbody)
    _find(r"vec3 smWc = uSeamWeed \* mix\([\d.]+, [\d.]+, smG\)\s*"
          r"\* mix\(vec3\([\d.]+, [\d.]+, [\d.]+\),\s*"
          r"vec3\([\d.]+, [\d.]+, [\d.]+\), smHue\);", sbody)


def test_one_seed_lays_the_same_road_every_time():
    """A render is re-run — for a fix round, for a video, for the judge — and
    the repairs must not move between takes.  The seed reaches the GPU only as
    a noise-space OFFSET (a uniform, because baking it would hand material
    one's seed to every material sharing the key), the three patches are
    decorrelated from each other, and all three differ from surface_wear's,
    aging's and accumulation's for the same seed."""
    out = _probe("""
const mk = (seed) => {
  const m = std();
  patchRoadSurface(m, { seed });
  patchSeamBand(m, { seed });
  patchTracks(m, { seed });
  patchMicroBreakup(m, { seed });
  patchDust(m, { seed });
  patchSnow(m, { seed });
  return m;
};
const a = mk(7), b = mk(7), c = mk(8);
const u = (m, n) => m.userData.uniforms[n].value.toArray();
console.log(JSON.stringify({
  aRoad: u(a, 'uRoadSeed'), bRoad: u(b, 'uRoadSeed'), cRoad: u(c, 'uRoadSeed'),
  aSeam: u(a, 'uSeamSeed'), cSeam: u(c, 'uSeamSeed'),
  aTrk: u(a, 'uTrkSeed'), cTrk: u(c, 'uTrkSeed'),
  aMicro: u(a, 'uMicroSeed'), aDust: u(a, 'uDustSeed'),
  aSnow: u(a, 'uSnowSeed'),
  sameSrc: compile(a).fragmentShader === compile(c).fragmentShader,
}));
""")
    assert out["aRoad"] == out["bRoad"], "the same seed moved the repairs"
    assert out["aRoad"] != out["cRoad"] and out["aSeam"] != out["cSeam"]
    assert out["aTrk"] != out["cTrk"]
    assert len({tuple(out[k]) for k in ("aRoad", "aSeam", "aTrk")}) == 3
    for other in ("aMicro", "aDust", "aSnow"):
        assert out["aRoad"] != out[other] and out["aSeam"] != out[other]
        assert out["aTrk"] != out[other]
    assert all(0 <= v <= 40 for v in out["aRoad"] + out["aTrk"])
    assert out["sameSrc"], "the seed is a uniform, not source"
    assert "Math.random" not in _LIB_SRC


def test_every_fine_field_dies_before_it_can_alias():
    """Detail that reads at three metres and turns to a shimmering mess at
    forty is worse than no detail: it is the one way a surface reads as an
    EFFECT rather than as a surface, and it moves with the camera.  Every
    field here finer than the pixel that will read it is therefore faded by
    the WORLD size of that pixel, and what remains converges to the average
    tone rather than to nothing."""
    out = _probe("""
const m = std();
patchRoadSurface(m, { aggregate: 0.5 });
patchSeamBand(m);
patchTracks(m, { kind: 'foot' });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    body = _main_body(fs)
    _find(r"float astraRoadFade\(vec3 p, float cycle\) \{\s*"
          r"return 1\.0 - smoothstep\(([\d.]+), ([\d.]+),\s*"
          r"length\(fwidth\(p\)\) / max\(cycle, 1e-4\)\);", fs)
    _find(r"float roGs = mix\([\d.]+, [\d.]+, uRoadAgg\);", body)
    _find(r"float roFd = astraRoadFade\(vAstraWorld, roGs\)"
          r" \* \(1\.0 - roRut \* [\d.]+\);", body)
    assert "roGrain" in body and "* roFd;" in body
    # The third, finest octave of the aggregate rides roFd TWICE, so it is the
    # first thing to go: two octaves at one stone size read as soft blobs.
    _find(r"\+ \(roG3 - 0\.5\) \* [\d.]+ \* roFd\) \* roFd;", body)
    # The seam's two fields converge to their AVERAGE, not to nothing.
    _find(r"float smFd = astraRoadFade\(vAstraWorld, [\d.]+\);", body)
    _find(r"float smFc = astraRoadFade\(vAstraWorld, [\d.]+\);", body)
    _find(r"vec3 smCol = uSeamColor \* mix\(1\.0, mix\([\d.]+, [\d.]+, smC\),"
          r" smFc\)\s*\* \(1\.0 \+ \(smG - 0\.5\) \* [\d.]+ \* smFd\);", body)
    _find(r"float smWd = mix\([\d.]+, smoothstep\([\d.]+, [\d.]+, smTf\),"
          r" smFc\)", body)
    # The tracks: the rim and the tread go with the fade, and the prints
    # become the trail.
    _find(r"float tkRim = clamp\(tkWide - tkPress, 0\.0, 1\.0\) \* tkFd;",
          body)
    _find(r"\* \(1\.0 - tkTread \* [\d.]+ \* tkFd\);", body)
    # Every fade argument is a real feature size in metres, not a magic
    # screen-space number.
    for cycle in re.findall(r"astraRoadFade\(vAstraWorld, ([\d.]+)\)", body):
        assert 0.01 <= float(cycle) <= 1.0, cycle
