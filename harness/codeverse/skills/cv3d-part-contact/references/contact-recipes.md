# Contact recipes and the finding-to-fix table

Companion to `cv3d-part-contact`. Everything here was checked against
`codeverse/spatial/connectivity.py`, `codeverse/spatial/measure.py`, the Blender 5.0.1
wrapper in `codeverse/languages/blender/wrappers/run_bpy.py`, and the recorded gate
findings under `bench/out/**/artifacts/gates/**` (mined 2026-08-25).

## 1. Finding to fix

| finding (verbatim shape) | severity | what it means | do this |
|---|---|---|---|
| `'A' and 'B' interpenetrate by ~N mm (F% of surface samples inside)` | WARN below 10 mm, ERROR above | the deepest sampled point of one part is N mm inside the other, and at least 2% of one part's surface samples are inside | shorten or move the *smaller* part so the overlap is 0.5-1.5 mm; if N is large the two parts are modelling the same volume twice, so delete one |
| `part 'X' is floating: nearest supported part is 'Y' at N mm` | always ERROR (cap 0.60) | X is not in the ground-touching component | apply the printed translation, or extend X by N mm toward Y; prefer extending |
| `part 'X' is floating: touches nothing` | ERROR | X shares no AABB neighbourhood with anything | it is in the wrong place by more than its own size — recompute its origin from its neighbour's plan bbox |
| `no part touches the ground (z=0)` | WARN | the whole model is off the floor | move the assembly, not one part |
| `part 'X' contains N tiny disconnected island(s) (< 5% of the part size)` | WARN | N loose shells inside one part's mesh | union them into the body, grow them past 5% of the part's largest extent, or delete them (usually boolean debris) |
| `part 'X' is tiny (N mm) - skipped for contact checks` | INFO | largest extent under 10 mm | not a defect; be aware this part is invisible to the contact graph, so it can neither support nor be reported as floating |

Recorded example depths, all real (`bench/out`, mined 2026-08-25): the median reported
interpenetration is **5.0 mm** over 1,088 findings, and **86%** of them are under the 10 mm
ERROR line — i.e. the typical failure is a WARN that nobody fixed, not a catastrophe.

## 2. bpy: derive the span, weld 1 mm

```python
SEAT_Z, SEAT_T = 0.45, 0.04          # from plan.parts["Seat"].bbox
WELD = 0.001                          # 1 mm: inside CONTACT_GAP_M, under PENETRATION_WARN_M
LEG_H = SEAT_Z - SEAT_T + WELD        # reaches 1 mm into the seat -- NOT a re-typed 0.411
```

The point is `SEAT_Z - SEAT_T`: when the seat moves in a later round the leg follows. A
literal `0.411` is a `contract/part_bbox` finding waiting to happen.

## 3. bpy: three sub-shapes, ONE part

Verified on Blender 5.0.1: this exports as a single node `Housing`, so no pair-wise
penetration test can ever run inside it.

```python
import bpy, bmesh
from mathutils import Matrix

bm = bmesh.new()
bmesh.ops.create_cube(bm, size=0.2, matrix=Matrix.Translation((0, 0, 0.1)))
bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.03, radius2=0.03,
                      depth=0.25, matrix=Matrix.Translation((0.06, 0, 0.30)))
bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.04,
                          matrix=Matrix.Translation((-0.06, 0, 0.30)))
me = bpy.data.meshes.new("Housing"); bm.to_mesh(me); bm.free()
housing = bpy.data.objects.new("Housing", me)
bpy.context.scene.collection.objects.link(housing)
```

The three shapes are still three *islands* of that one mesh. That is fine here because each
is well over 5% of the part's largest extent (0.425 m, so the cutoff is 21 mm). Drop a 15 mm
boss in the same way and you buy a stray-island WARN — fuse it instead (section 4).

## 4. bpy: fuse small detail, and never leave the cutter

```python
def fuse(obj, other):
    """other becomes part of obj's mesh: one island, no stray-island warning."""
    m = obj.modifiers.new("Union", 'BOOLEAN'); m.operation = 'UNION'; m.object = other
    m.solver = 'EXACT'
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(other, do_unlink=True)
```

For a DIFFERENCE the same shape applies. If you would rather leave the modifier for the
exporter to evaluate (it exports with `export_apply=True`, so unapplied modifiers are
applied at export), you must hide the cutter or it ships as its own part:

```python
cutter.hide_render = True          # any of hide_render / hide_viewport / hide_set(True)
cutter.hide_viewport = True        # keeps it out of the export; the boolean still evaluates
```

Verified on Blender 5.0.1 with the harness's exact export call: with no flag set, the GLB
came back with two parts (`Base`, `Cutter`); with any one flag set it came back with one,
and the notch was still cut.

## 5. CadQuery

Not corpus-backed — this language has **zero graded bench runs**; the rules below are read
off `codeverse/prompts/cadquery/cookbook.md` and the same gate constants.

* One `Workplane` result per plan part, and `len(result.solids().vals()) == 1` after every
  cut: a cut that splits a part into two solids is exactly the stray-island WARN in advance.
* Weld with `.union(other).clean()` — `clean()` merges the coplanar faces so the result is
  one solid, not two touching ones.
* Cutters must overshoot the face they pierce (the cookbook says >= 0.1 mm; coplanar faces
  make the boolean fail silently), but the *part-to-part* overlap still has to land in the
  0.5-1.5 mm band.

## 6. URDF (articulated_object)

`check_connectivity` runs on the rest pose (q = 0) exactly as above, over the link meshes.
Clearance across the joint *sweep* is a different gate (`joint_sweep`) and belongs to
`cv3d-urdf-joints`; a rest pose that is already 5 mm overlapped will fail both. Recorded
`joint_sweep` overlaps in this corpus run 3-5 mm already at the rest pose and reach 20 mm at
a joint limit, which is the signature of a hinge whose two links were welded like static
parts instead of being given running clearance.

## 7. The loop

```
build  ->  check_connectivity  ->  fix the ERRORs, then the deepest WARN  ->  build
```

`check_connectivity` takes no arguments and answers in your authoring frame. `isolate` the
part you doubt and `cross_section` through it when a finding does not make sense. Never
finish a round on an ERROR.
