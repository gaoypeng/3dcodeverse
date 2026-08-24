"""SideMirror — exterior side rearview mirrors.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan:
# SideMirror center (0.980, -0.720, 1.220) extents (0.240, 0.160, 0.140)
# x in [0.860, 1.100], y in [-0.800, -0.640], z in [1.150, 1.290]
# Attaches to: Cab (Cab door surface is at x = +-0.89)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_side_mirror_mesh(name_prefix, center_x, center_y, center_z, is_right):
    housing_mat = make_material(f"{name_prefix}_Housing", (0.08, 0.08, 0.09), roughness=0.6, metallic=0.1)
    mirror_mat = make_material(f"{name_prefix}_Glass", (0.95, 0.95, 0.98), roughness=0.05, metallic=0.98)

    bm = bmesh.new()

    # 1. Aerodynamic mirror housing
    # Center of housing at x = center_x + (0.03 if is_right else -0.03) -> 1.01 / -1.01
    # Housing size: dx=0.18, dy=0.16, dz=0.14 -> x span [0.92, 1.10], y span [-0.80, -0.64], z span [1.15, 1.29]
    hx = center_x + (0.03 if is_right else -0.03)
    hy = center_y
    hz = center_z

    house = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.18, 0.16, 0.14), verts=house["verts"])
    bmesh.ops.translate(bm, vec=(hx, hy, hz), verts=house["verts"])

    # 2. Reflective mirror glass facing rearward (+Y)
    mglass = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.15, 0.02, 0.12), verts=mglass["verts"])
    bmesh.ops.translate(bm, vec=(hx, hy + 0.075, hz), verts=mglass["verts"])

    # 3. Mounting arm connecting to Cab door (x reaches from cab door +-0.86 to housing +-0.94)
    arm_start_x = (0.86 if is_right else -0.86)
    arm_end_x = (0.94 if is_right else -0.94)
    arm_len = abs(arm_end_x - arm_start_x)
    arm_cx = (arm_start_x + arm_end_x) / 2.0

    arm = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(arm_len + 0.01, 0.05, 0.04), verts=arm["verts"])
    bmesh.ops.translate(bm, vec=(arm_cx, center_y - 0.02, center_z - 0.02), verts=arm["verts"])

    me = bpy.data.meshes.new(name_prefix)
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new(name_prefix, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(housing_mat)
    obj.data.materials.append(mirror_mat)

    for poly in obj.data.polygons:
        # Glass face facing +Y
        if poly.center.y > hy + 0.06 and poly.normal.y > 0.5:
            poly.material_index = 1
        else:
            poly.material_index = 0

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.008
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj

def build_side_mirror():
    objs = []
    # SideMirror_0: right side (+X), SideMirror_1: left side (-X)
    objs.append(create_side_mirror_mesh("SideMirror_0", 0.980, -0.720, 1.220, is_right=True))
    objs.append(create_side_mirror_mesh("SideMirror_1", -0.980, -0.720, 1.220, is_right=False))
    return objs
