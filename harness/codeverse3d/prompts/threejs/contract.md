# three.js (static object) authoring contract — track `static_object`, language `threejs`

## Files (ES modules, three r182)
```
src/object.js            export function build(THREE) → THREE.Group   (the whole object)
src/parts/<snake>.js     export function build<Pascal>(THREE) → THREE.Group  (one per plan part)
```
`build()` imports the part builders with relative paths (`./parts/seat_cushion.js`),
adds each returned Group to a root Group named after the object, and returns the root.
The harness loads `src/object.js` in node, calls `build(THREE)`, validates the Group
(a group with no meshes, a NaN / Infinity vertex position or an empty bounding box fails
the build and names the part), runs the optional `selfcheck`, and exports
`artifacts/object.glb` with `GLTFExporter` **exactly as authored** — nothing re-centres,
grounds or rescales it.  The lint derives the expected export from the file stem
(`src/parts/seat_cushion.js` → `buildSeatCushion`): a misspelt name is a WARN, no `build*`
export at all is an ERROR, and a part file nothing imports is a WARN whose geometry is
simply absent from the GLB.  Any other relative file under `src/` (a shared helper module)
is allowed.

## Frame, units, placement
* **Y is up, +Z is the front, +X is the right.  Meters.**
* three.js geometry facts: `BoxGeometry(w, h, d)` is x-width, y-height, z-depth;
  `CylinderGeometry` / `ConeGeometry` / `LatheGeometry` axis is Y (a lathe profile is
  `Vector2(radius, y)`, normals outward when the radius runs 0 → R → 0 bottom-up);
  `TorusGeometry`, `PlaneGeometry`, `ShapeGeometry` and `ExtrudeGeometry` lie in the XY plane
  (the extrusion grows along +Z); every primitive is **centred on its own origin**, so a part's
  `position` is its centre, not its base.
* Each part Group is returned **at its world pose** (position set on the Group or the meshes),
  geometry in meters, root scale (1, 1, 1): `root.scale.set(...)` makes the bounds and the
  contract gate read the geometry inconsistently.
* The build warns when the object needs more than 10 mm of translation to stand on y = 0
  centred on the Y axis; `check_contract` measures the same two things (below).

## Naming
* Part Group: `g.name = "<PartName>"` — PascalCase exactly as the plan (`SeatCushion`).  The
  GLB's node names are the Group / Mesh names, and the top-level node name is how every gate
  finds the part; `new THREE.Box3().setFromObject(root)` is the box `check_contract` measures.
  Unique names: a reused name makes the loader re-parent one of the nodes, and the gates then
  measure the wrong object.  Instances get their own names (`Leg_0`…`Leg_3`) even when one
  function builds them.  A mesh with no `.name` is a build warning (the census and the judge
  refer to meshes by name).
* `root.userData.tick` is not called, rendered or exported on this track (the census only
  records whether it exists); the judged pose is the one `build` returns.

## Imports
Imports that resolve: `three`, any `three/addons/...` module (for example
`three/addons/utils/BufferGeometryUtils.js` for `mergeGeometries`,
`three/addons/geometries/RoundedBoxGeometry.js`), and relative `./` files under `src/`.
A URL, another npm package, `three/webgpu`, `three/tsl`, a path escaping `src/`, a missing
file or a name the module does not export is a lint ERROR (`bad_import`).  Names removed
before r182 and therefore undefined: `mergeBufferGeometries` (now `mergeGeometries`),
`three/examples/js/*`, `Geometry`, `Face3`, `sRGBEncoding`, `outputEncoding`.  Copying
three's sources into `src/` gives two copies of the library and `instanceof` failures.
`mergeGeometries` returns `null` (logging "All geometries must have compatible attributes")
when one input is indexed or carries an attribute another lacks (`uv`, `color`); the next call
on that `null` fails the build.

## Sandbox (what the lint reports)
* Lint ERRORs: `document`, `window`, `navigator`, `localStorage`, `fetch`, `XMLHttpRequest`,
  `WebSocket`, `require()`, `process.`, `eval`, `TextureLoader` / `ImageLoader` / `FileLoader`,
  `WebGLRenderer`, `requestAnimationFrame`.  Lint WARNs, ignored at export: a camera, a light
  (exported as extras, not used by the harness lighting), a `Scene` in place of a Group.
* Not linted: the exporter replaces `Math.random` with a seeded generator (`--seed`), so its
  draws repeat from build to build; the clock (`Date.now()`, `performance.now()`) does not, and
  geometry that depends on it changes between the builds the harness measures and renders.

