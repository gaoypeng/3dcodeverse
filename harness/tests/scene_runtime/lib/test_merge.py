"""merge.js — regressions: the merged material kept only colour, a mirrored piece baked
inside out, and a patched batch lost its patch chain."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("merge.js",)

def test_the_surface_response_survives_the_merge():
    """The rebuilt material dropped `emissive` (lanterns rendered as dead grey
    blocks) and `side` (DoubleSide slats were culled); a batch that disagrees
    about the response says so."""
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
    """`scale.x = -1` reverses the winding once baked; the slab rendered as its
    own interior until the bake flipped it back."""
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


def test_a_shader_patched_batch_keeps_its_patch():
    """Rebuilding the material threw the patch chain away; it is replayed over
    the SOURCE uniform map, so one `tickShaders` still advances both."""
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


