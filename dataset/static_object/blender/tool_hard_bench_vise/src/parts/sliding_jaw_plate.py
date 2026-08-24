"""SlidingJawPlate — replaceable hardened steel jaw insert for the sliding jaw.

Hardened tool-steel plate (125 mm wide, 35 mm high, 15 mm thick) with serrated cross-hatching and two countersunk hex screws, facing backward towards +Y, leaving an open 35 mm clamping gap with the fixed jaw plate.
Material: dark hardened steel with milled teeth.
Plan bbox: center (0.000, -0.050, 0.165) extents (0.125, 0.015, 0.035)
  -> x in [-0.0625, 0.0625], y in [-0.0575, -0.0425], z in [0.1475, 0.1825]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_jaw_plate_material, link_object

SLIDING_JAW_PLATE_CENTER = (0.000, -0.050, 0.165)
SLIDING_JAW_PLATE_EXTENTS = (0.125, 0.015, 0.035)

def build_sliding_jaw_plate() -> bpy.types.Object:
    bm = bmesh.new()
    
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=SLIDING_JAW_PLATE_EXTENTS, verts=bm.verts)

    # Serration grooves across the clamping face (facing +Y at local y = +0.0075)
    n_grooves = 7
    z_step = 0.030 / (n_grooves + 1)
    for i in range(n_grooves):
        gz = -0.015 + (i + 1) * z_step
        gr = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.120, 0.001, 0.0015), verts=gr["verts"])
        bmesh.ops.transform(
            bm,
            matrix=Matrix.Translation((0.0, 0.0075 - 0.0004, gz)),
            verts=gr["verts"],
        )
    
    for x_pos in [-0.035, 0.035]:
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            segments=16,
            radius1=0.0045,
            radius2=0.003,
            depth=0.003,
            matrix=Matrix.Translation((x_pos, 0.0075 - 0.0015, 0.0)) @ Matrix.Rotation(-math.pi / 2, 4, 'X')
        )

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0002)

    me = bpy.data.meshes.new("SlidingJawPlate")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("SlidingJawPlate", me)
    obj.location = SLIDING_JAW_PLATE_CENTER
    obj.data.materials.append(get_jaw_plate_material())
    return link_object(obj)
