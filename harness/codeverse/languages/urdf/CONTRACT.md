# Authoring contract — language `urdf_blender` (articulated objects)

You write exactly TWO raw files. No SDK, no imports from the harness.

```
src/model.py     pure bpy  — one mesh object per LINK, at its rest-pose WORLD placement
src/robot.urdf   native URDF — links + joints; meshes referenced as meshes/<link>.glb
```

The harness runs `model.py` headless, exports `meshes/<link>.glb` per link, lints the
URDF, checks that the URDF frames reproduce your geometry, sweeps every joint through
its range looking for collisions / floating parts, and renders an articulation sheet
(rest + every joint at both limits) for the judge.

## 1. Frame, units, naming

* Z is UP, **-Y is the FRONT** of the object, +X its right. Meters. Radians.
* The object stands on the ground: lowest point at z = 0, footprint centred on the Z axis.
* Link names are `snake_case` part names: `base`, `door`, `drawer`, `lid`, `handle_left`
  (the plan's PascalCase spelling is accepted too — identical in both files, never `Door.001`).
  Never state words (`door_open`): states come from joints.
* The bpy object for a link is named **exactly** like the link (case-sensitive).

## 2. `src/model.py` — the meshes (pure bpy)

* The scene is EMPTY when your script starts (no default cube). Build geometry with
  `bpy.ops.mesh.primitive_*`, `bmesh`, modifiers, booleans — anything in bpy.
* ONE mesh object per URDF link, named exactly `<link>`, placed where the part sits in the
  **authored rest pose** (world coordinates). Extra helper objects must be parented under a
  link object (`ob.parent = link_obj`) — the wrapper joins every descendant into that link.
  Any mesh object that is not a link (or under one) is a build error.
* Visible detail lives in the meshes (panels, bevels, handles, rails, hinge barrels).
  Materials are welcome (`ob.data.materials.append(...)`, Principled BSDF colours).
* Do NOT: create cameras/lights, render, export, import files, `bpy.ops.wm.*`, `sys.exit`,
  network/subprocess, infinite loops. The harness owns all of that.
* The wrapper exports each link with world-space geometry baked in (`meshes/<link>.glb`,
  Z-up raw coordinates — NOT glTF Y-up — so any URDF loader places them correctly).

Copyable helper (raw bpy; keep it in your file if you like):

```python
import bpy

def box(name, center, size):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return ob

body = box("body", (0, 0, 0.40), (0.60, 0.40, 0.80))        # carcass, on the ground
door = box("door", (0, -0.21, 0.40), (0.58, 0.02, 0.78))     # front panel (front = -Y)
```

## 3. `src/robot.urdf` — links and joints (native URDF)

URDF semantics (standard): a joint's `<origin>` places the joint frame in the PARENT link
frame; `<axis>` is in the joint frame; the CHILD link frame coincides with the joint frame at
`q = 0`; `q = 0` is YOUR authored rest pose (the pose the plan's bboxes/pivots describe, so URDF
limits are the plan's `lower − rest .. upper − rest` — the plan's numbers when `rest` is 0).
Positive revolute `q` rotates by the right-hand rule about `<axis>`; positive prismatic `q`
slides along `<axis>`.

### The recipe we enforce (link frames at pivots, meshes authored in world)

1. The **root link frame = world origin** (`0 0 0`).
2. Every other **link frame = its parent joint's pivot point (world, rest pose)**, no
   rotation (`rpy="0 0 0"`); express the joint direction with `<axis>`, not with rpy.
3. Joint `<origin xyz>` = `pivot_world(child) − link_frame_world(parent)` (accumulate down the
   chain; for a joint whose parent is the root this is just the pivot).
4. Visual AND collision `<origin xyz>` = `−link_frame_world(link)` (the inverse transform,
   because `meshes/<link>.glb` holds world coordinates). Root link: `0 0 0`.
5. One `<visual>` + one identical `<collision>` per link; geometry is always
   `<mesh filename="meshes/<link>.glb"/>`.
6. `revolute` / `prismatic` need `<limit lower upper effort velocity>` (rad / m) with
   `lower ≤ 0 ≤ upper`; `continuous` has no lower/upper; `fixed` has no axis/limit.
   If positive motion goes the wrong way, **negate the axis — never swap the limits**.

The harness VERIFIES step 4 numerically (FK at q=0 must reproduce your model.py bboxes
within 1 mm) and tells you the exact corrected numbers if it is wrong.

### Worked example — cabinet with a front door hinged on its left edge

Door panel authored at x ∈ [-0.29, 0.29], y ∈ [-0.22, -0.20], z ∈ [0.01, 0.79]. Hinge axis is
vertical along the door's left edge: pivot world = `(-0.29, -0.20, 0)`. A door opening toward
the viewer (-Y) from a left hinge swings clockwise seen from above ⇒ axis `0 0 -1`.

```xml
<?xml version="1.0"?>
<robot name="cabinet">
  <link name="body">
    <visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></visual>
    <collision><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></collision>
  </link>
  <link name="door">   <!-- link frame at the hinge pivot (-0.29 -0.20 0) → visual origin is its negation -->
    <visual><origin xyz="0.29 0.20 0" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></visual>
    <collision><origin xyz="0.29 0.20 0" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></collision>
  </link>
  <joint name="hinge" type="revolute">
    <parent link="body"/>
    <child link="door"/>
    <origin xyz="-0.29 -0.20 0" rpy="0 0 0"/>   <!-- pivot_world(door) - frame_world(body) -->
    <axis xyz="0 0 -1"/>
    <limit lower="0" upper="1.7" effort="10" velocity="1"/>
  </joint>
</robot>
```

Drawer (prismatic, slides out of the front): pivot anywhere on the drawer, e.g. its rest
centre `(0, -0.05, 0.3)`; `<axis xyz="0 -1 0"/>`, `<limit lower="0" upper="0.35" .../>`;
visual origin `0 0.05 -0.3`. Chain: a handle on the door → parent `door`, pivot = handle
centre, joint `fixed`, joint origin = `handle_pivot − door_pivot`, visual origin = `−handle_pivot`.

## 4. What the harness checks (and tells you, with numbers)

* lint: XML, one root, tree, names, limits, axis, mesh paths, collision twins;
  model.py: forbidden calls, every link name present as a string.
* build: `model.py` runs; each link has an object; no stray objects.
* FK consistency: URDF frames reproduce model.py placement (corrected `<origin>` reported).
* pose sweep: rest + each joint at lower/mid/upper + random combos → link pairs must not
  interpenetrate (> 2 mm), nothing may float (each link touches the grounded assembly,
  ≤ 2 mm gap; hinge clearance ≤ 1 cm is only a warning), per-link islands counted.
  Penetration > 5 mm at REST fails the build.
* motion direction (when the plan states it): the child centroid must move the stated way.
* renders: rest and limit poses on one sheet — the judge sees your articulation.

Design advice: give parts 2-5 mm clearance where they move; pivots sit on real hinge lines
(door edge, lid back edge); drawers keep ≥ 1/3 of their depth inside at full extension;
continuous joints only for wheels/knobs; model visible hinges, rails and handles so nothing floats.
