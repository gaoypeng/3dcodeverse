"""merge.js — mergeStatic must collapse placed props into ONE true draw call.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_merge_lib.py).  The reference's whole scenario is kept: 300 placed
boxes, a third of them non-indexed, become one mesh with world matrices and
material colours baked in; groups are traversed with the parent transform; the
wrapper fails loud.  Draw calls are the same currency on our host (headless
Chrome falls back to SwiftShader whenever the GPU launch does not take), so
none of that needed retargeting.

Everything from ``test_the_surface_response_survives_the_merge`` down covers
what the port CHANGED, all four of them things the showcase render caught:

* the rebuilt material copied only roughness/metalness/flatShading and three
  maps, so a merged batch lost its ``emissive`` (lantern boxes rendered as dead
  grey blocks) and its ``side`` (6 of 13 DoubleSide fence slats vanished);
* a mirrored placement (``scale.x = -1``) baked its flipped winding and
  rendered as its own interior — three flips winding per draw from a matrix
  that the bake throws away;
* ``visible = false`` pieces were baked in permanently;
* a shader-patched material was silently reduced to a plain standard one, and
  ``Material.clone()`` would not have saved it (it copies userData but not
  ``onBeforeCompile`` — a dead chain still holding a live cache key), so the
  patch chain is replayed onto the merged material over the SOURCE uniform map.

The last test covers the one deliberate look change: ``variance`` (default
0.12) spreads each piece's baked albedo, because a 40-stone wall cut from one
material otherwise arrives as a single flat swatch — modal_frac 0.49 of the
wall region in the before render, 0.17 after.
"""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("merge.js",)

# The reference's probe: 300 coloured boxes -> one mesh, colours survive.
# Every third box is non-indexed so the probe also covers the mixed-index
# unification that mergeGeometries itself refuses.  variance is off here: these
# assertions grade the BAKE, and the spread is graded on its own below.
_PROBE = """
import * as THREE from 'three';
import { mergeStatic } from './lib/merge.js';

const N = 300;
const boxes = [];
const cols = [];
for (let i = 0; i < N; i++) {
  const c = new THREE.Color().setHSL((i * 0.618033) % 1, 0.7, 0.5);
  cols.push([c.r, c.g, c.b]);
  let g = new THREE.BoxGeometry(1, 1, 1);
  if (i % 3 === 0) g = g.toNonIndexed();
  const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({
    color: c, roughness: 0.6, metalness: 0.1 }));
  m.position.set((i % 20) * 4 - 38, (i % 5) * 2, Math.floor(i / 20) * 4 - 28);
  m.rotation.y = i * 0.37;
  m.scale.setScalar(1 + (i % 7) * 0.25);
  boxes.push(m);
}
const merged = mergeStatic(boxes, { variance: 0 });

// One TRUE draw call: a single mesh, no children, no material array,
// no geometry groups (each group is its own draw call).
const oneMesh = !!merged.isMesh && merged.children.length === 0
    && !Array.isArray(merged.material)
    && merged.geometry.groups.length === 0;

// mergeGeometries concatenates in input order; with everything
// unified to non-indexed, each box owns an equal vertex run.
const pos = merged.geometry.attributes.position;
const col = merged.geometry.attributes.color;
const stride = pos.count / N;
let colOk = 0, posOk = 0, yHalf150 = 0;
for (const i of [0, 1, 3, 7, 50, 150, 299]) {
  let dc = 0;
  const centroid = new THREE.Vector3();
  for (let v = i * stride; v < (i + 1) * stride; v++) {
    dc = Math.max(dc,
        Math.abs(col.getX(v) - cols[i][0]),
        Math.abs(col.getY(v) - cols[i][1]),
        Math.abs(col.getZ(v) - cols[i][2]));
    centroid.add(new THREE.Vector3(pos.getX(v), pos.getY(v), pos.getZ(v)));
    if (i === 150) {
      yHalf150 = Math.max(yHalf150,
          Math.abs(pos.getY(v) - boxes[150].position.y));
    }
  }
  centroid.divideScalar(stride);
  if (dc < 1e-3) colOk++;
  if (centroid.distanceTo(boxes[i].position) < 1e-3) posOk++;
}

// Groups are traversed and PARENT transforms reach the bake.
const grp = new THREE.Group();
grp.position.set(100, 0, 0);
for (const dx of [-2, 0, 2]) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshStandardMaterial({ color: 0x8899aa }));
  m.position.x = dx;
  grp.add(m);
}
const gm = mergeStatic([grp], { variance: 0 });
gm.geometry.computeBoundingBox();
const bb = gm.geometry.boundingBox;
const groupCenterX = (bb.min.x + bb.max.x) / 2;

// Fail-loud contract: empty input and mesh-less input both throw.
let threwEmpty = false, threwNoMesh = false;
try { mergeStatic([]); } catch (e) { threwEmpty = true; }
try { mergeStatic([new THREE.Group()]); } catch (e) { threwNoMesh = true; }

console.log(JSON.stringify({
  oneMesh, stride, colOk, posOk, yHalf150, groupCenterX,
  vertexColors: merged.material.vertexColors === true,
  isStandard: merged.material.isMeshStandardMaterial === true,
  roughness: merged.material.roughness,
  metalness: merged.material.metalness,
  hasNormals: !!merged.geometry.attributes.normal,
  hasUv: !!merged.geometry.attributes.uv,
  shadows: merged.castShadow && merged.receiveShadow,
  threwEmpty, threwNoMesh,
}));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return measure(_PROBE, _LIBS)


def test_three_hundred_boxes_collapse_to_one_draw_call(probe):
    """300 placed props become ONE mesh — no children, no material array, no
    geometry groups; anything else is still N draw calls under a new name."""
    m = probe
    assert m["oneMesh"], m
    # Mixed indexed/non-indexed inputs were unified (36 verts per box once
    # expanded); a fractional stride means a box was dropped.
    assert m["stride"] == 36, f"vertex runs uneven: {m['stride']}"


def test_world_transforms_and_per_box_colors_are_baked(probe):
    """Each box's vertex run must sit at its world position (pos + rot +
    scale) and carry its material colour as vertex colour — the
    render-visible promise the reference's probe grades."""
    m = probe
    assert m["posOk"] == 7, f"world matrices not baked: {m['posOk']}/7"
    assert m["colOk"] == 7, f"material colors lost: {m['colOk']}/7"
    # Box 150 has scale 1.75 with yaw-only rotation: its baked y half-extent
    # must be 0.875, or scale was dropped in the bake.
    assert abs(m["yHalf150"] - 0.875) < 1e-3, m["yHalf150"]


