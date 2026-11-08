# The frame arithmetic, end to end (verified by the build + FK check + sweep)

One hinge: a 0.25 m bin body (root, on the ground) and a 10 mm lid resting 1 mm above the rim,
hinged along its back top edge; q = 0 = lid closed.  It shows the origins and where they come
from, not how to build anything.

`src/model.py`
```python
import bpy, bmesh
from mathutils import Vector

for name, size, centre in (("body", (0.25, 0.25, 0.35), (0, 0, 0.175)),   # root link: frame = world origin
                           ("lid", (0.25, 0.25, 0.01), (0, 0, 0.356))):   # authored CLOSED (q = 0), 1 mm above the rim
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); o.location = centre; bpy.context.scene.collection.objects.link(o)
PIVOT = {"body": Vector((0, 0, 0)), "lid": Vector((0, 0.125, 0.351))}  # link frame = a world point on the hinge line
print("lid: joint origin", tuple(PIVOT["lid"] - PIVOT["body"]), "visual/collision origin", tuple(-PIVOT["lid"]))
```
`src/robot.urdf` — the plan's `motion` says the lid opens UP: +q about +X at the back edge would
push the front edge DOWN into the body, so the axis is −X (a sign is fixed by negating the axis,
never by swapping the limits):
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
  <joint name="body_to_lid" type="revolute">
    <parent link="body"/><child link="lid"/>
    <origin xyz="0 0.125 0.351" rpy="0 0 0"/>   <!-- pivot_lid - frame_body -->
    <axis xyz="-1 0 0"/>                         <!-- +q lifts the front edge: the motion word is "up" -->
    <limit lower="0" upper="1.5" effort="5" velocity="2"/>
  </joint>
</robot>
```
