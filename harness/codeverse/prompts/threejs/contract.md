# three.js (static object) authoring contract — track `static_object`, language `threejs`

## Files (ES modules, three r182)
```
src/object.js            export function build(THREE) → THREE.Group   (the whole object)
src/parts/<snake>.js     export function build<Pascal>(THREE) → THREE.Group  (one per plan part)
```
`build()` imports the part builders with relative paths (`./parts/seat_cushion.js`),
adds each returned Group to a root Group named after the object, and returns the root.
The harness loads `src/object.js` in node, calls `build(THREE)`, and exports
`artifacts/object.glb` with `GLTFExporter`.  No renderer, scene, camera, light, DOM,
`requestAnimationFrame`, `fetch`, texture loading or file I/O in your code.

## Frame, units, placement
* **Y is up, +Z is the front, +X is the right.  Meters.**  `BoxGeometry(w, h, d)` is
  x-width, y-height, z-depth; `CylinderGeometry` axis is Y; `PlaneGeometry` lies in XY.
* Each part Group is returned **at its world pose** (position set on the Group or the
  meshes — geometry in meters, root scale (1,1,1), never `root.scale.set(...)` to resize).
* Lowest point at y = 0, footprint centred on the Y axis.  Real-world dimensions.
  The harness exports the object **exactly where you put it** (no automatic drop to
  the ground or re-centring): an off-ground / off-centre build only gets a build
  warning plus a contract-gate finding, so place every part at its plan centre.

## Naming
* Part Group: `g.name = "<PartName>"` — PascalCase exactly as the plan (`SeatCushion`);
  meshes inside may be unnamed or `<PartName>_<detail>`.  Unique names; instances get
  their own names (`Leg1`…`Leg4`) even if built by one function.
* Optional animation hook: `root.userData.tick = (dt) => { ... }` on a pivot Group
  (position + rotation only; identity scale).  No other userData contract.

## Imports (only these resolve)
```js
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
```
No CDN URLs, no `three/examples/js/*`, no `three/webgpu`, no `three/tsl`, no npm
packages other than `three`, no `import ... from 'codeverse'` / `../runtime_js`.

## Forbidden / limits
* No `document`, `window`, `canvas`, `Image`, `TextureLoader`, `fetch`, `XMLHttpRequest`,
  `Math.random()` (use a seeded hash), `Date`, `performance`.
* ShaderMaterial is NOT exported to GLB — use `MeshStandardMaterial` /
  `MeshPhysicalMaterial` only.  Colours via `new THREE.Color(0xRRGGBB)`; vertex colours
  allowed (`geometry.setAttribute('color', …)` + `vertexColors: true`).
* Triangles: hit the **DETAIL BUDGET in the prompt** (sized from this object's plan); the
  absolute ceiling is 600 k.  ≤ 300 meshes (use `InstancedMesh` or merged
  geometries for repeats); module evaluates + builds in < 20 s.  `InstancedMesh`
  named `Posts` is exported as a group `Posts` of meshes `Posts_0 … Posts_{n-1}`
  (instance matrices and colours baked), so gates and the census see every copy.

## Self-check (optional, keep it tiny, at the end of object.js)
If `object.js` exports `selfcheck(THREE, root)`, the harness calls it on the group
returned by `build(THREE)` right after validation, before export.  A throw fails the
build as `SelfCheckError` with your message and file:line, so assert only what you
are sure about (the contract gate measures the GLB anyway).
```js
export function selfcheck(THREE, root) {
  const box = new THREE.Box3().setFromObject(root);
  if (!(box.min.y > -0.002 && box.min.y < 0.002)) throw new Error(`object not on ground: min.y=${box.min.y}`);
  return box;
}
```

## COMPLETE minimal example (verified in node with three@0.182 + GLTFExporter)

`src/parts/seat.js`
```js
import * as THREE from 'three';
const SEAT_W = 0.40, SEAT_D = 0.40, SEAT_T = 0.04, SEAT_Y = 0.45;   // plan numbers (m)
export function buildSeat(THREE) {
  const g = new THREE.Group(); g.name = 'Seat';
  const mat = new THREE.MeshStandardMaterial({ color: 0x8b5a2b, roughness: 0.6 });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(SEAT_W, SEAT_T, SEAT_D), mat);
  mesh.position.set(0, SEAT_Y - SEAT_T / 2, 0);                    // top face at 0.45
  g.add(mesh);
  return g;
}
```
`src/parts/legs.js`
```js
import * as THREE from 'three';
const LEG_R = 0.018, LEG_H = 0.45 - 0.04 + 0.001;   // reaches 1 mm into the seat (weld)
const LEG_XZ = 0.15;
export function buildLegs(THREE) {
  const g = new THREE.Group(); g.name = 'Legs';
  const geo = new THREE.CylinderGeometry(LEG_R, LEG_R, LEG_H, 24);
  const mat = new THREE.MeshStandardMaterial({ color: 0x555a60, roughness: 0.35, metalness: 0.9 });
  [[1, 1], [-1, 1], [-1, -1], [1, -1]].forEach(([sx, sz], i) => {
    const m = new THREE.Mesh(geo, mat); m.name = `Leg${i + 1}`;
    m.position.set(sx * LEG_XZ, LEG_H / 2, sz * LEG_XZ);
    g.add(m);
  });
  return g;
}
```
`src/object.js`
```js
import * as THREE from 'three';
import { buildSeat } from './parts/seat.js';
import { buildLegs } from './parts/legs.js';
export function build(THREE) {
  const root = new THREE.Group(); root.name = 'Stool';
  root.add(buildSeat(THREE), buildLegs(THREE));
  return root;
}
```
