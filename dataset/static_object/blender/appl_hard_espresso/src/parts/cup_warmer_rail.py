"""CupWarmerRail — guardrail around the top cup-warming plate.

U-shaped polished chrome wire rail (diameter 5 mm, height 25 mm) running along the left, rear, and right edges of the top warming surface.
Material: polished chrome steel.
Plan bbox: center (0.000, 0.030, 0.390) extents (0.250, 0.240, 0.030)
  x in [-0.125, 0.125], y in [-0.090, 0.150], z in [0.375, 0.400]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix


def make_material(name, rgb, roughness=0.1, metallic=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_cup_warmer_rail():
    """Build a 3-sided wire guardrail (left, rear, right) with vertical support posts using bmesh cylinders."""
    bm = bmesh.new()

    r_wire = 0.0025  # 5mm diameter wire
    z_top = 0.3975   # rail top bar (max Z = 0.400)
    z_bot = 0.375    # touches main chassis top at 0.385

    x_min, x_max = -0.1225, 0.1225  # total span = 0.250 with r_wire
    y_min, y_max = -0.0875, 0.1475  # total span = 0.240 with r_wire

    # 1. Top horizontal bars:
    left_len = y_max - y_min
    rot_x90 = Matrix.Rotation(math.radians(90), 4, 'X')
    rot_y90 = Matrix.Rotation(math.radians(90), 4, 'Y')

    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r_wire, radius2=r_wire, depth=left_len)
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(x_min, (y_min + y_max) / 2.0, z_top), verts=res["verts"])

    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r_wire, radius2=r_wire, depth=left_len)
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(x_max, (y_min + y_max) / 2.0, z_top), verts=res["verts"])

    rear_len = x_max - x_min
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r_wire, radius2=r_wire, depth=rear_len)
    bmesh.ops.transform(bm, matrix=rot_y90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=((x_min + x_max) / 2.0, y_max, z_top), verts=res["verts"])

    # 2. Vertical support posts
    post_h = z_top - z_bot
    post_z = (z_top + z_bot) / 2.0
    post_locs = [
        (x_min, y_min),
        (x_min, (y_min + y_max) / 2.0),
        (x_min, y_max),
        (x_max, y_min),
        (x_max, (y_min + y_max) / 2.0),
        (x_max, y_max),
        (0.0, y_max),
    ]

    for px, py in post_locs:
        res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=r_wire, radius2=r_wire, depth=post_h)
        bmesh.ops.translate(bm, vec=(px, py, post_z), verts=res["verts"])

    me = bpy.data.meshes.new("CupWarmerRail")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("CupWarmerRail", me)
    bpy.context.scene.collection.objects.link(obj)

    mat = make_material("ChromeRail", (0.95, 0.95, 0.95), roughness=0.1, metallic=1.0)
    obj.data.materials.append(mat)
    return obj
