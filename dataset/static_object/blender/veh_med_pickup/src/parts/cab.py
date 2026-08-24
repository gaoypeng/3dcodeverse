"""Cab — two-door driver cabin with tinted glass windows and roof.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan:
# Cab center (0.000, -0.250, 1.340) extents (1.780, 1.500, 0.780)
# x in [-0.890, 0.890], y in [-1.000, 0.500], z in [0.950, 1.730]
# Attaches to: ChassisAndLowerBody (z overlaps chassis top at 0.95-1.01)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_cab():
    body_mat = make_material("Cab_Paint_Green", (0.08, 0.22, 0.12), roughness=0.25, metallic=0.1)
    glass_mat = make_material("Cab_Glass_Dark", (0.05, 0.08, 0.10), roughness=0.1, metallic=0.9)
    trim_mat = make_material("Cab_Trim_Black", (0.1, 0.1, 0.1), roughness=0.6, metallic=0.1)

    bm = bmesh.new()

    # 1. Main Cab Shell / Doors / Lower Cabin Base
    # Base from z = 0.950 to 1.320, y from -1.000 to 0.500, x from -0.890 to 0.890
    base = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.78, 1.50, 0.37), verts=base["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -0.25, 1.135), verts=base["verts"]) # z from 0.950 to 1.320

    # 2. Upper cab / Greenhouse (Cabin Pillars and Roof frame)
    # Roof from z=1.320 to 1.730, y from -0.700 to 0.490, x from -0.850 to 0.850
    greenhouse = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.70, 1.19, 0.41), verts=greenhouse["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -0.105, 1.525), verts=greenhouse["verts"]) # z from 1.320 to 1.730

    # 3. Windshield glass panel (flush / slightly recessed into sloped A-pillar front)
    # Front windshield at y = -0.70 to -0.66, z in [1.33, 1.71], width 1.56
    ws = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.56, 0.04, 0.38), verts=ws["verts"])
    rot_ws = Matrix.Rotation(math.radians(20), 4, 'X')
    bmesh.ops.transform(bm, matrix=Matrix.Translation(Vector((0.0, -0.68, 1.52))) @ rot_ws, verts=ws["verts"])

    # 4. Rear window (flush against cabin back wall at y = 0.485)
    rw = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.30, 0.03, 0.30), verts=rw["verts"])
    bmesh.ops.translate(bm, vec=(0.0, 0.485, 1.53), verts=rw["verts"])

    # 5. Side Door Windows (Left & Right, flush with cabin side at x = +-0.852)
    for sx in [-0.852, 0.852]:
        sw = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.02, 0.80, 0.30), verts=sw["verts"])
        bmesh.ops.translate(bm, vec=(sx, -0.15, 1.52), verts=sw["verts"])

    # 6. Door handles on sides (x = +-0.895, y = -0.15, z = 1.25)
    for hx in [-0.895, 0.895]:
        dh = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.02, 0.14, 0.035), verts=dh["verts"])
        bmesh.ops.translate(bm, vec=(hx, -0.15, 1.25), verts=dh["verts"])

    me = bpy.data.meshes.new("Cab")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("Cab", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(body_mat)
    obj.data.materials.append(glass_mat)
    obj.data.materials.append(trim_mat)

    # Assign glass and trim materials
    for poly in obj.data.polygons:
        # Check if poly belongs to windshield, rear window, or side windows
        if poly.center.z > 1.34:
            if abs(poly.center.y - (-0.68)) < 0.12 or abs(poly.center.y - 0.485) < 0.05 or abs(poly.center.x) > 0.84:
                poly.material_index = 1
            else:
                poly.material_index = 0
        elif abs(poly.center.x) > 0.885 and abs(poly.center.y - (-0.15)) < 0.10:
            poly.material_index = 2
        else:
            poly.material_index = 0

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.015
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj
