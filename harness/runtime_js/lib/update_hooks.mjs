/** Observe property-dispatched animation hooks without adding any update calls.
 * A hook counts as unobserved only when it was never called through userData AND
 * nothing under its object moved during the sample: a zone that calls a cached
 * reference (what prompts/tracks/scene_asset.j2 teaches) or one tickShaders() for
 * every uTime still animates it.  An empty stub (`() => {}`) has nothing to forward.
 */
const EMPTY_FN_RE = /^(?:function\s*\w*)?\s*\([^)]*\)\s*(?:=>)?\s*\{\s*\}$|^\(?[\w\s,]*\)?\s*=>\s*(?:undefined|void 0|\{\s*\})$/;

function uniformValue(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number' || typeof v === 'boolean') return v;
  if (v.isTexture) return null;
  if (typeof v.toArray === 'function') return v.toArray();
  return null;
}

/** Everything under `object` an update() can visibly change, as one string. */
function stateOf(object) {
  const parts = [];
  object.traverse((o) => {
    parts.push(o.visible, o.position.toArray(), o.quaternion.toArray(), o.scale.toArray());
    if (o.morphTargetInfluences) parts.push(o.morphTargetInfluences.slice());
    if (o.instanceMatrix) parts.push(o.instanceMatrix.version);
    const attrs = o.geometry && o.geometry.attributes;
    if (attrs) for (const k of Object.keys(attrs)) parts.push(attrs[k].version);
    for (const m of [].concat(o.material || [])) {
      parts.push(m.opacity);
      for (const [k, u] of Object.entries(m.uniforms || {})) parts.push(k, uniformValue(u && u.value));
    }
  });
  return JSON.stringify(parts);
}

export function observeUpdateHooks(scene, exercise) {
  const rows = [], restore = [], before = new Map();
  try {
    scene.traverse((object) => {
      const data = object.userData;
      if (!data) return;
      const descriptor = Object.getOwnPropertyDescriptor(data, 'update');
      if (!descriptor || typeof descriptor.value !== 'function') return;
      const original = descriptor.value;
      const path = [];
      for (let current = object; current && current !== scene; current = current.parent) {
        path.unshift(current.name || current.type || 'Object3D');
      }
      const row = { path: path.join('/') || scene.name || 'Scene', zone: path[0] || '', calls: 0,
        instrumented: !!(descriptor && descriptor.writable && descriptor.value === original) };
      row.empty = EMPTY_FN_RE.test(String(original).trim());
      rows.push(row);
      if (!row.instrumented) return;
      before.set(row, [object, stateOf(object)]);
      function observed(...args) {
        row.calls += 1;
        return Reflect.apply(original, this, args);
      }
      // Preserve aliases such as userData.tick === userData.update.
      for (const key of Object.keys(data)) {
        const property = Object.getOwnPropertyDescriptor(data, key);
        if (property && property.writable && property.value === original) {
          data[key] = observed;
          restore.push(() => { if (data[key] === observed) data[key] = original; });
        }
      }
    });
    exercise();
  } finally {
    for (const undo of restore.reverse()) undo();
  }
  for (const [row, [object, state]] of before) row.moved = stateOf(object) !== state;
  return { hooks: rows, unobserved: rows.filter(row => row.instrumented && row.calls === 0 && !row.moved && !row.empty),
    limitation: 'Only property-dispatched hooks during the sampled update interval are observed. Captured callback references and delayed animations can be unobserved; inspect the parent forwarding before changing code.' };
}
