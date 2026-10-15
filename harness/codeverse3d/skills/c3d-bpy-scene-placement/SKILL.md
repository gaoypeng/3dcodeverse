---
name: c3d-bpy-scene-placement
description: "Use in every scene_blender env, zone, refine or rebuild session that places anything, and whenever check_placement or the scene_placement gate reported a floating, sunken, unsupported, interpenetrating or cantilevered asset, or too many free-tagged ones. Everything placed is ONE row in a measured table and its feet are sampled against what is under them. Gives what the gate measures and on which geometry, how a collection instance differs from a geometry-nodes cloud, the lib.place calls that seat a thing the way the gate samples it, the self-check to run before the build, and the finding-to-fix table."
license: Apache-2.0
compatibility: Blender 5.2.2 LTS headless, scene_blender runtime; the gate measures the harness-written census GLB (the evaluated depsgraph), never the .blend.
metadata:
  evidence: mixed
  evidence_note: 'The instrument half is read from live code on 2026-10-14 (spatial/scene_placement.py, runtime_js/lib/host_placement.mjs, config.Placement, wrappers/_census_glb.py) and the lib calls from the scene_blender starter library. The corpus half is empty: zero gated scene_blender runs exist on 2026-10-14; the first loop iteration attaches numbers.'
  verified: "2026-10-14"
  target_metric: "bpy_placement_errors"
  target_direction: "down"
  target_unit: "ERROR findings per run"
  target_measurable: "true"
  target_baseline: "n=0 — no gated scene_blender runs yet (2026-10-14); measured on the first loop iteration"
---

# Everything sits on something — placement the gate can measure

**Read this if** you are writing a zone, the env, or fixing a `check_placement` finding. The
placement gate is deterministic and strict (the 2026-10-14 standards: a hand's breadth of hover
is an ERROR), every ERROR is a score ceiling in the scene rubric, and the judge is handed a
close-up crop of each measured finding — a hover that is invisible in an overview is graded at
full size.

## What is measured, and on what

The build writes a census GLB from the evaluated depsgraph (modifiers, geometry nodes and
instances included) and the gate reads that, never the `.blend`. A **placed thing** is every
direct child of a zone collection that has mesh descendants, and each one is ONE row:

| what you build | rows | measured |
|---|---|---|
| a mesh object, with or without children | 1 — the whole subtree | yes |
| a collection-instance Empty (`lib.scatter.instance`) | 1 per placed copy | yes — its clones are sampled like a mesh |
| a multi-part prop finished under one root Empty (`Batch.finish`, `parent_under`) | 1 | yes |
| twelve loose meshes that together are a hut | 12 | yes, each at its own feet: the roof floats |
| a geometry-nodes or particle cloud (`gn_scatter`, `grass_patch`) | 1, exempt as instanced | no — and never a support either |
| a volume-only mesh (`lib.sky.haze`) | exempt as volumetric | no, and never a support or an overlap partner |
| ground, water and sky by name and shape; a shell that swallows the content box | exempt as backdrop or enclosure | no |
| anything tagged `obj["placement"] = "free"`, or its whole zone collection | exempt as free | no — but counted, see below |

Per row, from up to 5 **foot columns** taken from its lowest vertices — a table is measured at
its leg tips, not under its top:

* `ground_gap_m` — the vertical distance from the foot to the nearest surface of any OTHER mesh
  below it (terrain, floor, table top), else to the census ground level.
* `sunk_m` — how far a foot sits below a surface that passes through the asset's own extent.
* `on_water` — a water-named surface at or above the foot: supported, never sunk.
* `attached` — another asset's box within contact distance: mounted, so a small gap is left alone.
* `gap_max_m`, `columns_resting`, `tilt_m` — how many columns rest and how far the worst one hangs.

The kinds the report names, worst first: `floating` (a gap above the indoor or outdoor
tolerance; ERROR when it passes `floating_error_m` or when more than `floating_error_count`
assets float), `unsupported` (a smaller gap, touching nothing: WARN), `sunken` (burial past
`sunk_m`; ERROR past `sunk_error_m` when it also swallows `sunk_error_frac` of the height),
`interpenetration` (two boxes overlapping more than `overlap_warn` of the smaller one, confirmed by
`inside_frac` of its vertices inside the other; ERROR past `overlap_error`), `cantilevered` (at
most one of four or more columns rests while the worst one hangs: WARN) and `free_exempt` (the
share of free-tagged assets past `free_exempt_warn`, then `free_exempt_error`). Every threshold
is a `Settings.placement` field and the summary line of `check_placement` prints the values it
used; this sheet does not restate them. Interiors get the tighter tolerance, and the plan's
setting words decide that (`is_interior`), not your zone.

Three exemptions are by NAME (`placement_words.json`, one file read by the gate and by the
boot-time settle): `buried_ok` words (pit, pool, well, trench) are never sunken; `slope_ok` words
(path, platform, stairs, terrace) follow the terrain; `partial_ok` words (rock, tree, post, fence,
pebble) may sink up to `partial_ok_frac` of their height. Name things what they are and the gate
already knows how they sit. Renaming a bench "Rock" to dodge the check is pointless: the judge
grades the crop.

