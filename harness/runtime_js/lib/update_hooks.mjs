/** Observe property-dispatched animation hooks without adding any update calls.
 * A missing observation is a diagnostic, not proof that an animation is static:
 * callbacks captured in closures and delayed callbacks can evade this sample.
 */
export function observeUpdateHooks(scene, exercise) {
  const rows = [], restore = [];
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
      rows.push(row);
      if (!row.instrumented) return;
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
  return { hooks: rows, unobserved: rows.filter(row => row.instrumented && row.calls === 0),
    limitation: 'Only property-dispatched hooks during the sampled update interval are observed. Captured callback references and delayed animations can be unobserved; inspect the parent forwarding before changing code.' };
}
