# URDF + bpy authoring contract — track `articulated_object`, language `urdf_blender`

## Files
```
src/model.py    pure bpy (same rules as the blender contract): ONE mesh object per link,
                obj.name == link name, built in WORLD coordinates at the ZERO configuration.
src/robot.urdf  hand-written URDF: links + joints; every link's visual is
                <mesh filename="meshes/<link>.glb"/> with <origin xyz="0 0 0" rpy="0 0 0"/>.
```
The harness runs `model.py` headless, exports `artifacts/meshes/<link>.glb` **in the
link frame** (it translates your world mesh by −(link origin in world) — translation
only, see the frame rule), lints the URDF, runs FK, sweeps every joint over its range
for collisions, renders the rest/showcase pose and builds `object.glb` with one node
per link.  You never export.

## Frame rule (the whole trick — follow it literally)
* Z up, −Y front, +X right, meters, radians.  Object on z = 0, centred on Z.
* **Zero configuration = what you build** = doors closed, drawers in, lids down, arms at
  home.  The harness shows articulation by rendering at the plan's `rest` values
  (e.g. door 0.3 rad ajar) and by `joint_sweep` — do not bake an ajar pose into geometry.
* **Every joint `rpy="0 0 0"`.**  All link frames stay axis-aligned with the world; a
  link frame differs from the world frame only by a translation to its pivot.
* `<joint><origin xyz>` = `pivot_child_world − pivot_parent_world` (plain subtraction),
  where `pivot_root_world = (0,0,0)` and a pivot is a point ON the joint axis (hinge line
  / slide axis / wheel axle).  Fixed joints: the child's attachment point.
* `<axis xyz>` is therefore the axis in WORLD coordinates (unit vector).  Sign: positive
  q must move the child the way a user expects (door swings OPEN, drawer pulls OUT
  towards −Y front, lid lifts UP).  If positive q closes it, negate the axis — never swap
  lower/upper.
* Child visuals: `<origin xyz="0 0 0" rpy="0 0 0"/>` ALWAYS (the harness already placed
  the mesh in the link frame).  No `scale`, no `package://`, no inline primitives.
* Limits: `revolute`/`prismatic` MUST have `<limit lower upper effort velocity/>`
  with `lower < upper`, lower ≤ 0 ≤ upper (zero config inside the range);
  `continuous` has `<limit effort velocity/>` only; `fixed` none.
* Single-root tree: exactly one link that is no joint's child (the base); every other
  link is the child of exactly one joint; no cycles; ≤ 60 links.  Moving parts that carry
  hardware (handle on a door, knob on a drawer) attach with a `fixed` joint to the moving
  link, not to the base.
* Clearance: 1–3 mm between a moving part and its housing over the WHOLE range
  (drawer vs cabinet walls, door vs frame); no penetration at any q in [lower, upper].
* Names: link names == Blender object names == plan part names (PascalCase).  Joint names
  `<Parent>To<Child>` or the plan's joint names; all unique.

## Allowed / forbidden
Same as the blender contract for `model.py` (bpy, bmesh, mathutils, math, random).
URDF: no `<gazebo>`, `<transmission>`, `<sensor>`, xacro, `package://`, mesh scale,
`mimic` (unless the plan has it).  Inertial blocks are optional (harness fills defaults).

## COMPLETE minimal example (verified: bpy build + URDF parse + FK)
A pedal bin: body + lid hinged at the back edge, knob fixed on the lid.  Zero config =
lid closed; the plan's `rest` (e.g. 0.5 rad) is what renders show.
`src/model.py`
```python
import bpy, bmesh
from mathutils import Vector

W, D, H, T = 0.25, 0.25, 0.35, 0.010          # bin outer size, wall/lid thickness (m)
PIVOT = {"Body": Vector((0, 0, 0))}

def make_box(name, size, center):
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); o.location = center
    bpy.context.scene.collection.objects.link(o)
    return o

body = make_box("Body", (W, D, H), (0, 0, H / 2))                    # root link, on the ground
lid = make_box("Lid", (W, D, T), (0, 0, H + 0.001 + T / 2))          # 1 mm above the rim, closed
knob = make_box("Knob", (0.03, 0.03, 0.02), (0, -D / 2 + 0.04, H + 0.001 + T + 0.008))  # 2 mm into lid
PIVOT["Lid"] = Vector((0, D / 2, H + 0.001))                          # hinge line = back top edge
PIVOT["Knob"] = Vector((0, -D / 2 + 0.04, H + 0.001 + T))             # its attach point
for child, parent in (("Lid", "Body"), ("Knob", "Lid")):
    d = PIVOT[child] - PIVOT[parent]
    print(child, f'<origin xyz="{d.x:.4f} {d.y:.4f} {d.z:.4f}" rpy="0 0 0"/>')
```
`src/robot.urdf` — lid opens UP: +q about +X at the back edge would push the front edge
DOWN into the body, so the axis is −X (never fix a sign by swapping limits):
```xml
<?xml version="1.0"?>
<robot name="PedalBin">
  <link name="Body"><visual><origin xyz="0 0 0" rpy="0 0 0"/>
    <geometry><mesh filename="meshes/Body.glb"/></geometry></visual></link>
  <link name="Lid"><visual><origin xyz="0 0 0" rpy="0 0 0"/>
    <geometry><mesh filename="meshes/Lid.glb"/></geometry></visual></link>
  <link name="Knob"><visual><origin xyz="0 0 0" rpy="0 0 0"/>
    <geometry><mesh filename="meshes/Knob.glb"/></geometry></visual></link>
  <joint name="BodyToLid" type="revolute">
    <parent link="Body"/><child link="Lid"/>
    <origin xyz="0 0.125 0.351" rpy="0 0 0"/>
    <axis xyz="-1 0 0"/>
    <limit lower="0" upper="1.5" effort="5" velocity="2"/>
  </joint>
  <joint name="LidToKnob" type="fixed">
    <parent link="Lid"/><child link="Knob"/>
    <origin xyz="0 -0.21 0.01" rpy="0 0 0"/>
  </joint>
</robot>
```
See the cookbook for full cabinet (door + handle + drawer), laptop and cart examples.
