"""CargoBed — open rear cargo box with side walls, bulkhead, and tailgate.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan:
# CargoBed center (0.000, 1.320, 0.960) extents (1.840, 1.850, 0.580)
# x in [-0.920, 0.920], y in [0.395, 2.245], z in [0.670, 1.250]
# Attaches to: ChassisAndLowerBody

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_cargo_bed():
    paint_mat = make_material("Bed_Paint_Green", (0.08, 0.22, 0.12), roughness=0.25, metallic=0.1)
    bedliner_mat = make_material("Bed_Bedliner_Black", (0.04, 0.04, 0.04), roughness=0.85, metallic=0.0)

    bm = bmesh.new()

    # Bed floor:
    # y from 0.395 to 2.245 (length 1.85), x from -0.88 to 0.88, z from 0.670 to 0.730 (thickness 0.06)
    floor = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.76, 1.85, 0.06), verts=floor["verts"])
    bmesh.ops.translate(bm, vec=(0.0, 1.32, 0.70), verts=floor["verts"])

    # Front Bulkhead (wall against cab):
    # y = 0.435, x in [-0.92, 0.92], z in [0.67, 1.25]
    fwall = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.84, 0.08, 0.58), verts=fwall["verts"])
    bmesh.ops.translate(bm, vec=(0.0, 0.435, 0.96), verts=fwall["verts"])

    # Rear Tailgate:
    # y = 2.205, x in [-0.92, 0.92], z in [0.67, 1.25]
    rwall = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.84, 0.08, 0.58), verts=rwall["verts"])
    bmesh.ops.translate(bm, vec=(0.0, 2.205, 0.96), verts=rwall["verts"])

    # Left & Right Sidewalls with Bed Rails:
    # x = +-0.88 (thickness 0.08 -> x reaches +-0.92), y from 0.395 to 2.245, z in [0.67, 1.25]
    for sx in [-0.88, 0.88]:
        swall = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.08, 1.85, 0.58), verts=swall["verts"])
        bmesh.ops.translate(bm, vec=(sx, 1.32, 0.96), verts=swall["verts"])

    # Inner wheel well tubs inside bed at y = 1.45, x = +-0.70, z = 0.84
    for tx in [-0.70, 0.70]:
        tub = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.28, 0.85, 0.26), verts=tub["verts"])
        bmesh.ops.translate(bm, vec=(tx, 1.45, 0.84), verts=tub["verts"])

    # Bed floor corrugations (ribs along Y)
    for rx in [-0.5, -0.3, -0.1, 0.1, 0.3, 0.5]:
        rib = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.04, 1.70, 0.02), verts=rib["verts"])
        bmesh.ops.translate(bm, vec=(rx, 1.32, 0.74), verts=rib["verts"])

    me = bpy.data.meshes.new("CargoBed")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("CargoBed", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(paint_mat)
    obj.data.materials.append(bedliner_mat)

    # Inner surfaces get bedliner material
    for poly in obj.data.polygons:
        if abs(poly.center.x) < 0.84 and poly.center.z < 1.20 and poly.center.y > 0.48 and poly.center.y < 2.16:
            poly.material_index = 1
        else:
            poly.material_index = 0

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.012
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj
