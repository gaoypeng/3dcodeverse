---
name: 3d-lang-threejs
description: "Use when writing, refining or repairing a static_object in three.js (language threejs): the ESM files and build* exports the lint expects, the Y-up metre frame and three.js primitive facts (centred on the origin, lathe/torus axes), naming, the import and sandbox rules, what survives GLTFExporter, the thresholds the contract / connectivity / triangle-budget gates of `3dcode check` measure, and the 3dcode loop."
license: Apache-2.0
compatibility: three r182 ESM loaded in node by the 3dcode build; export with GLTFExporter exactly as authored.
metadata:
  verified: "2026-11-07"
---

# three.js — static objects

`build(THREE)` returns a `THREE.Group` with at least one mesh; the build owns the renderer,
the export and the cameras (a renderer or render loop in object code is a lint ERROR, a light
or camera a WARN the export ignores).

## Files (ES modules, three r182)
```
src/object.js            export function build(THREE) → THREE.Group   (the whole object)
src/parts/<snake>.js     export function build<Pascal>(THREE) → THREE.Group  (one per plan part)
```
`build()` imports the part builders with relative paths, adds each Group to a root Group named
after the object, returns the root.  The build loads `src/object.js` in node, calls
`build(THREE)`, validates the Group (no meshes, a NaN / Infinity vertex or an empty bounding box
fails the build naming the part), runs the optional `selfcheck`, exports `artifacts/object.glb`
AS AUTHORED.  The lint derives the export name from the file stem (`seat_cushion.js` →
`buildSeatCushion`): misspelt WARN, no `build*` export ERROR, a part file nothing imports WARN
(its geometry is absent).  Other relative helper files under `src/` are fine.
`references/interface.md` is the interface, executable.

## Frame, units, placement
* **Y up, +Z front, +X right, metres.**  `BoxGeometry(w, h, d)` is x-width, y-height, z-depth;
  `CylinderGeometry` / `ConeGeometry` / `LatheGeometry` axis is Y (a lathe profile is
  `Vector2(radius, y)`, normals outward when the radius runs 0 → R → 0 bottom-up);
  `TorusGeometry`, `PlaneGeometry`, `ShapeGeometry`, `ExtrudeGeometry` lie in XY (extrusion
  along +Z); every primitive is **centred on its origin**, so a part's `position` is its
  CENTRE, not its base — the one term behind most floating and sunken parts here.
* Each part Group is returned at its world pose, geometry in metres, root scale (1, 1, 1)
  (`root.scale.set(...)` makes the bounds and the contract gate disagree).  The build warns when
  the object needs > 10 mm of translation to stand on y = 0 centred on the Y axis.

## Naming
* `g.name = "<PartName>"` — the plan's PascalCase; the GLB's top-level node name is how every
  gate finds the part, `new THREE.Box3().setFromObject(root)` is the box the `contract` gate
  measures.  Unique names (a reused name makes the loader re-parent a node); instances get
  their own (`Leg_0`…`Leg_3`).  A mesh with no `.name` is a build warning.
* `root.userData.tick` is never called on this track; the judged pose is what `build` returns.

## Imports and sandbox (the lint)
* Resolve: `three`, `three/addons/...` (e.g. `three/addons/utils/BufferGeometryUtils.js` for
  `mergeGeometries`, `three/addons/geometries/RoundedBoxGeometry.js`), relative `./` files
  under `src/`.  A URL, another package, `three/webgpu`, `three/tsl`, a path escaping `src/`,
  a missing file or a name the module does not export is a lint ERROR (`bad_import`); never
  copy three's sources into `src/` (two copies of the library, `instanceof` failures).
  Undefined before r182: `mergeBufferGeometries` (now `mergeGeometries`), `three/examples/js/*`,
  `Geometry`, `Face3`, `sRGBEncoding`, `outputEncoding`.  `mergeGeometries` returns `null` when
  one input is indexed or lacks an attribute another has (`uv`, `color`).
* Lint ERRORs: `document`, `window`, `navigator`, `localStorage`, `fetch`, `XMLHttpRequest`,
  `WebSocket`, `require()`, `process.`, `eval`, `TextureLoader` / `ImageLoader` / `FileLoader`,
  `WebGLRenderer`, `requestAnimationFrame`.  WARN: a camera, a light, a `Scene` as root.
* `Math.random` is replaced by a seeded generator at build; the clock (`Date.now()`,
  `performance.now()`) is not — geometry depending on it differs between the measured and the
  rendered build.

## Materials
* `MeshStandardMaterial` / `MeshPhysicalMaterial` colour, roughness, metalness, emissive and
  vertex colours (`setAttribute('color', …)` + `vertexColors: true`, exported as `COLOR_0`)
  survive the export.
  `ShaderMaterial`, `MeshToonMaterial` and every texture do not (textures stripped with a build
  warning; a shader material exports plain).  A hex colour is sRGB; `material.color.r` is linear.
* An `InstancedMesh` named `Posts` exports as sibling meshes `Posts_0 … Posts_{n-1}` with the
  matrices baked, so the gates see every copy as an instance of `Posts` — and a copy that
  touches nothing is a floating island.

## What is measured
* **the `contract` gate of `3dcode check`** (the GLB vs the plan): each part's bbox centre and extents within
  max(10 mm, 10 % of the plan extent) per axis — WARN beyond, ERROR beyond 3 ×; lowest point
  within 10 mm of y = 0 (WARN; ERROR past 30 mm); footprint centre within max(10 mm, 10 % of
  the footprint) of the Y axis (WARN); height along the wrong axis is "lying on its side"; a
  top-level node the plan does not name is "not in the plan" — no helper meshes, bounding boxes
  or axes in the GLB.
* **the `connectivity` gate of `3dcode check`** (per top-level part): contact when the surface gap ≤ 2 mm; a
  part with no contact path to the supported assembly is a floating ERROR (cap 0.60) with the
  translation that closes the gap (parts under 10 mm are INFO); penetration > 2 mm WARN,
  > 10 mm ERROR (cap 0.70); islands under 5 % of the part are `stray_islands` WARN — what a
  merge leaves: a degenerate triangle, a duplicated ring at a lathe seam, a vertex at the origin.
* **Triangle budget** (sized from parts × instances): WARN below 350 per built object (floor
  clamped to 1 500–40 000), ERROR above 20 000 per built object (ceiling 60 000–600 000);
  target about 2 000 per built object; a build over 3 s per built object (≥ 20 s) WARN; killed
  at 300 s; the build also has a memory limit, but the triangle ceiling is the only count
  enforced.  Detail inside a part is invisible to the part-level gates; a new top-level node is
  a part.
* **Build**: a zero extent or radius, or `scale.set(0, 0, 0)` to hide, produces NaN and fails
  the export (`visible = false`, or do not add it).

## Self-check (optional)
`export function selfcheck(THREE, root)` is called on the built group before export; a throw
fails the build as `SelfCheckError` with your message — assert only what you are sure of.

## The loop
write every part file → `3dcode check` (build, connectivity, contract) → fix each ERROR →
`3dcode check` again until no ERROR → `3dcode judge`.  `isolate --part X` settles "where did
that fragment come from".  Every WARN left at the end goes to the judge with the renders; a WARN
never caps.  The round's hand-over, the one look at renders per refine round and the stop rules are the `3dcodeverse` skill's `## The loop`.