## Build so the rows come out right

1. **One height authority.** `height_at(x, y)` in `src/env.py` is the ground: build it with
   `lib.terrain.make_height_at`, let `lib.terrain.ground` mesh it, and let every zone read
   `ctx.height_at`. A zone that flattens its own patch to zero is how a bench ends up buried on
   one side and floating on the other.
2. **Seat with the lib, not by eye.** `lib.place.seat(obj, ctx.height_at, sink=...)` samples the
   same foot columns the gate does and returns the move it made. A placed copy of an asset is
   `lib.scatter.instance(ctx.collection, ctx.assets["<snake>"], name, x, y, height_at=ctx.height_at, rot_z=..., scale=..., sink=...)`
   — one Empty, already seated (an instance Empty you built yourself: `lib.place.seat_instance`).
   A thing ON a thing — a cup on a table, a lantern on a post — is `lib.place.rest_on(obj, support)`:
   the gate measures the gap to the SUPPORT, and `ctx.height_at` would drop it through the table.
   Every `lib.place` call reads the point set the gate measures — the object, its children and,
   for an instance Empty, the source collection under the Empty's transform — so pass the root.
3. **Sink only what sinks.** `sink` is for things whose name says they sink — a rock, a post, a
   tree with modelled roots; the words file tolerates those. A crate sunk "so it cannot float" is
   a `sunken` finding and a crop of a crate in the ground.
4. **Slopes.** `seat` rests the foot that stands over the lowest ground and buries the rest, so
   a wide thing on a slope comes back `sunken` on its uphill side; placing a thing at the ground
   height of its CENTRE instead (`height_at` of one point, the old skeleton's way) leaves the
   downhill feet hanging, which is the `cantilevered` finding. For wide things on a slope, flatten
   a pad in `height_at` under them (the ground mesh follows), split a long wall into segments each
   seated with `seat`, or turn it along the contour with `lib.place.face_toward`.
5. **A prop is one row.** Build its parts in one `lib.mesh.Batch` and `Batch.finish(ctx.collection, parent=True)`:
   ONE root Empty, one row, one seat. Twelve loose meshes are twelve rows with twelve chances to
   float, and twelve overlap partners for each other.
6. **Copies vs clouds.** Things the brief counts — props, trees near a camera, figures — are
   collection instances (`lib.scatter.instance`, `lib.scatter.scatter`): each is a row, each
   seated. Ground cover — grass, pebbles, leaves — is `gn_scatter` or `grass_patch`: exempt,
   cheap, never a support. Do not scatter benches with `gn_scatter` to escape the gate: the
   plan-contents check counts instances either way and the judge still sees a floating bench.
7. **`free` is for hanging and airborne things only** — a bird, a kite, a lantern on a chain, a
   drone. The gate reports the share of free-tagged assets (`free_exempt`), and the judge's
   floating defect still sees a free-tagged crate in the air.
8. **Stay inside.** `lib.place.keep_inside(obj, bbox)` with the zone's plan box as (centre,
   extents) slides it back horizontally. A thing entirely outside the plan bounds is the
   `out_of_bounds` kind, and one far from its planned `approx_size_m` is the `scale` kind — both
   target the zone, and the fix for the second is the SOURCE asset's build, not the Empties.
9. **Names are global in Blender.** `lib.place.unique_name(base, i)`; a second `Lantern_0` in
   another zone becomes `Lantern_0.001` and the plan-contents check loses it.

## The self-check, then the gate

Before the build, `lib.place.clear_of(objs)` lists the pairs whose boxes overlap past the gate's
own interpenetration band, as (name, name, fraction) — run it over the zone's root objects and
fix every pair it names (move, shrink, or make the two ONE prop). Then `build`, then
`check_placement`, which prints the findings with their fix hints and the table behind them:

```
asset | gap to support (m) | support | sunk (m) | into | water | attached
Market/Bench_0 | +0.236 | Terrain | 0.000 | - | - | -
interpenetration Market/Cart_1 × Market/Stall_0: 41% box, 23% inside
```

Apply the hint's number literally — `lower Bench_0 by 0.24 m onto Terrain` means `seat` it,
not "move it down a bit" — then `build` again. Do not finish a round whose last
`check_placement` still lists an ERROR: the rubric caps the run on each one, and a PASSED gate is
what vetoes the judge's own floating claim.

## Motion does not excuse the feet

The census is taken at the build's current frame, so a moving thing must be seated at every time
the harness samples. Keep a swaying tree's origin at its base (it rotates about its feet), drift
a boat ON water (`on_water` makes it supported), and let a bird be free. Keyframes go through
`lib.motion` on `ctx.frame(t)`; never move an object by shifting its mesh data after seating it.

Worked zone, where the rows come from in the census GLB, and the finding-to-fix table:
`references/finding-to-fix.md`.
