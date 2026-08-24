"""Headlights — front headlight and turn signal clusters.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan:
# Headlights center (0.720, -2.320, 0.740) extents (0.280, 0.080, 0.180)
# x in [0.580, 0.860], y in [-2.360, -2.280], z in [0.650, 0.830]
# Attaches to: FrontGrilleAndBumper (which sits at y in [-2.378, -2.258])

def make_material(name, rgb, roughness=0.5, metallic=0.0, emission=None):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if emission:
        bsdf.inputs["Emission Color"].default_value = (emission[0], emission[1], emission[2], 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission[3]
    return mat

def create_headlight_mesh(name_prefix, center_x, center_y, center_z, is_right):
    glass_mat = make_material(f"{name_prefix}_Glass", (0.92, 0.95, 1.0), roughness=0.05, metallic=0.1)
    core_mat = make_material(f"{name_prefix}_Core", (1.0, 0.98, 0.9), roughness=0.1, metallic=0.8, emission=(1.0, 0.95, 0.85, 2.0))
    amber_mat = make_material(f"{name_prefix}_Amber", (0.95, 0.55, 0.05), roughness=0.1, metallic=0.1, emission=(0.95, 0.55, 0.05, 1.0))

    bm = bmesh.new()

    # 1. Main outer housing / lens: y in [-2.360, -2.280] (center -2.320, depth 0.08)
    lens = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.28, 0.08, 0.18), verts=lens["verts"])
    bmesh.ops.translate(bm, vec=(center_x, center_y, center_z), verts=lens["verts"])

    # 2. Reflector bowl / bulb inside
    bulb_x = center_x + (-0.04 if is_right else 0.04)
    bulb = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.055, radius2=0.03, depth=0.04)
    rot_b = Matrix.Rotation(-math.pi / 2, 4, 'X')
    for v in bulb["verts"]:
        v.co = rot_b @ v.co + Vector((bulb_x, center_y - 0.01, center_z))

    # 3. Amber indicator on outer corner
    amber_x = center_x + (0.09 if is_right else -0.09)
    ind = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.06, 0.05, 0.14), verts=ind["verts"])
    bmesh.ops.translate(bm, vec=(amber_x, center_y - 0.01, center_z), verts=ind["verts"])

    me = bpy.data.meshes.new(name_prefix)
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new(name_prefix, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(glass_mat)
    obj.data.materials.append(core_mat)
    obj.data.materials.append(amber_mat)

    for poly in obj.data.polygons:
        if (is_right and poly.center.x > center_x + 0.06) or (not is_right and poly.center.x < center_x - 0.06):
            poly.material_index = 2
        elif abs(poly.center.x - bulb_x) < 0.06:
            poly.material_index = 1
        else:
            poly.material_index = 0

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.008
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj

def build_headlights():
    objs = []
    # Headlights_0: right side (+X), Headlights_1: left side (-X)
    objs.append(create_headlight_mesh("Headlights_0", 0.720, -2.320, 0.740, is_right=True))
    objs.append(create_headlight_mesh("Headlights_1", -0.720, -2.320, 0.740, is_right=False))
    return objs
