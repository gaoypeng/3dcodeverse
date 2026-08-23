// Bake THREE.InstancedMesh objects into plain meshes before GLTF export.
//
// three's GLTFExporter writes an InstancedMesh as ONE mesh node + the
// EXT_mesh_gpu_instancing extension.  Browser loaders understand that, but the
// python side (trimesh: measure / connectivity / contract / cross-sections) does
// not and sees a single copy at the node origin.  Baking every instance into a
// named child mesh keeps the GLB, the census and every gate consistent.
//
//   import { bakeInstancedMeshes } from './instances.mjs';
//   const baked = bakeInstancedMeshes(THREE, root);   // -> number of InstancedMesh objects replaced
//
// Each `InstancedMesh` named `Posts` becomes a `Group` named `Posts` (same local
// transform, userData, visibility) holding meshes `Posts_0 .. Posts_{n-1}` that
// share the source geometry (the exporter dedupes it) and material; per-instance
// colours (`instanceColor`) become per-colour material clones.

function instanceMaterial(THREE, source, color, cache) {
  if (!color) return source;
  const key = color.getHexString();
  let m = cache.get(key);
  if (!m) {
    m = Array.isArray(source) ? source.map((s) => s.clone()) : source.clone();
    for (const mat of Array.isArray(m) ? m : [m]) {
      if (mat.color && mat.color.isColor) mat.color.copy(color);
    }
    cache.set(key, m);
  }
  return m;
}

/** Replace one InstancedMesh by a Group of plain meshes; returns the Group. */
export function expandInstancedMesh(THREE, inst) {
  const group = new THREE.Group();
  group.name = inst.name || '';
  if (inst.matrixAutoUpdate) inst.updateMatrix();
  group.matrix.copy(inst.matrix);
  group.matrix.decompose(group.position, group.quaternion, group.scale);
  group.visible = inst.visible;
  group.userData = inst.userData;
  group.renderOrder = inst.renderOrder;
  const count = Math.max(0, Number(inst.count) || 0);
  const m4 = new THREE.Matrix4();
  const color = new THREE.Color();
  const matCache = new Map();
  for (let i = 0; i < count; i++) {
    inst.getMatrixAt(i, m4);
    let mat = inst.material;
    if (inst.instanceColor) {
      inst.getColorAt(i, color);
      mat = instanceMaterial(THREE, inst.material, color, matCache);
    }
    const mesh = new THREE.Mesh(inst.geometry, mat);
    mesh.name = group.name ? `${group.name}_${i}` : '';
    mesh.matrix.copy(m4);
    mesh.matrix.decompose(mesh.position, mesh.quaternion, mesh.scale);
    mesh.castShadow = inst.castShadow;
    mesh.receiveShadow = inst.receiveShadow;
    group.add(mesh);
  }
  // nested children (rare) ride along unchanged
  for (const child of [...inst.children]) group.add(child);
  return group;
}

/**
 * Bake every InstancedMesh under `root` (root itself included) in place, keeping
 * child order so census part order is unchanged.  Returns how many were baked.
 */
export function bakeInstancedMeshes(THREE, root) {
  const found = [];
  root.traverse((o) => {
    if (o.isInstancedMesh) found.push(o);
  });
  for (const inst of found) {
    const parent = inst.parent;
    const group = expandInstancedMesh(THREE, inst);
    if (!parent) continue; // a bare InstancedMesh root cannot be swapped; caller wraps it
    const idx = parent.children.indexOf(inst);
    parent.children[idx] = group;
    group.parent = parent;
    inst.parent = null;
  }
  root.updateWorldMatrix(true, true);
  return found.length;
}
