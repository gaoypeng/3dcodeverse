---
name: c3d-bpy-scene-placement
description: "Use in every scene_blender env, zone, refine or rebuild session that places anything, and whenever check_placement or the scene_placement gate reported a floating, sunken, unsupported, interpenetrating, cantilevered or out-of-bounds asset, too many free-tagged ones, or a world that ends at its bounds (world_edge). Everything placed is ONE row in a measured table and its feet are sampled against what is under them. Gives what the gate measures and on which geometry, how a collection instance differs from a geometry-nodes cloud, the requirements with their measurements, how to seat and check in raw bpy what the gate samples (no library ships in raw mode), the self-check, and the finding-to-fix table."
license: Apache-2.0
compatibility: Blender 5.2.2 LTS headless, scene_blender runtime; the gate measures the harness-written census GLB (the evaluated depsgraph), never the .blend.
metadata:
  evidence: mixed
  evidence_note: 'The argon_raw (D113) copy of the base bundle with every library call replaced by the raw bpy it stands for. The instrument half is read from live code on 2026-10-14 (spatial/scene_placement.py, runtime_js/lib/host_placement.mjs, config.Placement, wrappers/_census_glb.py). The corpus half is the argon loop iteration 0 of 2026-10-15 (30 scene_blender runs): 13 zones with an interpenetration ERROR, 9 of them a scatter into a hand-placed prop.'
  verified: "2026-10-17"
  target_metric: "bpy_placement_errors"
  target_direction: "down"
  target_unit: "ERROR findings per run"
  target_measurable: "true"
  target_baseline: "n=30 scene_blender runs, argon loop iteration 0 (2026-10-15): 13 zones shipped an interpenetration ERROR; the per-run ERROR count is not yet tabulated"
---

# Everything sits on something — placement the gate can measure

**Read this if** you are writing a zone, the env, or fixing a `check_placement` finding. The
placement gate is deterministic (a hand's breadth of hover is an ERROR), every ERROR is a score
ceiling in the scene rubric, and the judge is handed a close-up crop of each measured finding.

## What is measured, and on what

The build writes a census GLB from the evaluated depsgraph (modifiers, geometry nodes and
instances included) and the gate reads that, never the `.blend`. A **placed thing** is every
direct child of a zone collection that has mesh descendants, and each one is ONE row:

| what you build | rows | measured |
|---|---|---|
| a mesh object, with or without children | 1 — the whole subtree | yes |
| a collection-instance Empty (`instance_type = 'COLLECTION'`) | 1 per placed copy | yes — its clones are sampled like a mesh |
| a multi-part prop under one root (a parent Empty, parts parented under one mesh, or joined meshes) | 1 | yes |
| twelve loose meshes that together are a hut | 12 | yes, each at its own feet |
| a geometry-nodes or particle cloud (an Instance on Points tree) | 1, exempt as instanced | no — and never a support either |
| a volume-only mesh (a fog box, a steam puff) | exempt as volumetric | no, and never a support or an overlap partner |
| ground, water and sky by name and shape; a shell that swallows the content box | exempt as backdrop or enclosure | no |
| anything tagged `obj["placement"] = "free"`, or its whole zone collection | exempt as free | no — but counted, see below |

Per row, from up to 5 **foot columns** taken from its lowest vertices — a table is measured at
its leg tips, not under its top:

* `ground_gap_m` — the vertical distance from the foot to the nearest surface of any OTHER mesh
  below it (terrain, floor, table top), else to the census ground level.
* `sunk_m` — how far a foot sits below a surface that passes through the asset's own extent.
* `on_water` — a water-named surface at or above the foot: supported, never sunk.
* `attached` — another asset's box within contact distance: mounted, so a small gap is left alone.
* `gap_max_m`, `columns_resting`, `tilt_m` — how many columns rest, how far the worst hangs.

The kinds the report names, worst first: `floating` (a gap above the indoor or outdoor
tolerance; ERROR past `floating_error_m` or when more than `floating_error_count` assets float),
`unsupported` (a smaller gap, touching nothing: WARN), `sunken` (burial past `sunk_m`; ERROR past
`sunk_error_m` when it also swallows `sunk_error_frac` of the height), `interpenetration` (two
boxes overlapping more than `overlap_warn` of the smaller one, confirmed by `inside_frac` of its
vertices inside the other; ERROR past `overlap_error`), `cantilevered` (at most one of four or
more columns rests while the worst hangs: WARN) and `free_exempt` (the share of free-tagged
assets past `free_exempt_warn`, then `free_exempt_error`). Every threshold is a
`Settings.placement` field and the summary line of `check_placement` prints the values it used.
Interiors get the tighter tolerance; the plan's setting words decide that (`is_interior`).

Three exemptions are by NAME (`placement_words.json`, one file read by the gate and by the
boot-time settle): `buried_ok` words (pit, pool, well, trench) are never sunken; `slope_ok` words
(path, platform, stairs, terrace) follow the terrain; `partial_ok` words (rock, tree, post, fence,
pebble) may sink up to `partial_ok_frac` of their height. A thing named what it is gets the
tolerance its kind has; the judge grades the crop whatever the name.

## The requirements, and ways to meet them

1. **Interface: one ground authority.** `height_at(x, y)` in `src/env.py` is the ground every
   zone reads as `ctx.height_at`, and the ground mesh samples the same function. Any function of
   (x, y) the brief's landform needs is fine: rolling hills from summed sines or
   `mathutils.noise.fractal`, a slope to a river, a ridge, a terrace, a quay stepping down to a
   basin, a plaza flat everywhere; the ground mesh is a grid whose vertices you set to it. A zone
   that flattens its own patch to a different number is a bench buried on one side, floating on
   the other.
2. **Requirement: feet within the gate's gap tolerance of what is under them** (measured on the
   census GLB at the foot columns). Read the point set the gate reads — the root's world-space
   vertices, its children's, and for an instance Empty the source collection's through the
   Empty's matrix — keep the lowest ones, and move the root by the smallest
   `ctx.height_at(x, y) - z` over them. A thing ON a thing — a cup on a table, a lantern on a post
   — goes onto the support's top under its footprint, because the gate measures the gap to the
   SUPPORT. Any placement that passes `check_placement` is acceptable.
