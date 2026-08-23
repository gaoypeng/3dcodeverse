# URDF + bpy authoring contract — track `articulated_object`, language `urdf_blender`

## Files
```
src/model.py    pure bpy (same rules as the blender contract): ONE mesh object per link,
                obj.name == link name, built in WORLD coordinates at the REST pose (= URDF q=0).
src/robot.urdf  hand-written URDF: links + joints; every link has ONE <visual> and ONE identical
                <collision>, geometry <mesh filename="meshes/<link>.glb"/>, origin = −(link frame).
```
The harness runs `model.py` headless, exports `artifacts/meshes/<link>.glb` with the WORLD
coordinates baked in (nothing is shifted for you), lints the URDF, checks that FK at q=0
puts every mesh back where you authored it (1 mm — the build FAILS otherwise and prints the
corrected `<origin>`), sweeps every joint over its range for collisions, renders q=0 plus
every joint at its lower/upper limit, and builds `object.glb` with one node per link.
You never export.

## Frame rule (the whole trick — follow it literally)
* Z up, −Y front, +X right, meters, radians.  Object on z = 0, centred on Z.
* **q = 0 = what you build = the plan's rest pose**: the plan's bbox centres/extents and
  pivots describe this pose (usually doors closed, drawers in, lids down, arms at home —
  the plan's `rest` is 0 then).  The harness does not open anything for the showcase; the
  judge sees q=0 and every joint at both limits.
* **Every joint `rpy="0 0 0"`.**  All link frames stay axis-aligned with the world; a
  link frame differs from the world frame only by a translation to its pivot.
* Root link frame = world origin `(0,0,0)`.  Every other link frame = its joint's PIVOT: a
  world point ON the joint axis (hinge line / slide axis / wheel axle); fixed joints: the
  child's attachment point.
* `<joint><origin xyz>` = `pivot_child − frame_parent` (plain subtraction; for a child of
  the root that is just the pivot; for a handle on a door: `pivot_handle − pivot_door`).
* `<visual>` AND `<collision>` `<origin xyz>` = `−pivot_link` (root: `0 0 0`), because the
  mesh file holds world coordinates and the link frame sits at the pivot.  This is the
  line everyone gets wrong; the harness verifies it numerically and tells you the numbers.
* `<axis xyz>` is the axis in WORLD coordinates (unit vector).  Sign: positive q must move
  the child the way a user expects (door swings OPEN, drawer pulls OUT towards −Y front,
  lid lifts UP).  If positive q closes it, negate the axis — never swap lower/upper.
* Limits: `revolute`/`prismatic` MUST have `<limit lower upper effort velocity/>` with
  `lower ≤ 0 ≤ upper` (q=0 inside the range).  From the plan: `lower = plan.lower − rest`,
  `upper = plan.upper − rest` — identical to the plan's numbers when `rest` is 0 (the
  skeleton already wrote them).  `continuous`: `<limit effort velocity/>` only; `fixed`: none.
* Single-root tree: exactly one link that is no joint's child (the base); every other
  link is the child of exactly one joint; no cycles; ≤ 60 links.  Moving parts that carry
  hardware (handle on a door, knob on a drawer) attach with a `fixed` joint to the moving
  link, not to the base.
* Clearance: 1–3 mm between a moving part and its housing over the WHOLE range (drawer vs
  cabinet walls, door vs frame); no penetration at any q in [lower, upper].  Fixed
  children touch their parent (gap ≤ 2 mm) or sink ≤ 2 mm into it.
* Names: link name == Blender object name == `meshes/<link>.glb` stem, case-sensitive,
  identical in both files.  Keep the skeleton's names (snake_case of the plan parts:
  `door`, `handle_left`); the plan's PascalCase spelling is accepted too.  Never `Door.001`.
  Joint names: the plan's joint names; all unique.

## Allowed / forbidden
Same as the blender contract for `model.py` (bpy, bmesh, mathutils, math, random).
URDF: no `<gazebo>`, `<transmission>`, `<sensor>`, xacro, `package://`, mesh scale,
inline primitives, `mimic` (unless the plan has it).  Inertial blocks are optional.

## COMPLETE minimal example (verified: harness build + FK check + joint sweep)
A pedal bin: body + lid hinged at the back edge, knob fixed on the lid.  q = 0 = lid closed.
`src/model.py`
```python
import bpy, bmesh
from mathutils import Vector

W, D, H, T = 0.25, 0.25, 0.35, 0.010          # bin outer size, wall/lid thickness (m)
PIVOT = {"body": Vector((0, 0, 0))}            # link frame (world) per link; root = origin

def make_box(name, size, center):
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); o.location = center
    bpy.context.scene.collection.objects.link(o)
    return o

body = make_box("body", (W, D, H), (0, 0, H / 2))                    # root link, on the ground
lid = make_box("lid", (W, D, T), (0, 0, H + 0.001 + T / 2))          # 1 mm above the rim, closed
knob = make_box("knob", (0.03, 0.03, 0.02), (0, -D / 2 + 0.04, H + 0.001 + T + 0.0085))  # 1.5 mm into lid
PIVOT["lid"] = Vector((0, D / 2, H + 0.001))                          # hinge line = back top edge
PIVOT["knob"] = Vector((0, -D / 2 + 0.04, H + 0.001 + T))             # its attachment point

def xyz(v):
    return f'{v.x:.4f} {v.y:.4f} {v.z:.4f}'
for child, parent in (("lid", "body"), ("knob", "lid")):
    print(child, "joint origin", xyz(PIVOT[child] - PIVOT[parent]), "| visual+collision origin", xyz(-PIVOT[child]))
```
`src/robot.urdf` — lid opens UP: +q about +X at the back edge would push the front edge
DOWN into the body, so the axis is −X (never fix a sign by swapping limits):
```xml
<?xml version="1.0"?>
<robot name="pedal_bin">
  <link name="body">
    <visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></visual>
    <collision><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></collision>
  </link>
  <link name="lid">   <!-- frame at the hinge pivot (0 0.125 0.351) → origin = its negation -->
    <visual><origin xyz="0 -0.125 -0.351" rpy="0 0 0"/><geometry><mesh filename="meshes/lid.glb"/></geometry></visual>
    <collision><origin xyz="0 -0.125 -0.351" rpy="0 0 0"/><geometry><mesh filename="meshes/lid.glb"/></geometry></collision>
  </link>
  <link name="knob">  <!-- frame at its attachment point (0 -0.085 0.361) -->
    <visual><origin xyz="0 0.085 -0.361" rpy="0 0 0"/><geometry><mesh filename="meshes/knob.glb"/></geometry></visual>
    <collision><origin xyz="0 0.085 -0.361" rpy="0 0 0"/><geometry><mesh filename="meshes/knob.glb"/></geometry></collision>
  </link>
  <joint name="body_to_lid" type="revolute">
    <parent link="body"/><child link="lid"/>
    <origin xyz="0 0.125 0.351" rpy="0 0 0"/>   <!-- pivot_lid - frame_body -->
    <axis xyz="-1 0 0"/>
    <limit lower="0" upper="1.5" effort="5" velocity="2"/>
  </joint>
  <joint name="lid_to_knob" type="fixed">
    <parent link="lid"/><child link="knob"/>
    <origin xyz="0 -0.21 0.01" rpy="0 0 0"/>    <!-- pivot_knob - pivot_lid -->
  </joint>
</robot>
```
See the cookbook for full cabinet (door + handle + drawer), laptop and cart examples.