def test_one_standard_lit_material_with_vertex_colors(probe):
    """One LIT MeshStandardMaterial with vertexColors on, normals + uv
    present, shadows on — a basic/unlit or colour-blind material renders the
    whole merge flat."""
    m = probe
    assert m["isStandard"] and m["vertexColors"], m
    assert abs(m["roughness"] - 0.6) < 1e-6, m["roughness"]
    assert abs(m["metalness"] - 0.1) < 1e-6, m["metalness"]
    assert m["hasNormals"] and m["hasUv"], m
    assert m["shadows"], "merged mesh does not cast/receive shadows"


def test_groups_are_traversed_and_parent_transforms_reach_the_bake(probe):
    """Assets are Groups: passing one must merge its leaf meshes with the
    PARENT transform included — a bake that reads only the mesh's local
    matrix would pile every asset at the origin."""
    m = probe
    assert abs(m["groupCenterX"] - 100) < 1e-3, m["groupCenterX"]


def test_fails_loud_on_empty_or_meshless_input(probe):
    """The wrapper throws instead of returning an empty mesh: a silent no-op
    would ship a scene missing its props with nothing in the logs (fail loud
    is the repo-wide contract)."""
    m = probe
    assert m["threwEmpty"], "empty array did not throw"
    assert m["threwNoMesh"], "mesh-less group did not throw"


