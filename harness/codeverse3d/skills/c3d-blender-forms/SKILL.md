---
name: c3d-blender-forms
description: "Use when writing Blender bpy code (language blender or urdf_blender): turn a described shape into the right bpy technique. Avoids the five bpy traps this harness's lint and exporter actually catch: join without an active object, a part file model.py never imports, no PascalCase names, modifier_apply out of context, and primitive_cube_add scale doubling the extents. Covers building, detailing, refining and repairing geometry."
license: Apache-2.0
compatibility: Blender 5.2.2 LTS headless, run by codeverse3d/languages/wrappers/run_bpy.py; imports per the contract's allow-list.
metadata:
  evidence: measured
  verified: "2026-10-15"
  corpus: "102 graded blender runs + 23 urdf_blender runs with gate artefacts, mined 2026-08-25"
  target_metric: "blender_lint_findings"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "0.87 mean / 0.0 median findings per run; n=164 (bench/out, last gated round, 2026-08-25); blender 1.01, urdf_blender 0.00"
---

# Shapes to bpy, and the traps the lint counts

**Read this if** you are about to write `bpy` code. It is two things: a form-to-technique
table so you do not build everything out of boxes, and the five traps that a static lint
already caught in this corpus, with how often. Every row and practice is an option under its
real API name — the brief and the plan say what the object is; nothing here says how it must
be built.

blender is the biggest language here (102 of 142 graded runs with gate artefacts) and its
two weakest judge criteria are `assembly_fit` (0.60) and `geometry_detail` (0.67), means
over 246 judged rounds, mined 2026-08-25. `geometry_detail` is what the table below moves;
`assembly_fit` belongs to `c3d-part-contact`, whose contact and weld numbers this skill
defers to rather than restating.

## Form to technique

Say the form in words, then pick the technique; the table is a menu, not an order.

| form | technique | notes |
|---|---|---|
| revolved profile (bottle, knob, vase, wheel hub) | `bmesh.ops.spin` on a profile, or a SCREW modifier | one `(radius, z)` list is the whole shape; spin needs EDGES in `geom`, and `remove_doubles` welds the seam and the axis points |
| thread, coil, spring | SCREW modifier with `screw_offset` and `iterations` | not a stack of tori |
| swept tube (rail, cable, handle, hose) | curve with a non-zero `bevel_depth`; evaluate it to a mesh when a later step needs real geometry | `bevel_depth = 0` is a path with no surface: it exports no node and the part is reported **missing** (contract.md) |
| moulding, rim, handrail, cornice | a 2-D profile curve as the `bevel_object` of a 3-D path curve (`bevel_mode = 'OBJECT'`), evaluated to a mesh | a curved path needs sampled points, not four corners |
| extruded outline with softened edges | `bmesh.ops.extrude_face_region` then BEVEL modifier | `width` must be under half the thinnest wall or the bevel eats the part |
| shell / sheet metal / panel with wall thickness | build the surface, add SOLIDIFY, then any bevel | `offset=-1` grows inward, so the outer surface stays on the plan bbox; zero-thickness faces have no volume for the gate's inside test and break booleans |
| cavity, slot, groove, hole | BOOLEAN DIFFERENCE, solver EXACT or MANIFOLD | cutter must overshoot the pierced face and never be coplanar with it |
| small detail welded onto a body | BOOLEAN UNION, or build it into the same `bmesh` | overlapping is not merging: see `c3d-part-contact` |
| N identical features on one part | ARRAY or MIRROR modifier, or one python loop into one `bmesh` | array/mirror/screw results are ONE object, which is what you want for in-part detail |
| N identical *parts* | a loop that creates N top-level objects named `Name_0 .. Name_N-1` | never a parent Empty; see `c3d-repeats-and-mirrors` |
| taper, bend, twist | `bmesh` vertex maths, or SIMPLE_DEFORM | pivots on the object origin; needs rings along the length |
| smooth organic body with sharp seams | SUBSURF plus edge creases (`crease_edge` attribute) | subsurf shrinks the part: measured on Blender 5.2.2 a level-2 subsurfed cube loses **16%** of every extent. The exporter applies it, so the contract gate sees the shrunk size |
| beam between two points | build along one axis, then place from the endpoints | a quaternion from the endpoint direction (`Vector.rotation_difference`) avoids Euler-order mistakes; see `references/form-recipes.md` |

Short practices for each row are in `references/form-recipes.md`.

## The five traps, with counts

Counted over the 102 graded blender runs (mined 2026-08-25); each is a real `lint:blender`
finding, emitted before the build even runs.

1. **`bpy.ops.object.join` — 17 runs (17%).** The join result inherits the *active* object's
   transform, and in background mode the selection is not what you think. Either set
   `bpy.context.view_layer.objects.active` to an object with an identity transform first, or
   do not join at all: build the whole part in one `bmesh` and skip the operator.
