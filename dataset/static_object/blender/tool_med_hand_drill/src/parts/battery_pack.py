"""BatteryPack — Detachable rechargeable battery base.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, 0.015, 0.025) extents (0.080, 0.130, 0.050)
# x in [-0.040, 0.040] (width = 0.080)
# y in [-0.050, 0.080] (length = 0.130)
# z in [0.000, 0.050]  (height = 0.050)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_battery_pack():
    mat_dark = make_material("BatteryCharcoal", (0.08, 0.08, 0.09), roughness=0.6, metallic=0.0)

    bm = bmesh.new()

    # Create one single unified solid mesh for the battery via stacked loft rings
    # z from 0.000 to 0.050
    # Ring 0: z=0.000 (ground footprint), x: [-0.040, 0.040], y: [-0.050, 0.080]
    # Ring 1: z=0.038 (main body shoulder), x: [-0.040, 0.040], y: [-0.050, 0.080]
    # Ring 2: z=0.040 (inward step), x: [-0.027, 0.027], y: [-0.015, 0.065]
    # Ring 3: z=0.050 (top rail tower), x: [-0.027, 0.027], y: [-0.015, 0.065]

    # Let's create a clean 2-step single solid mesh:
    # 1. Main block:
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = v.co.x * 0.080
        v.co.y = 0.015 + v.co.y * 0.130
        v.co.z = 0.019 + v.co.z * 0.038

    # 2. Top tower
    bm_top = bmesh.new()
    bmesh.ops.create_cube(bm_top, size=1.0)
    for v in bm_top.verts:
        v.co.x = v.co.x * 0.054
        v.co.y = 0.025 + v.co.y * 0.080
        v.co.z = 0.044 + v.co.z * 0.012

    offset = len(bm.verts)
    for v in bm_top.verts:
        bm.verts.new(v.co)
    bm.verts.ensure_lookup_table()
    for f in bm_top.faces:
        bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
    bm_top.free()

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("BatteryPack")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("BatteryPack", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat_dark)

    return obj
