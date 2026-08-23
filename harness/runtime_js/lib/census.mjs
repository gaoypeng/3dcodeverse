// Census of a THREE.Object3D tree: per-part triangle counts, world bboxes,
// materials, NaN checks.  Pure three.js math — usable in node and browser.
//
//   import { objectCensus, triangleCount, worldBox } from './census.mjs';

/** Triangles of one mesh geometry (index or non-indexed), honouring draw range. */
export function geometryTriangles(geometry) {
  if (!geometry || !geometry.attributes || !geometry.attributes.position) return 0;
  const count = geometry.index ? geometry.index.count : geometry.attributes.position.count;
  const range = geometry.drawRange ? Math.min(geometry.drawRange.count, count) : count;
  return Math.floor(range / 3);
}

function isMeshLike(obj) {
  return !!(obj.isMesh || obj.isSkinnedMesh || obj.isInstancedMesh);
}

/** Triangle count of a subtree (InstancedMesh multiplied by its instance count). */
export function triangleCount(root) {
  let n = 0;
  root.traverseVisible((o) => {
    if (!isMeshLike(o)) return;
    const tris = geometryTriangles(o.geometry);
    n += o.isInstancedMesh ? tris * o.count : tris;
  });
  return n;
}

/** World-space AABB of a subtree as [[minx,miny,minz],[maxx,maxy,maxz]] or null if empty. */
export function worldBox(THREE, root) {
  root.updateWorldMatrix(true, true);
  const box = new THREE.Box3().setFromObject(root, true);
  if (box.isEmpty()) return null;
  const r = (v) => +v.toFixed(5);
  return [box.min.toArray().map(r), box.max.toArray().map(r)];
}

/** True when any position attribute contains a non-finite value. */
export function hasNonFinitePositions(root) {
  let bad = false;
  root.traverse((o) => {
    if (bad || !isMeshLike(o) || !o.geometry || !o.geometry.attributes.position) return;
    const a = o.geometry.attributes.position.array;
    for (let i = 0; i < a.length; i++) {
      if (!Number.isFinite(a[i])) {
        bad = true;
        return;
      }
    }
  });
  return bad;
}

function materialSummary(m) {
  const out = { name: m.name || '', type: m.type };
  if (m.color && m.color.isColor) out.color = '#' + m.color.getHexString();
  if (typeof m.roughness === 'number') out.roughness = +m.roughness.toFixed(3);
  if (typeof m.metalness === 'number') out.metalness = +m.metalness.toFixed(3);
  if (m.transparent) out.opacity = +(m.opacity ?? 1).toFixed(3);
  if (m.map) out.map = true;
  return out;
}

/** Unique materials used under `root` (by object identity). */
export function materialsOf(root) {
  const seen = new Set();
  const out = [];
  root.traverse((o) => {
    if (!isMeshLike(o) || !o.material) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of mats) {
      if (!m || seen.has(m)) continue;
      seen.add(m);
      out.push(materialSummary(m));
    }
  });
  return out;
}

function namedDescendants(part) {
  const names = [];
  part.traverse((o) => {
    if (o !== part && o.name) names.push(o.name);
  });
  return names;
}

/**
 * Census of an object group: each direct child is a "part".
 * @returns {{object_name, parts, tri_count, bbox, materials, n_meshes}}
 */
export function objectCensus(THREE, group) {
  group.updateWorldMatrix(true, true);
  const parts = group.children.map((child) => {
    const box = worldBox(THREE, child);
    return {
      name: child.name || '',
      type: child.type,
      tri_count: triangleCount(child),
      bbox_min: box ? box[0] : null,
      bbox_max: box ? box[1] : null,
      children: namedDescendants(child),
    };
  });
  let nMeshes = 0;
  group.traverse((o) => {
    if (isMeshLike(o)) nMeshes += 1;
  });
  const box = worldBox(THREE, group);
  return {
    object_name: group.name || '',
    parts,
    tri_count: triangleCount(group),
    bbox: box ? { min: box[0], max: box[1] } : null,
    materials: materialsOf(group),
    n_meshes: nMeshes,
  };
}
