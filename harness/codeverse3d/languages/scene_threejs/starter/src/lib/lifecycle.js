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

/** Every object in a factory's tree when it claimed ownership (attachDisposal):
 * how a check tells what the library built from what its caller built around it.
 * Weak, so it keeps nothing alive; caller additions made afterwards are not in it.
 */
const BUILT = new WeakSet();

/** Did a library factory build this object (it was in the tree it claimed)? */
export function isLibraryBuilt(object) {
    return BUILT.has(object);
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
    if (typeof root.traverse === 'function') root.traverse((object) => BUILT.add(object));
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
