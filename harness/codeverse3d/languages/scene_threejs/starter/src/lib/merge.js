/**
 * Draw-call collapse for static props: many positioned meshes in, ONE
 * mesh out. Software GL is DRAW-CALL-bound, making this the biggest
 * SwiftShader perf lever. Never
 * merge anything tick() animates or a fix round must retarget alone.
 *
 * The merge is also where a batch stops looking like a batch: 40 stones
 * cut from one material arrive as ONE albedo, and a wall of one albedo
 * reads as an extruded solid, not as stone. `variance` (on by default)
 * gives every piece its own small value/warmth offset, keyed to where
 * the piece SITS, so the collapse costs the wall nothing in hue spread.
 */

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { patchStandard } from './shader.js';

const _KEEP = ['position', 'normal', 'uv', 'color'];
const _WHITE = new THREE.Color(1, 1, 1);
const _C = new THREE.Vector3();

// Surface response carried over from the batch's first material. `color`
// is deliberately absent — it becomes vertex colour. Everything else here
// is what makes the merged mesh shade the way its pieces did; dropping
// `emissive` renders merged lanterns as dead grey boxes, and dropping
// `side` deletes every back-facing card in a DoubleSide batch.
const _SCALARS = [
  'roughness', 'metalness', 'emissiveIntensity', 'envMapIntensity',
  'opacity', 'transparent', 'alphaTest', 'depthWrite', 'side', 'shadowSide',
  'flatShading', 'toneMapped', 'dithering', 'fog', 'aoMapIntensity',
  'bumpScale', 'displacementScale', 'displacementBias',
];
const _MAPS = [
  'map', 'roughnessMap', 'metalnessMap', 'normalMap', 'bumpMap', 'aoMap',
  'alphaMap', 'emissiveMap', 'lightMap', 'displacementMap',
];

/** Stable per-piece hash in [0,1) from a world position. */
function _hash(x, y, z, k) {
  const s = Math.sin(x * 127.1 + y * 311.7 + z * 74.7 + k * 37.3) * 43758.5453;
  return s - Math.floor(s);
}

/**
 * Reverse triangle winding in place (index triples, or the vertex runs of
 * a non-indexed geometry). A mirrored placement — `scale.x = -1`, how a
 * matching pair gets made — has a negative-determinant world matrix, and
 * baking that flips which side of every triangle faces out. The piece then
 * renders as its own interior: front faces culled, the back ones lit from
 * behind. Un-baked it never showed, because three flips winding per draw
 * from the matrix it can still see; after the bake there is no matrix left.
 */
function _flipWinding(g) {
  if (g.index) {
    const a = g.index.array;
    for (let i = 0; i + 2 < a.length; i += 3) {
      const t = a[i + 1]; a[i + 1] = a[i + 2]; a[i + 2] = t;
    }
    g.index.needsUpdate = true;
    return;
  }
  for (const name of Object.keys(g.attributes)) {
    const at = g.attributes[name];
    const n = at.itemSize;
    const arr = at.array;
    for (let i = 0; i + 2 < at.count; i += 3) {
      for (let k = 0; k < n; k++) {
        const p = (i + 1) * n + k;
        const q = (i + 2) * n + k;
        const t = arr[p]; arr[p] = arr[q]; arr[q] = t;
      }
    }
    at.needsUpdate = true;
  }
}

/** What one material contributes to the shared surface response. */
function _sig(m) {
  if (!m) return '';
  return [m.type, m.side, m.transparent === true, m.flatShading === true,
    m.roughness, m.metalness, m.emissive ? m.emissive.getHexString() : '',
    m.emissiveIntensity, m.map ? m.map.uuid : ''].join('|');
}

/**
 * Merge positioned static meshes into one vertex-coloured Mesh.
 * World matrices and material colours are baked in; pieces may differ
 * ONLY by colour — the FIRST mesh's material supplies the shared
 * surface response (and a batch that disagrees about it says so in the
 * console, rather than losing half its look silently). A merge that
 * fails throws rather than shipping a partial scene.
 *
 * Invisible pieces are skipped: `visible = false` is how a scene hides
 * an LOD stand-in, and baking one in would make it permanent.
 *
 * @param {Array<THREE.Object3D>} meshes Positioned meshes (or groups
 *   of meshes) to consume. Add the RETURNED mesh to the scene instead
 *   of these.
 * @param {object} opts
 *   `variance` (default 0.12, 0 disables) per-piece albedo spread: each
 *   piece's baked colour is nudged in value by ±variance and in warm/cool
 *   by ±0.6·variance, from a hash of where the piece sits — so the offset
 *   is stable across runs, independent of input order, and two pieces of
 *   one material stop being the same flat swatch. Set 0 when the baked
 *   colours are data (colour-coded parts, a legend, a gradient you built).
 * @returns {THREE.Mesh} One shadow-casting mesh named 'MergedStatic'
 *   — one draw call. Rename it to the zone it forms.
 * @throws {Error} When the input holds no mesh or the geometries
 *   cannot merge.
 */
