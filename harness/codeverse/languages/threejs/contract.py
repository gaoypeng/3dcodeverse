"""Built-in fallback for the three.js authoring contract (prompts/threejs/contract.md wins)."""

from __future__ import annotations

CONTRACT_FALLBACK = """\
# three.js static-object contract (raw ESM, no SDK)

Files (all under `src/`):
* `src/parts/<snake_name>.js` — one file per part:
  `export function build<PascalName>(THREE) { ...; return group; }`
  The returned `THREE.Group` is named `<PascalName>` and already sits at its WORLD pose
  (no re-positioning happens in object.js).
* `src/object.js` — `export function build(THREE) { ... return root; }` imports every part
  builder, adds each part group to one root `THREE.Group` whose `.name` is the object name,
  and returns it.  Optional: `root.userData.tick = (t, dt) => { ... }` for idle animation.

Frame & units: Y is UP, +Z is the FRONT (towards the default camera), +X is the right.
Meters.  The object stands on the ground: lowest point at y = 0, footprint centred on
the Y axis.  Real-world sizes (a chair seat is ~0.45 m high, not 45).

Imports allowed: `import * as THREE from 'three'`, `three/addons/...` (e.g.
`three/addons/utils/BufferGeometryUtils.js`, `three/addons/geometries/RoundedBoxGeometry.js`),
and relative `./` files inside `src/`.  Nothing else: no CDN/http URLs, no npm packages,
no `fetch`, no `document`/`window`, no WebGLRenderer/cameras/lights/scenes in object code —
the harness renders and exports for you.

Geometry & materials: BufferGeometry only; `MeshStandardMaterial`/`MeshPhysicalMaterial`
with explicit `color`, `roughness`, `metalness`.  Textures are NOT exportable from node —
use materials and vertex detail.  Every mesh gets a meaningful `.name`.  Keep total
triangles well under 600k.  Parts must physically touch (no floating pieces) and must not
interpenetrate visibly.

Build: the harness runs `build(THREE)` in node, validates the group (≥1 mesh, finite
bbox, no NaN), and exports `artifacts/object.glb` with one named node per part.
"""
