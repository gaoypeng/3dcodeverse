# Using the 3dcv spatial tools (look before you leap)

The tools are the harness's instruments.  They are cheap, deterministic and honest —
trust their numbers over your mental model.  Observations come back as
`{text, numbers, images[]}`; images are small labelled PNGs / contact sheets.

## The standard loop

```
1. build                 → errors first (file:line + traceback tail) or key numbers
2. render_sheet          → 4 labelled views (front_right_34, back_left_34, front, top)
3. check_connectivity    → islands, floating parts, ground gap, per-part touch graph
4. fix the worst finding → rebuild (repeat 1–3 until clean)
5. isolate <part>        → the one part you doubt, alone, 4 views + its bbox
6. cross_section         → axis=y at the cavity height: is the mug actually hollow?
7. compare_silhouette    → only when a reference image exists (IoU per view)
8. joint_sweep / scene_probe / shader_probe  → articulated / scene tracks
9. check_contract        → plan bbox vs measured, per part (last, before done)
```
Always `build` after an edit; never reason about stale renders.

## Tool cards (arguments are validated; read the error if you get one)

* `build()` — lint + execute + export + measure.  On error: fix that file:line first.
  On success: overall bbox/extents, tri count, n_meshes, n_islands, part table.
* `measure(parts=[...])` — bbox/extents/islands/volume per part; use it to check a
  dimension numerically instead of eyeballing a render.
* `render_views(views=[...], mode="shaded"|"wire"|"normals"|"clay", isolate=[...],
  explode=0.0)` — named views from the preset list (front, right, back, left, top,
  front_right_34, back_left_34, low_front_left).  `explode=0.3` separates parts to see
  what is where.  `wire` shows density; `clay` removes colour so shape is judged alone.
* `render_sheet()` — the quick 4-view contact sheet.  One image, always look at it.
* `isolate(part="SeatCushion")` — that part alone, framed; everything else hidden.
* `cross_section(axis="y", at=0.05)` — slice at height `at` (meters, in the GLB Y-up
  frame); shows walls, cavities, interpenetration.  Slice where the detail is.
* `check_connectivity(gap_m=0.002)` — hard facts: islands, floating pieces (with the
  nearest neighbour and the gap in mm), ground gap.  A positive gap = you must weld.
* `check_contract(tol_m=0.01)` — plan bbox vs measured per part; lists the offenders
  with the translation / scale that would fix them.
* `compare_silhouette(view="front")` — IoU of your render vs the reference image.
* `joint_sweep(joint="DoorHinge")` — FK over [lower, mid, upper]; renders + collision
  pairs per pose; "child does not move" or "penetrates parent at q=1.2" is the finding.
* `scene_probe()` — census (draw calls, tris, instanced meshes), cameras inside geometry,
  console errors, fps, black/blown frames.
* `check_placement()` — (scene) every placed asset's gap to what is under it, burial depth,
  water, contacts and 3-D interpenetrations; "floating / sunken / unsupported /
  interpenetration" findings with the exact move to make.  Run it after every `build`.
* `shader_probe()` — compiles every ShaderMaterial / onBeforeCompile patch with the real
  renderer; reports GLSL errors with the author's line number.
* `read_cookbook(section="Booleans")` — fetch one cookbook chapter by its heading.

## Reading the outputs

* `n_islands > n_parts` → something is split or floating; `check_connectivity` names it.
* `ground_gap_m < 0` → buried; `> 0.002` → hovering.  Translate the whole object.
* `extents` far from plan → wrong unit (mm vs m) or a scale you forgot to apply.
* `footprint_offset_m > 0.01` → not centred on the up-axis.
* In renders: identical grey everywhere = materials missing; a part present in `top`
  but absent in `front` = it is inside something else; thin white lines at seams = gaps.

## Image budget etiquette

* One `render_sheet` per build cycle (not per edit).  Use `isolate` / `cross_section`
  only for the part you are about to change.  Prefer `measure` (numbers, no image) for
  dimension questions.
* Do not request all 8 views + wire + normals at once; the sheet is enough to decide.
* Re-render after a fix, and compare with the previous sheet before moving on.
* Stop when the definition-of-done list is green, not when you run out of ideas.
