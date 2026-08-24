"""FrontWheel — steerable front wheel assemblies with treaded rubber tires and alloy rims.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan numbers:
# FrontWheel center (0.860, -1.450, 0.380) extents (0.260, 0.760, 0.760)
# x in [0.730, 0.990], y in [-1.830, -1.070], z in [0.000, 0.760]

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_wheel_mesh(name_prefix, center_x, center_y, center_z, is_right):
    tire_mat = make_material(f"{name_prefix}_TireMat", (0.12, 0.12, 0.13), roughness=0.85, metallic=0.0)
    rim_mat = make_material(f"{name_prefix}_RimMat", (0.80, 0.80, 0.82), roughness=0.25, metallic=0.9)
    hub_mat = make_material(f"{name_prefix}_HubMat", (0.1, 0.1, 0.1), roughness=0.4, metallic=0.5)

    bm = bmesh.new()

    tire_outer_r = 0.38
    tire_width = 0.24
    rim_r = 0.22
    hub_r = 0.08
    rot_x = Matrix.Rotation(math.pi / 2, 4, 'Y')

    # 1. Tire Main Tread & Sidewalls
    t_cyl = bmesh.ops.create_cone(bm, cap_ends=True, segments=36, radius1=tire_outer_r, radius2=tire_outer_r, depth=0.24)
    for v in t_cyl["verts"]:
        v.co = rot_x @ v.co

    # 2. Rim outer ring & deep dish
    rim_cyl = bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=rim_r, radius2=rim_r, depth=0.25)
    for v in rim_cyl["verts"]:
        v.co = rot_x @ v.co

    # 3. Hub cap
    hub_cyl = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=hub_r, radius2=hub_r, depth=0.26)
    for v in hub_cyl["verts"]:
        v.co = rot_x @ v.co

    # Position entire wheel at (center_x, center_y, center_z)
    bmesh.ops.translate(bm, vec=Vector((center_x, center_y, center_z)), verts=bm.verts)

    me = bpy.data.meshes.new(name_prefix)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name_prefix, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(tire_mat)
    obj.data.materials.append(rim_mat)
    obj.data.materials.append(hub_mat)

    for poly in obj.data.polygons:
        dy = poly.center.y - center_y
        dz = poly.center.z - center_z
        r_poly = math.sqrt(dy * dy + dz * dz)
        if r_poly < hub_r + 0.01:
            poly.material_index = 2
        elif r_poly < rim_r + 0.01:
            poly.material_index = 1
        else:
            poly.material_index = 0

    return obj

def build_front_wheel():
    objs = []
    # FrontWheel_0: right side (+X), FrontWheel_1: left side (-X)
    objs.append(create_wheel_mesh("FrontWheel_0", 0.860, -1.450, 0.380, is_right=True))
    objs.append(create_wheel_mesh("FrontWheel_1", -0.860, -1.450, 0.380, is_right=False))
    return objs
