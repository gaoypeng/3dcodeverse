// Bake THREE.InstancedMesh objects into plain meshes before GLTF export.
//
// three's GLTFExporter writes an InstancedMesh as ONE mesh node + the
// EXT_mesh_gpu_instancing extension.  Browser loaders understand that, but the
// python side (trimesh: measure / connectivity / contract / cross-sections) does
// not and sees a single copy at the node origin.  Baking every instance into a
// named mesh keeps the GLB, the census and every gate consistent.
//
//   import { bakeInstancedMeshes } from './instances.mjs';
//   const { root: baked, count } = bakeInstancedMeshes(THREE, root);
//
// Each `InstancedMesh` named `Posts` is REPLACED, in its parent, by sibling meshes
// `Posts_0 .. Posts_{n-1}` (no wrapper group: the contract gate counts top-level
// `Name_i` nodes as the instances of plan part `Name`, conventions.split_instance).
// They share the source geometry (the exporter dedupes it) and material, carry the
// InstancedMesh's own transform times each instance matrix, and per-instance colours
// (`instanceColor`) become per-colour material clones.  A bare InstancedMesh root has
// no parent, so it becomes a Group of the same name holding the meshes.

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

/** The plain objects that replace one InstancedMesh, posed in its parent's frame. */
function expand(THREE, inst) {
  if (inst.matrixAutoUpdate) inst.updateMatrix();
  const out = [];
  const pose = (obj, local) => {
    obj.matrix.multiplyMatrices(inst.matrix, local);
    obj.matrix.decompose(obj.position, obj.quaternion, obj.scale);
    obj.visible = obj.visible && inst.visible;
    out.push(obj);
  };
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
    mesh.name = inst.name ? `${inst.name}_${i}` : '';
    mesh.userData = inst.userData;
    mesh.renderOrder = inst.renderOrder;
    mesh.castShadow = inst.castShadow;
    mesh.receiveShadow = inst.receiveShadow;
    pose(mesh, m4.clone());
  }
  // nested children (rare) ride along in the same frame
  for (const child of [...inst.children]) {
    if (child.matrixAutoUpdate) child.updateMatrix();
    inst.remove(child);
    pose(child, child.matrix.clone());
  }
  return out;
}

/**
 * Bake every InstancedMesh under `root` (root itself included) in place, keeping
 * child order.  Returns `{ root, count }`: `root` is a new Group only when `root`
 * itself was an InstancedMesh; `count` is how many InstancedMesh objects were baked.
 */
export function bakeInstancedMeshes(THREE, root) {
  const found = [];
  root.traverse((o) => {
    if (o.isInstancedMesh) found.push(o);
  });
  let out = root;
  for (const inst of found) {
    const parent = inst === root ? null : inst.parent;
    const parts = expand(THREE, inst);
    if (!parent) {
      out = new THREE.Group();
      out.name = inst.name || '';
      out.userData = inst.userData; // the root's userData (e.g. tick) stays on the root
      for (const p of parts) out.add(p);
      continue;
    }
    const idx = parent.children.indexOf(inst);
    for (const p of parts) p.parent = parent;
    parent.children.splice(idx, 1, ...parts);
    inst.parent = null;
  }
  out.updateWorldMatrix(true, true);
  return { root: out, count: found.length };
}
