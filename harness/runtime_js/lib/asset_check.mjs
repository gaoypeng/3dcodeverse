// The scene-asset checker: import one asset module, call its build<Pascal>(THREE, {}) and
// measure the group it returns (size, triangles, meshes, materials, NaN positions).
//   node --import lib/resolve_three.mjs lib/asset_check.mjs FILE_URL EXPORT_NAME  → one JSON line
// (tracks/scene_assets.check_threejs_asset: spatial.node.run_node(three_hook=True)).

import * as THREE from 'three';
const [, , fileUrl, exportName] = process.argv;
const out = { ok: false, errors: [], warnings: [], size_m: null, tris: 0, meshes: 0, materials: 0 };
try {
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
      let bad = 0;
      g.traverse((o) => {
        if (!o.isMesh) return;
        out.meshes += 1;
        mats.add(o.material?.uuid ?? o.material);
        const pos = o.geometry?.attributes?.position;
        if (!pos) return;
        const arr = pos.array;
        for (let i = 0; i < arr.length; i++) if (!Number.isFinite(arr[i])) { bad += 1; break; }
        const idx = o.geometry.index;
        out.tris += ((idx ? idx.count : pos.count) / 3) * (o.isInstancedMesh ? o.count : 1);
      });
      out.materials = mats.size;
      if (bad) out.errors.push(`${bad} mesh(es) have NaN/Infinity vertex positions`);
      if (out.meshes === 0) out.errors.push('the returned group contains no meshes');
      if (box.isEmpty()) out.errors.push('the returned group has an empty bounding box');
      else {
        const s = new THREE.Vector3(); box.getSize(s);
        if (!Number.isFinite(s.x + s.y + s.z)) out.errors.push('the bounding box is not finite');
        else { out.size_m = [+s.x.toFixed(3), +s.y.toFixed(3), +s.z.toFixed(3)]; out.min_y = +box.min.y.toFixed(3); }
      }
      out.tris = Math.round(out.tris);
    }
  }
} catch (e) {
  out.errors.push(`${e?.name || 'Error'}: ${e?.message || String(e)}`);
  const stack = String(e?.stack || '').split('\n').slice(1, 4).filter((l) => l.includes(fileUrl.replace('file://', '')));
  if (stack.length) out.errors.push('at ' + stack.map((l) => l.trim()).join(' / '));
}
out.ok = out.errors.length === 0;
console.log(JSON.stringify(out));
