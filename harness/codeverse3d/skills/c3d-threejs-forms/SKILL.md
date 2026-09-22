---
name: c3d-threejs-forms
description: "Use when writing three.js object geometry in src/object.js or src/parts/*.js on the static_object track, in any session kind. Turn each FORM word in your manifest into the right three.js geometry maker, with the segment counts that make curves read and the traps that make a threejs part silently ship nothing. Covers the fact that this language has no boolean/CSG at all, so cavities and shells are built into the profile instead of subtracted."
license: Apache-2.0
compatibility: "static_object track, language threejs. three r182 ESM in node, exported to GLB by the harness. Imports that resolve: three, three/addons/*, relative files under src/."
metadata:
  evidence: inherited-unverified
  evidence_note: "Zero graded threejs bench runs exist (bench/out/*/runs/*, checked 2026-08-25); the only recorded threejs run is runs/e2e_bench_threejs. Every API claim here is verified against codeverse3d/prompts/threejs/contract.md, codeverse3d/prompts/threejs/cookbook.md, codeverse3d/languages/threejs/lint.py and runtime_js/package.json, but the ADVICE has not been A/B'd on our corpus. Routed off unless C3D_SKILLS_UNVERIFIED=on; upgrade to measured when threejs reaches 20 graded runs."
  verified: "2026-08-25"
  pairs_with: "c3d-part-contact, c3d-bbox-contract"
  target_metric: "missing_parts"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "none: n=0, bench/out holds no graded threejs run (2026-08-25)"
---

# FORM to three.js

## When to use

You have a manifest (or a plan part row) and you are about to write
`src/parts/<snake>.js`. Look up each FORM word, write that element, move on.

## The table

The FORM words in the left column key the blender and cadquery tables too, so only
this column changes when the language does.

| form | three.js | notes |
|---|---|---|
| revolved profile | `LatheGeometry(points, seg)` | `points` are `Vector2(radius, y)`, spun about **Y**. Normals point out when the radius goes 0 → R → 0 bottom-up |
| extruded outline | `ExtrudeGeometry(shape, {depth, bevelEnabled:true, bevelSize, bevelSegments})` | extrudes along **+Z**; `.rotateX(-Math.PI/2)` to lay it flat with thickness along Y |
| tube swept along a path | `TubeGeometry(new CatmullRomCurve3(pts), tubular, radius, radial, closed)` | the only clean way to do a handle, cable, rail or hoop |
| slab | `BoxGeometry(w,h,d)`, or `RoundedBoxGeometry(w,h,d,seg,r)` from `three/addons/geometries/RoundedBoxGeometry.js` | use the rounded one for anything hands touch |
| shaft | `CylinderGeometry(r, r, h, radial)` | axis is **Y**; rotate the group, not the geometry, when you need another axis |
| tapered solid | `CylinderGeometry(rTop, rBottom, h, radial)` or `ConeGeometry(r,h,seg)` | a 5–15 % difference between the two radii is a visible draft and costs nothing |
| ring or band | `TorusGeometry(R, r, radial, tubular)` | lies in the **XY** plane; `.rotateX(Math.PI/2)` to lay it flat |
| carved cavity | **no boolean exists here** — put the recess in the profile: a `Shape` with a `Path` pushed into `shape.holes`, a `LatheGeometry` profile that climbs the outside and comes back down the inside, or a ring of four slabs around the opening | see "There is no CSG" below |
| shell with wall thickness | a `LatheGeometry` whose profile traces outer wall up, across the rim, inner wall down | one geometry, watertight, and the open rim is refinement kind 6 |
| mirror pair | build once, add twice with `mesh.position.x = ±d` | never `scale.set(-1,1,1)`: a negative scale inverts the winding and exports reversed normals |
| radial array | one geometry, `InstancedMesh(geo, mat, N)` with `M.makeRotationY(i/N * 2π)` | derive the angle from N; see the instancing trap below |
| layered or inset | a second slab 1–2 mm proud of or sunk into the face, its own material | the cheapest panel break there is |
| faceted solid | raw `BufferGeometry`: `Float32BufferAttribute` positions + `setIndex` + **`computeVertexNormals()`** | without the normals call it shades black |
| lofted transition | `LatheGeometry` when it is round-to-round; otherwise a raw `BufferGeometry` ring-to-ring strip | three r182 has no loft primitive |

`mergeGeometries` from `three/addons/utils/BufferGeometryUtils.js` collapses many
static pieces of one part into one mesh. Merge inputs must share the same
attribute set (`geo.deleteAttribute('uv')` or `toNonIndexed()` everything), and
call `computeVertexNormals()` after.

