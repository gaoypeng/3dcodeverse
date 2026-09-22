// Geometry checks on a THREE.Object3D tree: triangle counts, world boxes and the first mesh
// with NaN/Infinity positions (the offending mesh and part named).  Pure three.js math — node and
// browser.  What the exported object measures (parts, tris, boxes, materials) is spatial/measure.py's
// job, read off the GLB.
//
//   import { findNonFinitePositions, geometryTriangles, worldBox } from './census.mjs';

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

/** World-space AABB of a subtree as [[minx,miny,minz],[maxx,maxy,maxz]] or null if empty. */
export function worldBox(THREE, root) {
  root.updateWorldMatrix(true, true);
  const box = new THREE.Box3().setFromObject(root, true);
  if (box.isEmpty()) return null;
  const r = (v) => +v.toFixed(5);
  return [box.min.toArray().map(r), box.max.toArray().map(r)];
}

function geometryHasNonFinite(geometry) {
  if (!geometry || !geometry.attributes || !geometry.attributes.position) return false;
  const a = geometry.attributes.position.array;
  for (let i = 0; i < a.length; i++) {
    if (!Number.isFinite(a[i])) return true;
  }
  return false;
}

/**
 * First mesh under `root` whose position attribute holds NaN/Infinity, or null.
 * `part` is the root's direct child on the path to the mesh (the plan part it
 * belongs to — the mesh itself when it is a direct child, '' when it is the
 * root); `ancestor` is the nearest named ancestor below the root.
 * @returns {{name: string, type: string, part: string, ancestor: string} | null}
 */
export function findNonFinitePositions(root) {
  const visit = (o, path) => {
    if (isMeshLike(o) && geometryHasNonFinite(o.geometry)) {
      const below = path.slice(1); // ancestors strictly below root, nearest last
      const named = below.filter((a) => a.name);
      return {
        name: o.name || '', type: o.type,
        part: path.length === 0 ? '' : (below.length ? below[0] : o).name || '',
        ancestor: named.length ? named[named.length - 1].name : '',
      };
    }
    for (const c of o.children) {
      const hit = visit(c, [...path, o]);
      if (hit) return hit;
    }
    return null;
  };
  return visit(root, []);
}