2. **A part file `src/model.py` never imports — 7 runs (7%).** `src/parts/backrest.py` that
   nobody imports ships *nothing*, silently: no build error, just a missing part and a
   `contract/missing_part` finding two gates later. `model.py` must `from parts.backrest
   import build_backrest` and call it exactly once. The mirror trap: a part file that calls
   its own builder at module level builds the part twice and you get `Backrest.001`.
3. **No PascalCase object names — 14 runs (14%).** Every visible mesh takes its plan part's
   name exactly; instances are `Leg_0 .. Leg_3`. An auto-suffixed `Leg.001` is a different
   part as far as the contract gate is concerned. (urdf_blender instead names each object
   after its URDF link, and `lint:urdf` checks the link name appears verbatim in
   `model.py`.)
4. **`bpy.ops.object.modifier_apply` out of context — 7 runs (7%).** It needs the object
   active and selected in OBJECT mode. **Usually you should not apply at all**: the exporter
   runs with `export_apply=True`, so bevels, solidifies, arrays and mirrors are evaluated
   for you. Apply only when a later step needs the real mesh — a boolean, a join, a vertex
   loop — and then wrap it: `with bpy.context.temp_override(object=obj, active_object=obj,
   selected_objects=[obj]):`.
5. **`primitive_cube_add(scale=...)` without `size=1` — the extents come out doubled.**
   Re-measured on Blender 5.2.2: `primitive_cube_add(scale=(0.3, 0.2, 0.1))` gives dimensions
   0.6 x 0.4 x 0.2; adding `size=1` gives exactly 0.3 x 0.2 x 0.1. Pass `size=1` and the
   scale vector **is** the plan's extents, so you can paste the plan numbers in. Then bake
   it: `transform_apply(location=False, rotation=False, scale=True)` — all three keywords,
   always, because the ones you omit default to `True` and will bake the location too.

Two more from the same lint that cost a whole run when they fire: indexing
`bm.verts[i]` without `ensure_lookup_table()` is an ERROR (IndexError at build time), and
`bsdf.inputs["Specular"]` is an ERROR (renamed to `"Specular IOR Level"` in 4.x/5.x).

## Strong options (not in the lint)

* **Struts.** Build along +Z and place with a quaternion from the two world endpoints
  (`references/form-recipes.md` §1); Euler order betrays you on the second axis.
* **Spans computed from the neighbours' plan bboxes** follow the neighbours when they move;
  a typed literal does not.
* **Keep `obj.scale` at 1 by export time.** A scaled object bevels unevenly and exports its
  scale baked; apply the transform before any bevel or boolean.
* **`bpy.context.selected_objects` and `bpy.context.object` are unreliable headless.** Keep
  your own references to everything you create. (1 run lost time to this.)
* **The exporter takes every *visible* mesh.** A leftover cutter, a construction guide or a
  duplicate ships as a part. Delete it or set `hide_render`/`hide_viewport`.
* **Fewer booleans.** Cutters of one kind can share one mesh and one cut: every boolean is
  another evaluation and another chance of a non-manifold sliver, which the island gate
  reports. A cutter coplanar with the face it cuts, or not closed (`cap_ends=True`), fails
  silently.
* **Flipped normals** after `bmesh.ops.scale` with a negative factor or after mirroring read
  as dark, inside-out faces in every render (no gate measures them):
  `bmesh.ops.recalc_face_normals(bm, faces=bm.faces)` before `to_mesh`.
* **Bend / taper.** SIMPLE_DEFORM pivots on the object origin and needs rings along the
  length (`bmesh.ops.subdivide_edges` on the side edges): an origin at one end gives an arc,
  a centred one a C.
* **Two-tone parts.** A second colour on one part is a second material slot and
  `face.material_index` on the faces you choose; `bmesh.ops.create_*` return only `verts`,
  so diff `set(bm.faces)` before and after the op to find the new faces, or set
  `polygon.material_index` on `obj.data.polygons` afterwards. The census warns when a slot
  is never used (contract.md, Materials).
* **Where detail is seen.** The judge's montage shows the silhouette and the largest faces
  from the rig's views; the underside and interiors the camera cannot enter are never
  rendered. Bevels and chamfers add shading breaks for few triangles; the triangle budget
  gate states a number only when you exceed it.

## Before you finish

`build` -> `render_sheet` -> `check_connectivity` -> `check_contract`, and read the
`lint:blender` findings in the build report first: they are free, static, and every one of
the five above was reported *before* the model was wrong.

Short options — the beam, the lathe, the swept tube, the solidify shell, the boolean with
its cutter cleaned up, and the multi-file wiring — are in `references/form-recipes.md`.