## Segment counts that read

Under-segmenting is the most common reason a lathe or a tube reads as a
prism in the render sheet.

| what | radial segments |
|---|---|
| visible cylinder, lathe body, knob | 24–48 |
| thin rod, spoke, baluster, screw shank | 8–12 |
| tube radial | 8–12 |
| small sphere | 16 x 12 |
| extrude `curveSegments` on a curved outline | 16 |

A `SphereGeometry(r, 64, 64)` repeated a hundred times is 800 k triangles and
blows the ceiling; the budget belongs on the shapes that fill the silhouette.

## There is no CSG

`runtime_js/package.json` ships `three`, `puppeteer` and `three-mesh-bvh` only —
there is no `three-bvh-csg`, no `Brush`, no `Evaluator`, and the lint allows
imports of `three`, `three/addons/*` and relative files under `src/` and nothing
else. So a boolean operation is not available to you at all, in either
direction.

That changes how you plan a cavity. A cup is not "a cylinder minus a smaller
cylinder"; it is one lathe profile that goes up the outer wall, across the rim
and back down the inner wall. A bracket with a bolt hole is not a plate minus a
cylinder; it is a `Shape` with an `absarc` `Path` in `shape.holes`. A slot in a
face is four slabs around the opening, or a hole in the extrude outline. Each of
those is one geometry, stays watertight, and exports cleanly — which subtraction
in this language would not, because it does not exist.

## The traps that actually bite

1. **The file name is the export name.** `src/parts/seat_cushion.js` must
   `export function buildSeatCushion(THREE)` — the lint derives the expected name
   from the file stem (`codeverse3d/languages/threejs/lint.py`). A misspelt name is
   a WARN; no `build*` export in the file at all is an ERROR.
2. **A part file nobody imports ships nothing.** `src/object.js` must import each
   part builder and add its Group to the root. An unimported `src/parts/*.js` is
   a WARN ("dead part file?"), the build succeeds, and the part is simply absent
   from the GLB — this is a real, recorded failure mode in the blender track and
   the same lint rule exists here.
3. **An `InstancedMesh` reads as N stray islands.** The harness exports an
   `InstancedMesh` named `Bolts` as a group `Bolts` of meshes `Bolts_0 …
   Bolts_{n-1}` with matrices baked, so the connectivity gate sees one plan part
   made of N disconnected pieces. In `runs/e2e_bench_threejs` a `FastenerBolts`
   part produced **39 tiny disconnected island** warnings. Instancing is still
   the right tool — just sink each copy into the surface it fastens so it is a
   contact, not a floater.
4. **Never resize with `root.scale`.** Writing your dimensions in one unit and
   then setting `root.scale.set(0.01, ...)` to convert makes the bounds and the
   contract gate disagree, because they read the scaled and unscaled geometry
   inconsistently. Build the geometry at the size the contract asks for.
5. **`ShaderMaterial` and every texture are lost.** GLB export keeps
   `MeshStandardMaterial` / `MeshPhysicalMaterial` and vertex colours only. The
   module is imported in node with no DOM, so `TextureLoader` / `ImageLoader` /
   `FileLoader` and any `document` or `window` reference are hard lint ERRORs —
   colour, roughness, metalness and vertex colours are the whole palette.
6. **Faceted or black shading after any manual edit** → you skipped
   `computeVertexNormals()`.
7. **`mesh.position` versus `geometry.translate()`.** Position moves the pivot
   and is what you want for a part placed at its world pose; `.translate()` bakes
   the offset into the vertices and is what you want before a merge or to move a
   blade root onto its pivot. Doing both by accident doubles the offset.
8. **Only `mergeGeometries` exists**, not `mergeBufferGeometries`, and
   `three/examples/js/*`, `Geometry`, `Face3`, `sRGBEncoding` and `outputEncoding`
   were all removed before r182.

## What the part must return

One `THREE.Group` per plan part, returned at its world pose and added to the root
in `src/object.js`. Everything inside it — merged detail, instanced repeats, raw
BufferGeometry — is invisible to the part-level gates, which is exactly why the
detail belongs in there. The object's naming and frame rules are in the contract;
dimensions and tolerances belong to `c3d-bbox-contract`; contact and overlap
belong to `c3d-part-contact`.

## Depth

`references/form_recipes.md` — a runnable snippet per form word, including the
cavity and shell profiles that replace the boolean you do not have.
Longer code lives in the cookbook — the cookbook section "Geometry
toolkit", and "Detail (how to look good cheaply)", "Density: visual complexity
without hand-modelling every screw" or "Raw BufferGeometry and a spoked wheel".