export function mergeStatic(meshes, opts = {}) {
  const { variance = 0.12 } = opts;
  if (!meshes || meshes.length === 0) {
    throw new Error('mergeStatic: got an empty input array');
  }

  // Collect leaf meshes with world matrices up to date (parents too:
  // a group's own transform must reach its children's bakes).
  const found = [];
  for (const root of meshes) {
    root.updateWorldMatrix(true, true);
    root.traverse((n) => {
      if (!n.isMesh || n.isInstancedMesh || !n.geometry) return;
      if (!n.geometry.attributes || !n.geometry.attributes.position) return;
      for (let p = n; p; p = p.parent) {
        if (!p.visible) return;
        if (p === root) break;
      }
      found.push(n);
    });
  }
  if (found.length === 0) {
    throw new Error('mergeStatic: no meshes found in the input');
  }

  // mergeGeometries refuses to mix indexed and non-indexed inputs; if
  // both appear, expand the indexed ones.
  const mixed = found.some((m) => m.geometry.index === null);

  const geos = [];
  let firstMat = null;
  let odd = 0;
  for (const mesh of found) {
    let g = mesh.geometry.clone();
    if (mixed && g.index) g = g.toNonIndexed();
    g.applyMatrix4(mesh.matrixWorld);  // bake pos + rot + scale
    // applyMatrix4 carries normals through the normal matrix, which is
    // right even under a mirror — but the winding is not.
    if (mesh.matrixWorld.determinant() < 0) _flipWinding(g);
    g.morphAttributes = {};
    for (const name of Object.keys(g.attributes)) {
      if (!_KEEP.includes(name)) g.deleteAttribute(name);
    }
    if (!g.attributes.normal) g.computeVertexNormals();
    const count = g.attributes.position.count;
    if (!g.attributes.uv) {
      g.setAttribute(
          'uv', new THREE.BufferAttribute(new Float32Array(count * 2), 2));
    }
    const mat = Array.isArray(mesh.material)
        ? mesh.material[0] : mesh.material;
    if (!firstMat && mat) firstMat = mat;
    else if (_sig(mat) !== _sig(firstMat)) odd++;
    // The piece's material colour becomes vertex colour, modulating
    // any colours the geometry already carries (strata bands etc.).
    const tint = mat && mat.color ? mat.color : _WHITE;
    const old = g.attributes.color;
    // Per-piece spread, hashed from where the piece ended up: same seed
    // every run, unchanged if the caller reorders the batch, and different
    // for two copies of one prop standing apart.
    let val = 1;
    let warm = 0;
    if (variance > 0) {
      g.computeBoundingBox();
      g.boundingBox.getCenter(_C);
      val = 1 + (_hash(_C.x, _C.y, _C.z, 1) - 0.5) * 2 * variance;
      warm = (_hash(_C.x, _C.y, _C.z, 7) - 0.5) * 1.2 * variance;
    }
    const rgb = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      rgb[i * 3] = Math.min(
          1, tint.r * (old ? old.getX(i) : 1) * val * (1 + warm));
      rgb[i * 3 + 1] = Math.min(1, tint.g * (old ? old.getY(i) : 1) * val);
      rgb[i * 3 + 2] = Math.min(
          1, tint.b * (old ? old.getZ(i) : 1) * val * (1 - warm));
    }
    g.setAttribute('color', new THREE.BufferAttribute(rgb, 3));
    geos.push(g);
  }

  const merged = mergeGeometries(geos, false);
  if (!merged) {
    throw new Error(
        'mergeStatic: geometries would not merge (incompatible '
        + 'attribute sets) — merge compatible pieces per batch');
  }

  // ONE material: colour lives per-vertex; surface response comes from
  // the first piece (colour-only differences by contract).
  const material = new THREE.MeshStandardMaterial({ vertexColors: true });
  if (firstMat) {
    for (const k of _SCALARS) {
      if (firstMat[k] !== undefined) material[k] = firstMat[k];
    }
    for (const k of _MAPS) {
      if (firstMat[k]) material[k] = firstMat[k];
    }
    if (firstMat.emissive) material.emissive.copy(firstMat.emissive);
    if (firstMat.normalMap && firstMat.normalScale) {
      material.normalScale.copy(firstMat.normalScale);
    }
    // A shader-patched material does not survive being rebuilt, and
    // Material.clone() would not have saved it either (it copies
    // userData but NOT onBeforeCompile — a dead chain holding a live
    // cache key). Replay the chain instead, sharing the SOURCE uniform
    // map so one tickShaders still advances both.
    const patches = firstMat.userData && firstMat.userData.astraPatches;
    if (patches && patches.length) {
      material.userData.uniforms = firstMat.userData.uniforms;
      for (const p of patches) patchStandard(material, p);
    }
  }
  if (odd) {
    console.warn(
        `mergeStatic: ${odd} of ${found.length} pieces disagree with the `
        + 'first material about the shared surface response (side, '
        + 'emissive, roughness, map…) — those differences are LOST. '
        + 'Merge one material family per batch; only colour may vary.');
  }

  const out = new THREE.Mesh(merged, material);
  out.name = 'MergedStatic';
  out.castShadow = true;
  out.receiveShadow = true;
  return out;
}
