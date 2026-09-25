// The scene-asset checker: import one asset module, call its build<Pascal>(THREE, {}) and
// measure the group it returns (size, triangles, meshes, materials, NaN positions).
//   node --import lib/resolve_three.mjs lib/asset_check.mjs FILE_URL EXPORT_NAME [LIFECYCLE_URL]
//   → one JSON line
// (tracks/scene_assets.check_threejs_asset: spatial.node.run_node(three_hook=True)).
// An asset may compose the workspace's effect library (D100).  LIFECYCLE_URL is that library's
// lib/lifecycle.js, the SAME module instance the asset's own imports load, so `isLibraryBuilt` says
// which objects a factory built: their triangles are `lib_tris`, not `tris`, and a factory's foot
// is its origin (a tree's roots go below grade by design), not its lowest vertex.

import * as THREE from 'three';
const [, , fileUrl, exportName, lifecycleUrl] = process.argv;
const out = { ok: false, errors: [], warnings: [], size_m: null, tris: 0, lib_tris: 0, meshes: 0, materials: 0 };
try {
  const isLibraryBuilt = lifecycleUrl ? (await import(lifecycleUrl)).isLibraryBuilt : () => false;
  const mod = await import(fileUrl);
  const fn = mod[exportName] ?? mod.default;
  if (typeof fn !== 'function') {
    out.errors.push(`missing export: this file must \`export function ${exportName}(THREE, opts = {})\` (found: ${Object.keys(mod).join(', ') || 'nothing'})`);
  } else {
    const g = fn(THREE, {});
    if (!g || !g.isObject3D) {
      out.errors.push(`${exportName}(THREE) must return a THREE.Group / Object3D (got ${Object.prototype.toString.call(g)})`);
    } else {
      const box = new THREE.Box3().setFromObject(g);
      const mats = new Set();
      let bad = 0, footY = Infinity;
      const part = new THREE.Box3(), at = new THREE.Vector3();
      g.traverse((o) => {
        const built = isLibraryBuilt(o);
        if (built && !isLibraryBuilt(o.parent)) footY = Math.min(footY, o.getWorldPosition(at).y);
        if (!o.isMesh) return;
        out.meshes += 1;
        mats.add(o.material?.uuid ?? o.material);
        const pos = o.geometry?.attributes?.position;
        if (!pos) return;
        const arr = pos.array;
        for (let i = 0; i < arr.length; i++) if (!Number.isFinite(arr[i])) { bad += 1; break; }
        const idx = o.geometry.index;
        const tris = ((idx ? idx.count : pos.count) / 3) * (o.isInstancedMesh ? o.count : 1);
        if (built) { out.lib_tris += tris; return; }
        out.tris += tris;
        if (o.isInstancedMesh) { o.computeBoundingBox(); part.copy(o.boundingBox); }
        else { o.geometry.computeBoundingBox(); part.copy(o.geometry.boundingBox); }
        if (!part.isEmpty()) footY = Math.min(footY, part.applyMatrix4(o.matrixWorld).min.y);
      });
      out.materials = mats.size;
      if (bad) out.errors.push(`${bad} mesh(es) have NaN/Infinity vertex positions`);
      if (out.meshes === 0) out.errors.push('the returned group contains no meshes');
      if (box.isEmpty()) out.errors.push('the returned group has an empty bounding box');
      else {
        const s = new THREE.Vector3(); box.getSize(s);
        if (!Number.isFinite(s.x + s.y + s.z)) out.errors.push('the bounding box is not finite');
        else {
          out.size_m = [+s.x.toFixed(3), +s.y.toFixed(3), +s.z.toFixed(3)];
          out.min_y = +(Number.isFinite(footY) ? footY : box.min.y).toFixed(3);
        }
      }
      out.tris = Math.round(out.tris);
      out.lib_tris = Math.round(out.lib_tris);
    }
  }
} catch (e) {
  out.errors.push(`${e?.name || 'Error'}: ${e?.message || String(e)}`);
  const stack = String(e?.stack || '').split('\n').slice(1, 4).filter((l) => l.includes(fileUrl.replace('file://', '')));
  if (stack.length) out.errors.push('at ' + stack.map((l) => l.trim()).join(' / '));
}
out.ok = out.errors.length === 0;
console.log(JSON.stringify(out));