def test_the_surface_response_survives_the_merge():
    """Everything but colour comes from the batch's first material, and the
    port's rebuilt material dropped most of it.  `emissive` is the one that
    costs a look outright — merged lantern boxes rendered as dead grey blocks
    (lantern region mean_lum 0.644 -> 0.777 day, 0.376 -> 0.642 night) — and
    `side` deletes geometry: 6 of 13 DoubleSide slats were culled.  A batch
    that DISAGREES about the response must say so, because those differences
    are lost either way."""
    out = measure("""
import * as THREE from 'three';
import { mergeStatic } from './lib/merge.js';
const tex = new THREE.Texture();
const mat = new THREE.MeshStandardMaterial({
  color: 0x334455, roughness: 0.31, metalness: 0.22, side: THREE.DoubleSide,
  emissive: new THREE.Color(0xff9944), emissiveIntensity: 2.6,
  transparent: true, opacity: 0.8, envMapIntensity: 1.7, flatShading: true,
  normalMap: tex, aoMap: tex, alphaMap: tex, dithering: true,
});
mat.normalScale.set(0.4, 0.4);
const mk = (m) => new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), m);
const one = mergeStatic([mk(mat)]).material;

// a batch whose second piece disagrees about more than colour warns once
const warns = [];
const realWarn = console.warn;
console.warn = (msg) => warns.push(String(msg));
const odd = new THREE.MeshStandardMaterial({ color: 0x334455, roughness: 0.9 });
mergeStatic([mk(mat), mk(odd)]);
mergeStatic([mk(mat), mk(mat.clone())]);   // colour-equal: no warning
console.warn = realWarn;

console.log(JSON.stringify({
  emissive: one.emissive.getHexString(),
  emissiveIntensity: one.emissiveIntensity,
  doubleSide: one.side === THREE.DoubleSide,
  roughness: one.roughness, metalness: one.metalness,
  opacity: one.opacity, transparent: one.transparent,
  envMapIntensity: one.envMapIntensity, flatShading: one.flatShading,
  dithering: one.dithering,
  maps: ['normalMap', 'aoMap', 'alphaMap'].filter((k) => one[k] === tex).length,
  normalScale: one.normalScale.x,
  // colour is NOT copied: it lives per-vertex, and copying it would
  // multiply the bake a second time
  white: one.color.getHex() === 0xffffff,
  warnCount: warns.length, warned: warns.join(' ').includes('surface response'),
}));
""", _LIBS)
    assert out["emissive"] == "ff9944" and out["emissiveIntensity"] == 2.6, out
    assert out["doubleSide"], "side dropped — back-facing cards disappear"
    assert out["transparent"] and out["opacity"] == 0.8, out
    assert out["envMapIntensity"] == 1.7 and out["flatShading"], out
    assert out["dithering"], "dithering dropped — merged gradients band"
    assert out["maps"] == 3 and out["normalScale"] == 0.4, out
    assert abs(out["roughness"] - 0.31) < 1e-6, out
    assert out["white"], "the first material's colour was copied AND baked"
    assert out["warnCount"] == 1 and out["warned"], out


def test_a_mirrored_piece_is_not_baked_inside_out():
    """`scale.x = -1` is how a matching pair gets made.  Its world matrix has
    a negative determinant, so the bake reverses which side of every triangle
    faces out; three normally fixes that per draw from the matrix, which the
    merge throws away.  The right-hand buttress slab rendered as its own
    interior — no sunlit top, a flat dark front — until the winding was
    flipped back (crop pair fx/crop_merge_slab_before.png -> _after.png)."""
    out = measure("""
import * as THREE from 'three';
import { mergeStatic } from './lib/merge.js';
const facing = (mesh) => {
  const g = mesh.geometry;
  const p = g.attributes.position, n = g.attributes.normal;
  const idx = g.index;
  const tri = idx ? idx.count / 3 : p.count / 3;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  let out = 0;
  for (let t = 0; t < tri; t++) {
    const i0 = idx ? idx.getX(t * 3) : t * 3;
    const i1 = idx ? idx.getX(t * 3 + 1) : t * 3 + 1;
    const i2 = idx ? idx.getX(t * 3 + 2) : t * 3 + 2;
    a.fromBufferAttribute(p, i0);
    b.fromBufferAttribute(p, i1).sub(a);
    c.fromBufferAttribute(p, i2).sub(a);
    b.cross(c);
    const vn = new THREE.Vector3().fromBufferAttribute(n, i0);
    if (b.dot(vn) > 0) out++;
  }
  return [out, tri];
};
const mk = (sx, geo) => {
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x998877 }));
  m.position.set(4, 1, 0); m.rotation.set(0.2, 0.5, 0.13); m.scale.x = sx;
  return m;
};
const plain = facing(mergeStatic([mk(1, new THREE.BoxGeometry(0.5, 1.9, 1.1))]));
const mirrored = facing(mergeStatic([mk(-1, new THREE.BoxGeometry(0.5, 1.9, 1.1))]));
// the non-indexed path flips vertex runs instead of index triples
const nonIdx = facing(mergeStatic(
    [mk(-1, new THREE.BoxGeometry(0.5, 1.9, 1.1).toNonIndexed())]));
// a mirrored piece merged NEXT TO a plain one must not disturb it
const both = facing(mergeStatic([mk(1, new THREE.BoxGeometry(1, 1, 1)),
                                 mk(-1, new THREE.BoxGeometry(1, 1, 1))]));
console.log(JSON.stringify({ plain, mirrored, nonIdx, both }));
""", _LIBS)
    for name in ("plain", "mirrored", "nonIdx", "both"):
        out_faces, tris = out[name]
        assert tris > 0 and out_faces == tris, (
            f"{name}: {out_faces}/{tris} triangles wind outward")


