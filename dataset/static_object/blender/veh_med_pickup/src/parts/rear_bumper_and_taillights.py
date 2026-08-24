"""RearBumperAndTaillights — rear step bumper and vertical taillight clusters.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan:
# RearBumperAndTaillights center (0.000, 2.340, 0.680) extents (1.840, 0.160, 0.540)
# x in [-0.920, 0.920], y in [2.260, 2.420], z in [0.410, 0.950]
# Attaches to: CargoBed (Tailgate rear face is at y = 2.245; Bumper and taillights touch/overlap at y = 2.243..2.260)

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

def build_rear_bumper_and_taillights():
    steel_mat = make_material("RearBumper_Steel", (0.15, 0.15, 0.16), roughness=0.4, metallic=0.7)
    step_mat = make_material("RearBumper_StepPad", (0.05, 0.05, 0.05), roughness=0.9, metallic=0.0)
    tail_red = make_material("Taillight_Red", (0.85, 0.05, 0.05), roughness=0.1, metallic=0.1, emission=(0.85, 0.05, 0.05, 1.5))
    tail_white = make_material("Taillight_White", (0.9, 0.9, 0.9), roughness=0.1, metallic=0.1, emission=(0.9, 0.9, 0.9, 1.0))

    bm = bmesh.new()

    # 1. Rear Steel Step Bumper across bottom
    # z from 0.41 to 0.62 (center 0.515), y in [2.243, 2.403] (thickness 0.16, center y = 2.323), x in [-0.92, 0.92]
    # Reaches y = 2.243 to touch CargoBed tailgate (at y=2.245) and chassis frame end
    bmp = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.84, 0.16, 0.21), verts=bmp["verts"])
    bmesh.ops.translate(bm, vec=(0.0, 2.323, 0.515), verts=bmp["verts"])

    # 2. Step pad / License plate recess
    pad = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.60, 0.14, 0.06), verts=pad["verts"])
    bmesh.ops.translate(bm, vec=(0.0, 2.338, 0.58), verts=pad["verts"])

    # 3. Vertical Taillight Clusters at corners (Left & Right)
    # x at +-0.85 (width 0.14 -> from 0.78 to 0.92), y in [2.243, 2.383] (thickness 0.14, center y = 2.313), z from 0.65 to 0.95
    for tx in [-0.85, 0.85]:
        tl = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.14, 0.14, 0.30), verts=tl["verts"])
        bmesh.ops.translate(bm, vec=(tx, 2.313, 0.80), verts=tl["verts"])

    me = bpy.data.meshes.new("RearBumperAndTaillights")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("RearBumperAndTaillights", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(steel_mat)
    obj.data.materials.append(step_mat)
    obj.data.materials.append(tail_red)
    obj.data.materials.append(tail_white)

    for poly in obj.data.polygons:
        if poly.center.z > 0.64 and abs(poly.center.x) > 0.76:
            if poly.center.z < 0.74:
                poly.material_index = 3 # reverse white
            else:
                poly.material_index = 2 # brake red
        elif abs(poly.center.x) < 0.32 and poly.center.z > 0.55:
            poly.material_index = 1
        else:
            poly.material_index = 0

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.01
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj
