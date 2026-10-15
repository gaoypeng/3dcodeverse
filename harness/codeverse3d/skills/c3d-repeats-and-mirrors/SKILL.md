---
name: c3d-repeats-and-mirrors
description: "Use when a part has instances above 1 or symmetry, is a left/right pair or a ring of N, or when contract/instance_bbox or instance_count fired. Build repeated, mirrored and radially arrayed parts so the contract gate passes on the first build. Covers instance naming (Name_0..Name_N), which box the gate compares for an instanced part, mirroring without a negative object scale, and radial angles derived from the count."
license: Apache-2.0
compatibility: static_object and articulated_object tracks; blender, urdf_blender, cadquery and threejs. Numbers are from codeverse3d/spatial/contract.py and conventions.py.
metadata:
  evidence: measured
  verified: "2026-10-15"
  corpus: "217 run records / 2,121 gate reports under bench/out, mined 2026-08-25"
  owns: "contract/instance_bbox, contract/instance_count"
  target_metric: "contract_instance_findings"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "0.32 mean / 0.0 median findings per run; n=164 (bench/out, last gated round, 2026-08-25); 132 of 164 runs already at zero"
---

# Repeats, mirrors and arrays

**When:** a plan row says `instances > 1`, or `symmetry` is `mirror_x` / `mirror_y` / `radial`,
or the brief asks for a ring / row / pair of the same thing. Skip if every part is unique.

**A strong option:** build the shape once and place the copies from a loop whose numbers come
out of the plan row — a hand-typed second copy is how a pair stops matching.

## What the gate actually measures

`check_contract` (`spatial/contract.py`) treats an instanced part differently from a single part.

* **Naming.** The gate first claims the node named exactly like the plan part, then claims
  `Name_0`, `Name_1`, ... (`Leg1`, with no separator, is NOT an instance of `Leg`).
  A node whose name is exactly *another* plan part's name is never stolen. Use
  `Name_0 .. Name_{N-1}` — that is the string the gate's own fix hint asks for.
* **Count.** `instances = N` but a different number of nodes found is a WARN:
  `'ApexFinial': found 1 instance(s), plan asks for 2`.
* **Box.** For `instances > 1` the **centre is not checked at all**. The gate computes two
  readings and reports whichever fits better:
  * `(each instance)` — the *worst single copy's* extents against the plan extents;
  * `(all instances)` — the *union* bbox of all copies against the plan extents.
  Satisfying either one clears the finding. The plan's bbox for an instanced row describes
  **one copy** at a representative location (see `tracks/planner.py::plan_example`: `Upright`,
  `instances: 4`, the extents of one bar), so build each copy at those extents and the
  `(each instance)` reading goes to zero.
* **Tolerance.** Per axis `tol = max(0.01 m, 0.10 x plan extent)`; beyond `3.0 x tol` the WARN
  becomes an ERROR. Same constants as `c3d-bbox-contract` (`BBOX_TOLERANCE_M`, `REL_TOL`,
  `ERROR_FACTOR`).

Because the centre is ignored here, a mis-*placed* copy never shows up as `instance_bbox`. It
surfaces later as interpenetration, a floating part, or an overall-bbox deviation — one loop
bug, N findings in a different gate.

## Measured (bench/out, 2026-08-25)

* `contract/instance_bbox` fired in **38 of 217 runs**; 12 of those reached ERROR.
* Of its 191 findings, **144 were `(each instance)`** and 47 `(all instances)` — the copies were
  the wrong *size*, far more often than the group was the wrong *span*.
* Median worst deviation **2.0 x tolerance**, max 27.7 x.
* The judge's phrasing of the same bug is plural and total: *"All four stool legs float above the
  ground plane"*, *"All four feet interpenetrate the BasePlinth by 3.0 mm"*, *"All four wheels
  interpenetrate the chassis"*. When a loop is wrong it is wrong N times.

## Placement: derive from the plan, not from the keyboard

```python
# plan row: Name - extents EX, instances N; CENTRES = the N copy centres the row's symmetry gives
first = build_mesh("Name_0", EX, CENTRES[0])          # ONE build of the mesh data ...
for i, c in enumerate(CENTRES[1:], start=1):
    copy = bpy.data.objects.new(f"Name_{i}", first.data)  # ... N objects sharing it
    copy.location = c
    bpy.context.scene.collection.objects.link(copy)
```

Shared mesh data means every copy has the same extents by construction, so `(each instance)`
cannot drift. If a copy must differ,
that is a different plan part, not an instance.

## Radial arrays: the angle comes from the count

```python
n = 6                                              # the count drives the angles
for i in range(n):
    a = 2.0 * math.pi * i / n
    place(f"Spoke_{i}", (R * math.cos(a), R * math.sin(a), Z), yaw=a)
```

A typed list silently drops or duplicates a step when the count changes, and the gate then
reports a count mismatch it took you a round to see. For repeated *detail inside one part*
(flutes, dentils, cage wires) an ARRAY modifier with `use_object_offset` and an Empty
rotated 2π/n is the option (apply it and delete the Empty — only meshes reach the export):
one export node, which is what the plan asked for.

## Mirrors

Two different things share the word:

* **Symmetry inside one part** (a bracket that is symmetric about x=0): MIRROR modifier over the
  object origin — put the origin ON the symmetry plane, then apply. One node, one plan part.
* **A mirrored pair the plan names** (`instances: 2`, `symmetry: mirror_x`): two nodes,
  `Name_0` and `Name_1`, at `+X` and `-X`. Both must exist; a missing twin is a count WARN and
  usually a lopsided silhouette in every render.

**On mirroring by negative object scale, the usual warning is wrong here.** It is widely
repeated that a negative scale reaches the export and inverts normals. Checked on 2026-08-25:
`spatial/measure.py` loads the GLB with trimesh and calls `apply_transform` per node, and
trimesh repairs the face winding of a negatively-scaled mesh on load; `measure` then records
`abs(volume)`. The gates read identical numbers either way. That cuts the other way too —
**no geometry gate here can tell a correctly mirrored twin from a botched one.** Mirror the
mesh data (MIRROR modifier, or a bmesh transform), apply it, and look at the render: the judge
is the only check you have.

## Same, or deliberately different

The judge penalises both ends, so read the brief before looping:

* Too different: *"The two handle loops are identical in size and shape, failing to provide a
  distinct smaller thumb loop and larger multi-finger loop"* — 3 findings, `major`, urdf. When
  the brief distinguishes the members, they are **separate plan parts**, not instances.
* Too same: *"Cliff walls are composed of identical, rigidly stacked blocks arranged in perfectly
  straight lines, lacking any organic shape"* — `critical`. For natural repeats, give copies a
  seeded per-copy variation in scale and rotation small enough to stay inside the contract
  tolerance (`max(0.01 m, 0.10 x extent)` per axis) so it cannot cost you a contract finding
  — see references §6.

## Finish

Run `build`, then `check_contract` (count + both box readings) and `check_connectivity` (the
loop's placement). Read the numbers; do not eyeball the montage.

Depth, the full failing-message catalogue and the per-language array/mirror idioms:
`references/instances.md`.