3. **Sinking is tolerated for names that sink** — a rock, a post, a tree with modelled roots; the
   words file carries the list. A crate sunk "so it cannot float" is a `sunken` finding and a
   crop of a crate in the ground.
4. **Slopes.** Seating by the smallest gap rests the foot over the lowest ground and buries the rest, so a wide thing
   on a slope comes back `sunken` uphill; placed at the ground height of its CENTRE, its downhill
   feet hang: `cantilevered`. Options: a pad in `height_at` under it (the ground mesh follows),
   seated segments, or a turn along the contour (a yaw about Z).
5. **Requirement: a multi-part thing whose parts support each other is ONE row** — one direct
   child of the zone collection with the parts under it — or each part is seated on its own
   support. A root Empty with the parts parented under it at relative locations, or one mesh
   built from all of them, does it; twelve loose meshes are twelve rows and twelve overlap
   partners for each other.
6. **Requirement: anything the brief or plan counts is a measured row** (a direct child of the
   zone collection with mesh descendants). Geometry-nodes and particle clouds are exempt from
   placement and are never supports, so nothing can rest on them; the plan-contents check counts
   instances either way. Collection-instance Empties make rows; a geometry-nodes Instance on
   Points tree makes a cloud — the way for the hundreds (blades, pebbles, litter, a far treeline).
   In 5.2 a new `GeometryNodeTree` has no sockets and no nodes: add them with
   `ng.interface.new_socket(name, in_out='INPUT' | 'OUTPUT', socket_type='NodeSocketGeometry')` and
   your own `NodeGroupInput` / `NodeGroupOutput` nodes (`NodeTree.inputs` / `.outputs` are gone).
7. **`free` is for hanging and airborne things** — a bird, a kite, a lantern on a chain, a steam
   plume. The gate reports the share of free-tagged assets (`free_exempt`), and the judge's
   floating defect still sees a free-tagged crate in the air.
8. **Requirement: a PLANNED content placed entirely outside the plan bounds is the
   `out_of_bounds` kind** (a WARN, past the bounds + max(5 m, 10 % of the span)). Everything
   else — terrain, water, far buildings, forest, the rest of the street — continues past the
   bounds as the brief's place does and is never reported. Clamping its x / y into the zone's
   plan box slides one back. A row far from its planned `approx_size_m` is the `scale` kind, and the fix
   is the SOURCE asset's build, not the Empties.
9. **Requirement (outdoor plans): the world continues past the bounds.** `world_edge` is a
   WARN when the farthest content or ground group at least 2 m tall reaches less than
   1.25 × the bounds' half-extent from the centre. Whatever the brief's horizon holds satisfies it
   — a landform that keeps rising, a forest edge, far roofs, cliffs, a sea wall — built in the env
   (not a placement row) and named `Sky…` when wide, so the census classifies it as backdrop. The
   census also measures `stamped_ring` — near-identical copies evenly on a circle round the
   world's edge — which the judge calls a toy backdrop.
10. **Names are global in Blender.** Number copies `f"{base}_{i}"`; a second `Lantern_0`
    becomes `Lantern_0.001` and the plan-contents check loses it.

## The self-check, then the gate

Compare the world bounding boxes of the zone's roots AND its scattered Empties pair by pair:
the overlap volume over the smaller box's volume past the interpenetration band is a finding.
Drop every scatter point that falls inside the footprint (plus a margin) of what you placed by
hand: 9 of the 13 Blender zones with an interpenetration ERROR in the argon loop's
iteration 0 (2026-10-15) had scattered a small prop into a cart, a rack, a shrub or a mound. Then
`build`, then `check_placement`, which prints the findings with their fix hints and the table:

```
asset | gap to support (m) | support | sunk (m) | into | water | attached
Market/Bench_0 | +0.236 | Terrain | 0.000 | - | - | -
interpenetration Market/Cart_1 × Market/Stall_0: 41% box, 23% inside
```

The hint's number is the measured gap: move the root by it. A round whose last `check_placement`
lists an ERROR is capped by the rubric on each one; a PASSED gate vetoes the judge's own floating claim.

## Motion, and the feet

The census is taken at the build's current frame, so a moving thing is measured seated at every
time the harness samples (t = 0 and t = 1.5 s; the judge diffs the two frames and names
`nothing_moves` when it sees no change). Motion is data: keyframes on `ctx.frame(t)` or a simple
driver of `frame` (a driver that needs Python evaluates to 0 and the lint rejects it). A tree
with its origin at its base sways about its feet (a keyed rotation turns about the origin: put
it at the hinge or hub). A boat drifting ON a water-named plane is `on_water`; a bird, a steam
plume or a spark cloud built as one object is a `free` row.

Worked zone, row provenance and the finding-to-fix table: `references/finding-to-fix.md`.
