---
name: cv3d-blender-forms
description: Turn a described shape into the right bpy technique, and avoid the five bpy traps this harness's lint and exporter actually catch (join without an active object, a part file model.py never imports, no PascalCase names, modifier_apply out of context, primitive_cube_add scale doubling the extents). Use in any session writing Blender python — static_object with language blender, or articulated_object with language urdf_blender — when building, detailing, refining or repairing geometry.
license: Apache-2.0
compatibility: Blender 5.0.1 headless, run by codeverse/languages/blender/wrappers/run_bpy.py. bpy, bmesh, mathutils, math, random, numpy only.
metadata:
  evidence: measured
  verified: "2026-08-25"
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
already caught in this corpus, with how often.

blender is the biggest language here (102 of 142 graded runs with gate artefacts) and its
two weakest judge criteria are `assembly_fit` (0.60) and `geometry_detail` (0.67), means
over 246 judged rounds, mined 2026-08-25. `geometry_detail` is what the table below moves;
`assembly_fit` belongs to `cv3d-part-contact`, whose contact and weld numbers this skill
defers to rather than restating.

## Form to technique

Name the form first (this is what `cv3d-form-manifest` writes), then pick the row. Never
start from "what primitive is nearest".

| form | technique | notes |
|---|---|---|
| revolved profile (bottle, knob, vase, wheel hub) | `bmesh.ops.spin` on a profile, or a SCREW modifier | a lathe is one line and reads instantly better than a stack of cylinders |
| thread, coil, spring | SCREW modifier with `screw_offset` and `iterations` | not a stack of tori |
| swept tube (rail, cable, handle, hose) | curve with a non-zero `bevel_depth`; evaluate it to a mesh when a later step needs real geometry | `bevel_depth = 0` is a path with no surface: the node exports with no faces and the part is reported **missing** |
| extruded outline with softened edges | `bmesh.ops.extrude_face_region` then BEVEL modifier | `width` must be under half the thinnest wall or the bevel eats the part |
| shell / sheet metal / panel with wall thickness | build the surface, add SOLIDIFY | `offset=-1` grows inward, so the outer surface stays on the plan bbox |
| cavity, slot, groove, hole | BOOLEAN DIFFERENCE, solver EXACT or MANIFOLD | cutter must overshoot the pierced face and never be coplanar with it |
| small detail welded onto a body | BOOLEAN UNION, or build it into the same `bmesh` | overlapping is not merging: see `cv3d-part-contact` |
| N identical features on one part | ARRAY or MIRROR modifier, or one python loop into one `bmesh` | array/mirror/screw results are ONE object, which is what you want for in-part detail |
| N identical *parts* | a loop that creates N top-level objects named `Name_0 .. Name_N-1` | never a parent Empty; see `cv3d-repeats-and-mirrors` |
| taper, bend, twist | `bmesh` vertex maths, or SIMPLE_DEFORM | cheap and it reads |
| smooth organic body with sharp seams | SUBSURF plus edge creases | subsurf shrinks the part: measured on Blender 5.0.1 a subsurfed cube loses **16%** of every extent, a 32-segment cylinder 4-6%. The exporter applies it, so the contract gate sees the shrunk size |
| beam between two points | build along one axis, then place from the endpoints | do **not** rotate a cylinder with Euler angles; see `references/form-recipes.md` |

Code for every row: `read_cookbook(section="Modifiers")`, `"Screw and lathe"`,
`"Curves"`, `"Deformation"`, `"Boolean detailing"`, `"Parametric repetition"`.

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
   Verified on Blender 5.0.1: `primitive_cube_add(scale=(0.3, 0.2, 0.1))` gives dimensions
   0.6 x 0.4 x 0.2; adding `size=1` gives exactly 0.3 x 0.2 x 0.1. Pass `size=1` and the
   scale vector **is** the plan's extents, so you can paste the plan numbers in. Then bake
   it: `transform_apply(location=False, rotation=False, scale=True)` — all three keywords,
   always, because the ones you omit default to `True` and will bake the location too.

Two more from the same lint that cost a whole run when they fire: indexing
`bm.verts[i]` without `ensure_lookup_table()` is an ERROR (IndexError at build time), and
`bsdf.inputs["Specular"]` is an ERROR (renamed to `"Specular IOR Level"` in 4.x/5.x).

## Rules that are not in the lint but should be in your head

* **Never rotate a cylinder into place.** Build a beam from its two world endpoints —
  direction, length, and a quaternion from `(0,0,1)` to that direction. Euler order will
  betray you on the second axis. Recipe in `references/form-recipes.md`.
* **Derive spanning dimensions from the neighbour's plan bbox**, not from a typed literal.
  A cross-brace's length is `right.bbox.min.x - left.bbox.max.x + 2 * weld`.
* **Keep `obj.scale` at 1 by export time.** A scaled object bevels unevenly and exports its
  scale baked; `apply_transforms` before any bevel or boolean.
* **`bpy.context.selected_objects` and `bpy.context.object` are unreliable headless.** Keep
  your own references to everything you create. (1 run lost time to this.)
* **The exporter takes every *visible* mesh.** A leftover cutter, a construction guide or a
  duplicate ships as a part. Delete it or set `hide_render`/`hide_viewport`.
* **Detail budget over triangle count.** The prompt gives a target/floor/ceiling; a bevel
  and a chamfer on the right edges read as more refinement than 200k triangles of subsurf.

## Before you finish

`build` -> `render_sheet` -> `check_connectivity` -> `check_contract`, and read the
`lint:blender` findings in the build report first: they are free, static, and every one of
the five above was reported *before* the model was wrong.

Worked code — the beam helper, the lathe, the sweep, the solidify shell, the boolean with
its cutter cleaned up, and the multi-file wiring skeleton — is in
`references/form-recipes.md`.