def test_invisible_pieces_are_left_out():
    """`visible = false` is how a scene hides an LOD stand-in or a debug
    proxy.  Baking one in makes it permanent and un-hideable, and it is the
    merged mesh's own visibility that would have to go instead."""
    out = measure("""
import * as THREE from 'three';
import { mergeStatic } from './lib/merge.js';
const mk = () => new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
    new THREE.MeshStandardMaterial({ color: 0x778899 }));
const a = mk(), b = mk(), c = mk();
b.visible = false;
const hiddenGroup = new THREE.Group();
hiddenGroup.visible = false;
hiddenGroup.add(c);
const n = (o) => mergeStatic(o).geometry.attributes.position.count;
let threwAllHidden = false;
try { mergeStatic([b]); } catch (e) { threwAllHidden = true; }
console.log(JSON.stringify({
  one: n([a]), withHidden: n([a, b]), withHiddenGroup: n([a, hiddenGroup]),
  threwAllHidden,
}));
""", _LIBS)
    assert out["withHidden"] == out["one"], "an invisible mesh was baked in"
    assert out["withHiddenGroup"] == out["one"], "a hidden group was baked in"
    assert out["threwAllHidden"], "an all-hidden batch merged silently"


def test_a_shader_patched_batch_keeps_its_patch():
    """A patched stone material (patchRockStrata, patchTriplanar, …) is the
    normal case for the props worth merging, and rebuilding the material threw
    the whole chain away — the merged wall lost its look with nothing in the
    logs.  The chain is replayed onto the merged material over the SOURCE
    uniform map, so one `tickShaders` still advances both, and the cache key
    still names the chain (the first material to compile a key decides the
    GLSL for every material holding it)."""
    out = measure("""
import * as THREE from 'three';
import { mergeStatic } from './lib/merge.js';
import { patchStandard, tickShaders } from './lib/shader.js';
const mat = new THREE.MeshStandardMaterial({ color: 0x8b8377 });
patchStandard(mat, { name: 'Strata', uniforms: { uBand: { value: 0.4 } },
  fragmentBody: 'diffuseColor.rgb *= 1.0 - uBand;' });
patchStandard(mat, { name: 'Streaks', fragmentBody: 'diffuseColor.rgb *= 0.9;' });
const merged = mergeStatic([new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat)]);
const mm = merged.material;
const s = { uniforms: {}, vertexShader: '#include <begin_vertex>',
            fragmentShader: '#include <color_fragment>' };
mm.onBeforeCompile(s, null);
const root = new THREE.Group();
root.add(merged);
tickShaders(root, 3.25);
console.log(JSON.stringify({
  patches: (mm.userData.astraPatches || []).map((p) => p.name),
  key: mm.customProgramCacheKey(),
  sharedUniforms: mm.userData.uniforms === mat.userData.uniforms,
  glslInOrder: s.fragmentShader.indexOf('1.0 - uBand')
      < s.fragmentShader.indexOf('*= 0.9'),
  boundUniform: s.uniforms.uBand !== undefined,
  ticked: mat.userData.uniforms.uTime.value,
  stillVertexColors: mm.vertexColors === true,
}));
""", ("merge.js", "shader.js"))
    assert out["patches"] == ["Strata", "Streaks"], out
    assert out["key"] == "astra:Strata+Streaks", out
    assert out["sharedUniforms"], "the merged material got a dead uniform map"
    assert out["glslInOrder"] and out["boundUniform"], out
    assert out["ticked"] == 3.25, "tickShaders no longer reaches the patch"
    assert out["stillVertexColors"], "the replay dropped vertexColors"


