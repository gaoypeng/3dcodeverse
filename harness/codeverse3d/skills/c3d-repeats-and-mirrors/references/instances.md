# Instances, mirrors and arrays — depth

Everything here was read out of the live harness on 2026-08-25:
`codeverse3d/spatial/contract.py`, `codeverse3d/contracts/plan.py`,
`codeverse3d/conventions.py`, `codeverse3d/tracks/planner.py`,
`codeverse3d/prompts/blender/cookbook.md`, and 2,121 gate reports under `bench/out`.

## 1. Exactly how the gate matches a name to a plan part

`contract.match_parts` runs two passes over the measured GLB nodes.

1. **Exact pass.** For each plan part, claim every node whose `to_snake(name)` equals the plan
   part's `to_snake(name)`.
2. **Instance pass.** For each plan part, claim the remaining nodes named `{snake}`, a
   separator (`_`, `.` or `-`) and one to three digits (`conventions.split_instance`) — but never
   a node whose snake name is *reserved*, i.e. is the exact snake name of some other plan part.

Pass 2's reservation exists because of a real regression: a plan with `Shelf` and `Shelf2` had
`Shelf` swallow the `Shelf2` node, producing a false `plan part 'Shelf2' is missing from the
GLB` ERROR on geometry that matched the plan exactly.

Consequences you can act on:

* `Leg_0`, `Leg.1`, `Leg-2` all count as instances of plan part `Leg`; `Leg3` does NOT (no
  separator: it is another name, so plan part `Leg` is reported missing). Only up to three digits.
* Blender's auto-suffix `Leg.001` also matches (3 digits) — but do not rely on it; auto-suffixes
  mean you created a name collision and the numbering is not yours.
* `LegFront` does **not** match `Leg` (the regex allows digits only after the stem).
* If the plan has both `Leg` and `Leg2` as separate rows, name your nodes exactly `Leg` and
  `Leg2` and do not add `Leg_0`.

## 2. The two box readings, in full

For a plan row with `instances > 1` and `k` matched nodes:

```
per[i]      = extents(node_i)          - plan.extents      # each copy on its own
union       = extents(bbox of all k)   - plan.extents      # the group's span
worst_each  = the per[i] with the largest |delta| / tol
report      = whichever of (union, worst_each) has the smaller |delta| / tol
```

`tol` is per axis: `max(BBOX_TOLERANCE_M, REL_TOL * plan_extent)` = `max(0.01 m, 0.10 * extent)`.
`ERROR_FACTOR = 3.0`, so `ratio > 3.0` is an ERROR and anything above 1.0 is a WARN.
`check_center=False` for this branch — the centre delta is computed but never reported.

The message tells you which reading lost:

```
'Leg' (each instance) bbox deviates from the plan (worst 3.4x tolerance)      [error]
'BaseFootPlates' (all instances) bbox deviates from the plan (worst 1.1x tolerance)  [warn]
```

`(each instance)` means at least one copy is the wrong size. `(all instances)` means the copies
are individually plausible but their union does not span the planned box — usually the spacing,
not the shape.

The `fix_hint` gives you the numbers in **your authoring frame**, already converted back from the
GLB frame, e.g. `size 21.0x3.5x41.0 vs planned 3.5x3.5x41.0 cm (delta x+17.5cm, y+0.0cm,
z+0.0cm)`. Paste those numbers; do not re-derive them.

## 3. Count mismatch

```
'ApexFinial': found 1 instance(s), plan asks for 2     [warn]
'ShoulderFinial': found 2 instance(s), plan asks for 4 [warn]
```

Fix hint: `name the copies ApexFinial_0 .. ApexFinial_1`. Two causes in the corpus, in order:

1. the loop was written but every copy landed on the same object name, so Blender collapsed them
   (or the exporter emitted one node);
2. the copies were parented under an Empty. **Never parent parts or instances under an Empty** —
   the harness measures top-level GLB nodes, so an Empty merges its whole subtree into one part
   and the other copies are reported missing.

If a plan part is entirely absent the severity jumps: `plan part 'X' is missing from the GLB` is
an ERROR, with hint `create a part named exactly 'X' (x4 as X_0..X_3)`.

