/** Explicit, construction-time ownership for procedural effects.
 *
 * Capture immediately after building the effect. Later caller-added children
 * are not owned. Material textures are deliberately excluded: environment maps,
 * cached maps and other borrowed assets often outlive a single effect. Add only
 * textures created by this factory to the resource set before attaching it.
 */

/** Collect geometry, material (including custom shadows) and instance buffers.
 * Set a category to false when it is borrowed, then add any owned exceptions.
 * This is a snapshot, not a traversal performed during disposal.
 */
export function snapshotResources(root, {
    geometries = true, materials = true, instances = true,
} = {}) {
    const resources = new Set();
    root.traverse((object) => {
        if (geometries && object.geometry) resources.add(object.geometry);
        if (instances && object.isInstancedMesh) resources.add(object);
        if (materials) {
            for (const material of [].concat(object.material || [],
                object.customDepthMaterial || [], object.customDistanceMaterial || [])) {
                if (material) resources.add(material);
            }
        }
    });
    return resources;
}

/** Install userData.dispose() for an explicit iterable of owned resources.
 * Duplicate resources are released once; repeated disposal is harmless. A
 * resource may be a Three.js disposable or {dispose(){...}} for external state.
 * Cleanup continues after an individual failure, then reports all failures.
 */
export function attachDisposal(root, resources) {
    const owned = new Set(resources);
    for (const resource of owned) {
        if (!resource || typeof resource.dispose !== 'function') {
            throw new TypeError('Every owned resource must implement dispose()');
        }
    }
    let disposed = false;
    root.userData.dispose = () => {
        if (disposed) return false;
        disposed = true;
        const errors = [];
        for (const resource of owned) {
            try { resource.dispose(); } catch (error) { errors.push(error); }
        }
        owned.clear();
        if (errors.length) throw new AggregateError(errors, 'Effect resource disposal failed');
        return true;
    };
    return root;
}