## Materials
* `MeshStandardMaterial` / `MeshPhysicalMaterial` map to glTF PBR: colour, roughness, metalness,
  emissive and vertex colours (`geometry.setAttribute('color', …)` + `vertexColors: true`,
  exported as `COLOR_0`) survive the export.  `ShaderMaterial`, `MeshToonMaterial` and every
  texture do not: textures are stripped at export with a build warning, a shader material
  exports as a plain one.
* A hex colour is read as sRGB; `material.color.r` is the linear value, not the hex byte.
* An `InstancedMesh` named `Posts` is exported as sibling meshes `Posts_0 … Posts_{n-1}` in
  its place (instance matrices and colours baked), so gates see every copy as an instance of
  `Posts` — and a copy that touches nothing is a floating island.

## What is measured (the gates, with their thresholds)
* **Contract** (`check_contract`, on the exported GLB against the plan): each plan part's bbox
  centre and extents are within max(10 mm, 10 % of the plan extent) per axis — WARN beyond,
  ERROR beyond 3 × that; the whole object's lowest point within 10 mm of y = 0 (WARN; ERROR
  past 30 mm) and its footprint centre within max(10 mm, 10 % of the footprint) of the Y axis
  (WARN); an object whose height runs along the wrong axis is reported as lying on its side;
  a top-level node the plan does not name is reported as "not in the plan".
* **Connectivity** (`check_connectivity`, per top-level part): two parts are in contact when
  their surface gap is ≤ 2 mm.  A part with no contact path to the supported assembly is a
  floating ERROR, with the nearest supported part and the translation that closes the gap
  (parts under 10 mm in their largest extent are INFO only).  Penetration deeper than 2 mm
  across a visible share of the part is a WARN, deeper than 10 mm an ERROR.  Inside one part,
  disconnected islands smaller than 5 % of the part are a `stray_islands` WARN — what a merge
  leaves behind: a degenerate triangle, a duplicated ring at a lathe seam, a vertex at the
  origin.
* **Triangle budget** (the depth gate, sized from the plan's part × instance count): WARN below
  350 triangles per built object (floor clamped to 1 500 – 40 000), ERROR above 20 000 per
  built object (ceiling clamped to 60 000 – 600 000); the target is about 2 000 per built
  object; a build over 3 s per built object (at least 20 s) is a WARN.  Detail inside a plan
  part is invisible to the part-level gates; a new top-level node is a part.
* **Build**: a geometry with a zero extent or radius, or `scale.set(0, 0, 0)` to hide something,
  produces NaN positions / normals and fails the export (hide with `visible = false`, or do not
  add it).  The build is killed at `build_timeout_s` (300 s by default) and has a memory
  limit; the triangle ceiling above is the only count enforced.

## Self-check (optional, at the end of object.js)
If `object.js` exports `selfcheck(THREE, root)`, the harness calls it on the group returned
by `build(THREE)` after validation, before export.  A throw fails the build as
`SelfCheckError` with your message and file:line, so assert only what you are sure about
(the contract gate measures the GLB anyway).
```js
export function selfcheck(THREE, root) {
  const box = new THREE.Box3().setFromObject(root);
  if (Math.abs(box.min.y) > 0.01) throw new Error(`object not on ground: min.y=${box.min.y}`);
}
```

## The interface, and nothing else (runs in node with three@0.182 + GLTFExporter)

`src/parts/body.js`
```js
import * as THREE from 'three';
const SIZE = [0.3, 0.2, 0.3], CENTRE = [0, 0.1, 0];   // this part's plan bbox (world, metres)
export function buildBody(THREE) {
  const g = new THREE.Group(); g.name = 'Body';         // the plan's part name
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...SIZE), new THREE.MeshStandardMaterial({ color: 0x8a8f98 }));
  mesh.name = 'BodyShell';
  mesh.position.set(...CENTRE);                          // world pose: the centre, not the base
  g.add(mesh);
  return g;
}
```
`src/object.js`
```js
import * as THREE from 'three';
import { buildBody } from './parts/body.js';
export function build(THREE) {
  const root = new THREE.Group(); root.name = 'Object'; // the plan's object name
  root.add(buildBody(THREE));                           // one line per plan part
  return root;
}
```
What the part is made of — primitives, a lathe profile, an extruded outline, a swept tube,
merged pieces, instanced repeats, raw `BufferGeometry` — is the brief's and the plan's.