## 4. Corpus detail (bench/out, mined 2026-08-25)

Denominator: all 217 run records under `bench/out` that carry gate artefacts; 2,121 gate reports.

| observation | value |
|---|---|
| runs where `contract/instance_bbox` fired | 38 |
| of those, runs that reached ERROR | 12 |
| findings, `(each instance)` | 144 |
| findings, `(all instances)` | 47 |
| median worst deviation | 2.0 x tolerance |
| max worst deviation | 27.7 x tolerance |
| runs where `contract/instance_count` fired | 1 (6 findings) |

Judge issues that are the same defect seen from the render side (all corpus-verbatim):

* `major / assembly` — "All four stool legs float above the ground plane, not providing support."
* `major / assembly` — "All four wheels interpenetrate the chassis significantly."
* `minor / assembly` — "All four feet interpenetrate the BasePlinth by 3.0 mm."
* `major / fidelity` (x3) — "The two handle loops are identical in size and shape, failing to
  provide a distinct smaller thumb loop and larger multi-finger loop."
* `critical / geometry` — "Cliff walls are composed of identical, rigidly stacked blocks arranged
  in perfectly straight lines, lacking any organic shape."

## 5. Per-language idioms

### blender / urdf_blender (bpy)

```python
def linked_copy(src, name, location):
    o = bpy.data.objects.new(name, src.data)      # shares mesh data: identical by construction
    o.location = location
    o.rotation_euler = src.rotation_euler
    o.scale = src.scale
    return link(o)
```

* Mirror inside one part: `add_mirror(obj, 'X')` then `apply_modifiers(obj)`. Move the **mesh**
  off the plane (`obj.data.transform(Matrix.Translation((-dx, 0, 0)))`) so the object origin
  stays on x = 0 — the modifier mirrors across the origin plane, not across the world.
* Repeated detail inside one part: ARRAY modifier (`add_array`) for rows,
  `radial_array(obj, count)` for rings. Both produce ONE object, which is what a single plan
  part must export as.
* `bpy.ops.mesh.primitive_cube_add(scale=...)` without `size=1` builds a 2 m cube times the
  scale — every extent doubles. `lint:blender` flags it. Prefer the cookbook's bmesh
  `make_box`.
* For a URDF pair, each copy is its **own link** with its own joint and pivot. Instancing is a
  Blender convenience; the URDF still needs `left_wheel` and `right_wheel` as separate links.

### cadquery

Polar and rectangular arrays place points, then one feature call consumes them:

```python
(cq.Workplane("XY").faces(">Z").workplane()
   .polarArray(0.04, 0, 360, 6)          # radius, startAngle, total angle, count
   .hole(0.005))                          # ONE feature call consumes all 6 points
```

`polarArray` takes the count and sweeps the total angle itself, which is exactly the
"angle from the count" rule the loop version spells out. `.rarray(xSpacing, ySpacing,
xCount, yCount)` for grids; `.pushPoints([...])` only when the positions are genuinely
irregular. One array plus one cut is far cheaper than a loop of cuts and never leaves
slivers behind.

### three.js

`InstancedMesh` is one draw call but also **one node** — good for scenery, wrong for a plan row
that must be measured per copy. When the plan names `instances: N` and the gate must see N
nodes, emit N `THREE.Mesh` objects sharing one geometry, named `Name_0..Name_{N-1}`, added at
the top level of the exported group.

## 6. Jitter that is safe

Per-copy variation reads as real, but it competes with the contract tolerance. The tolerance on
a 4 cm part is `max(0.01, 0.10 * 0.04) = 0.01 m` — an absolute 10 mm floor, generous. On a
1 m part it is 0.10 m. Scale jitter of 1-2 % is therefore always safe; position jitter is free
because the centre is not checked for instanced parts. Rotation jitter can enlarge a copy's
axis-aligned bbox: a 0.6 degree tilt on a 0.41 m leg adds about 4 mm to the x extent, which is
inside the 10 mm floor but not inside a 1 % budget on a 5 cm part. Keep tilt under 1 degree on
parts smaller than 10 cm.