def test_variance_stops_a_one_material_batch_reading_as_one_solid():
    """The deliberate look change.  A dry-stone wall cut from one material
    arrives as ONE albedo, and one albedo over 44 stones reads as an extruded
    solid: the wall region of the before render put 49% of its pixels in a
    single luminance bin (modal_frac 0.4918, val_std 0.1245) against 17% after
    (0.1727, val_std 0.1448).  The offset is hashed from where the piece SITS,
    so it is stable across runs and independent of input order, it stays
    inside its stated bound, and `variance: 0` turns it off for baked colours
    that are data."""
    out = measure("""
import * as THREE from 'three';
import { mergeStatic } from './lib/merge.js';
const V = 0.12;
const mk = (i) => {
  const m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshStandardMaterial({ color: 0x8b8377 }));
  m.position.set(i * 1.7 - 20, 0.5 + (i % 3) * 0.4, (i % 5) * 1.1);
  return m;
};
const N = 24;
const pieces = [];
for (let i = 0; i < N; i++) pieces.push(mk(i));
const pieceMeans = (mesh) => {
  const c = mesh.geometry.attributes.color;
  const stride = c.count / N;
  const out = [];
  for (let i = 0; i < N; i++) {
    let r = 0, g = 0, b = 0;
    for (let v = i * stride; v < (i + 1) * stride; v++) {
      r += c.getX(v); g += c.getY(v); b += c.getZ(v);
    }
    out.push([r / stride, g / stride, b / stride]);
  }
  return out;
};
const varied = pieceMeans(mergeStatic(pieces));
const flat = pieceMeans(mergeStatic(pieces, { variance: 0 }));
// reordering the batch must not change what any piece looks like
const shuffled = pieces.slice().reverse();
const reordered = pieceMeans(mergeStatic(shuffled)).reverse();

const lum = (c) => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
const warmth = (c) => c[0] / Math.max(1e-6, c[2]);
const spread = (f, a) => {
  const v = a.map(f);
  const mean = v.reduce((s, x) => s + x, 0) / v.length;
  return Math.sqrt(v.reduce((s, x) => s + (x - mean) ** 2, 0) / v.length) / mean;
};
let maxDev = 0, maxOrderDev = 0;
const base = lum(flat[0]);
for (let i = 0; i < N; i++) {
  maxDev = Math.max(maxDev, Math.abs(lum(varied[i]) - base) / base);
  maxOrderDev = Math.max(maxOrderDev,
      Math.abs(lum(varied[i]) - lum(reordered[i])));
}
console.log(JSON.stringify({
  flatSpread: spread(lum, flat),
  variedSpread: spread(lum, varied),
  warmSpread: spread(warmth, varied),
  flatWarmSpread: spread(warmth, flat),
  maxDev, maxOrderDev, V,
  meanShift: Math.abs(
      varied.reduce((s, c) => s + lum(c), 0) / N - base) / base,
  inRange: varied.every((c) => c.every((x) => x > 0.02 && x <= 0.8)),
}));
""", _LIBS)
    assert out["flatSpread"] < 1e-9, "variance: 0 still varied the batch"
    assert out["flatWarmSpread"] < 1e-9, out
    # every piece its own value AND its own warm/cool, none of it beyond the
    # documented +-variance / +-0.6*variance
    assert out["variedSpread"] > 0.03, f"batch is still one swatch: {out}"
    assert out["warmSpread"] > 0.01, f"value-only spread, no hue: {out}"
    assert out["maxDev"] <= out["V"] * 1.6 + 1e-6, out
    assert out["maxOrderDev"] < 1e-9, "the spread depends on input order"
    # a grade, not a brightness change: the batch mean barely moves
    assert out["meanShift"] < 0.05, out
    # and the result stays a sane non-emissive albedo
    assert out["inRange"], out
