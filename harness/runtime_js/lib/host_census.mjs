/**
 * Deterministic scene census (page-side, pure over a THREE.Scene).
 * Counts meshes / instances / lights / triangles / custom-shader materials,
 * measures per top-level group (zone) world bboxes, classifies sky/ground
 * groups (shared rules: `backdrop.mjs`), computes a content bbox (sky + ground excluded) and pairwise zone
 * overlap statistics.  Returns plain JSON.
 */

import { classifyBackdrop } from './backdrop.mjs';
import { geometryTriangles as triCount } from './census.mjs';

export function isCustomShader(mat, THREE) {
  if (!mat) return false;
  if (mat.isShaderMaterial || mat.isRawShaderMaterial) return true;
  if (Object.prototype.hasOwnProperty.call(mat, 'onBeforeCompile')) return true;
  return mat.onBeforeCompile !== THREE.Material.prototype.onBeforeCompile;
}

function boxToJson(box) {
  if (!box || box.isEmpty()) return null;
  return {
    min: [box.min.x, box.min.y, box.min.z].map((v) => +v.toFixed(3)),
    max: [box.max.x, box.max.y, box.max.z].map((v) => +v.toFixed(3)),
    size: [box.max.x - box.min.x, box.max.y - box.min.y, box.max.z - box.min.z].map((v) => +v.toFixed(3)),
  };
}

function walkGroup(root, THREE) {
  let meshes = 0, instances = 0, tris = 0, lights = 0, custom = 0;
  const kinds = { sky: 0, ground: 0, content: 0 };
  const box = new THREE.Box3();       // content only
  const boxAll = new THREE.Box3();    // everything drawable
  const groundBox = new THREE.Box3();
  const tmp = new THREE.Box3();
  root.updateMatrixWorld(true);
  root.traverse((o) => {
    if (!o.visible) return;
    if (o.isLight) lights += 1;
    if (!(o.isMesh || o.isPoints || o.isLine || o.isSprite)) return;
    const count = o.isInstancedMesh ? o.count : 1;
    if (o.isMesh) { meshes += 1; instances += count; }
    tris += triCount(o.geometry) * count;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    if (mats.some((m) => isCustomShader(m, THREE))) custom += 1;
    if (!o.geometry) return;
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    const gb = o.geometry.boundingBox;
    if (!gb || gb.isEmpty()) return;
    const mb = new THREE.Box3();
    if (o.isInstancedMesh) {
      const m = new THREE.Matrix4();
      const n = Math.min(o.count, 4096);
      for (let i = 0; i < n; i++) {
        o.getMatrixAt(i, m);
        tmp.copy(gb).applyMatrix4(m).applyMatrix4(o.matrixWorld);
        mb.union(tmp);
      }
    } else {
      mb.copy(gb).applyMatrix4(o.matrixWorld);
    }
    if (mb.isEmpty()) return;
    const kind = classifyBackdrop(o, mb);   // shared rules (lib/backdrop.mjs); no content bbox yet here
    kinds[kind] += 1;
    boxAll.union(mb);
    if (kind === 'content') box.union(mb);
    if (kind === 'ground') groundBox.union(mb);
  });
  const kind = kinds.content ? 'content' : kinds.ground ? 'ground' : kinds.sky ? 'sky' : (lights && !meshes ? 'light' : 'empty');
  return { meshes, instances, tris, lights, custom, box, boxAll, groundBox, kind, kinds };
}

function overlapStats(groups) {
  const out = [];
  const content = groups.filter((g) => g.kind === 'content' && g.bbox);
  for (let i = 0; i < content.length; i++) {
    for (let j = i + 1; j < content.length; j++) {
      const a = content[i].bbox, b = content[j].bbox;
      const ix = Math.min(a.max[0], b.max[0]) - Math.max(a.min[0], b.min[0]);
      const iz = Math.min(a.max[2], b.max[2]) - Math.max(a.min[2], b.min[2]);
      if (ix <= 0 || iz <= 0) continue;
      const areaA = Math.max(1e-6, a.size[0] * a.size[2]);
      const areaB = Math.max(1e-6, b.size[0] * b.size[2]);
      const frac = (ix * iz) / Math.min(areaA, areaB);
      if (frac > 0.05) out.push({ a: content[i].name, b: content[j].name, footprint_overlap: +frac.toFixed(3) });
    }
  }
  return out.sort((x, y) => y.footprint_overlap - x.footprint_overlap).slice(0, 20);
}

/** Walk `scene` and return the census JSON. */
export function sceneCensus(scene, THREE) {
  scene.updateMatrixWorld(true);
  const groups = [];
  const totals = { meshes: 0, instances: 0, triangles: 0, lights: 0, custom_shader_meshes: 0 };
  const lightTypes = {};
  const materials = new Set();
  const customMaterials = [];
  scene.traverse((o) => {
    if (o.isLight) lightTypes[o.type] = (lightTypes[o.type] || 0) + 1;
    if (o.material) {
      for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
        if (!m || materials.has(m.uuid)) continue;
        materials.add(m.uuid);
        if (isCustomShader(m, THREE)) {
          customMaterials.push({
            name: m.name || '', type: m.type, on: o.name || o.type,
            kind: m.isShaderMaterial || m.isRawShaderMaterial ? 'ShaderMaterial' : 'onBeforeCompile',
            has_time_uniform: !!(m.uniforms && (m.uniforms.uTime || m.uniforms.time)),
          });
        }
      }
    }
  });
  const unionAll = new THREE.Box3();
  const content = new THREE.Box3();
  const groundAll = new THREE.Box3();
  for (const child of scene.children) {
    const g = walkGroup(child, THREE);
    const name = child.name || `${child.type}_${groups.length}`;
    groups.push({
      name, named: !!child.name, type: child.type, kind: g.kind, meshes: g.meshes, instances: g.instances,
      triangles: g.tris, lights: g.lights, custom_shader_meshes: g.custom, mesh_kinds: g.kinds,
      bbox: boxToJson(g.kind === 'content' ? g.box : g.boxAll),
    });
    totals.meshes += g.meshes; totals.instances += g.instances; totals.triangles += g.tris; totals.lights += g.lights; totals.custom_shader_meshes += g.custom;
    if (!g.boxAll.isEmpty()) unionAll.union(g.boxAll);
    if (!g.box.isEmpty()) content.union(g.box);
    if (!g.groundBox.isEmpty()) groundAll.union(g.groundBox);
  }
  const groundY = groundAll.isEmpty() ? null : groundAll.max.y;
  const contentBox = boxToJson(content.isEmpty() ? unionAll : content);
  return {
    totals,
    light_types: lightTypes,
    materials: materials.size,
    custom_materials: customMaterials,
    fog: scene.fog ? { type: scene.fog.type, near: scene.fog.near, far: scene.fog.far, density: scene.fog.density } : null,
    background: scene.background ? (scene.background.isColor ? '#' + scene.background.getHexString() : scene.background.type || 'texture') : null,
    environment: !!scene.environment,
    groups,
    bbox: boxToJson(unionAll),
    content_bbox: contentBox,
    ground_y: groundY !== null ? +groundY.toFixed(3) : (contentBox ? contentBox.min[1] : 0),
    has_ground: groundY !== null,
    overlaps: overlapStats(groups),
  };
}
