"""FrontGrilleAndBumper — front radiator grille, badge, and heavy-duty front bumper.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan:
# FrontGrilleAndBumper center (0.000, -2.340, 0.620) extents (1.820, 0.160, 0.520)
# x in [-0.910, 0.910], y in [-2.420, -2.260], z in [0.360, 0.880]
# Attaches to: ChassisAndLowerBody (Chassis front is at y = -2.260; Grille extends to y = -2.258 for 2mm weld)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_front_grille_and_bumper():
    bumper_mat = make_material("FrontBumper_Steel", (0.15, 0.15, 0.16), roughness=0.4, metallic=0.7)
    grille_mat = make_material("FrontGrille_Dark", (0.05, 0.05, 0.05), roughness=0.7, metallic=0.2)
    chrome_mat = make_material("FrontChrome_Trim", (0.90, 0.90, 0.92), roughness=0.15, metallic=0.95)

    bm = bmesh.new()

    # 1. Heavy-duty Front Bumper across bottom
    # z from 0.36 to 0.58, y in [-2.418, -2.258] (thickness 0.16, center y = -2.338), x in [-0.91, 0.91]
    bmp = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.82, 0.16, 0.22), verts=bmp["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -2.338, 0.47), verts=bmp["verts"])

    # 2. Bumper lower skid guard / push bar center section
    skid = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.80, 0.08, 0.08), verts=skid["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -2.38, 0.40), verts=skid["verts"])

    # 3. Grille Surround Frame & Headlight Mounting Backing
    # z from 0.58 to 0.88 (height 0.30, center 0.73)
    # y in [-2.378, -2.258] (thickness 0.12, center y = -2.318)
    # Full width: x in [-0.91, 0.91] (covers behind headlights at x=+-0.72)
    # To weld with Headlights (whose rear is at y = -2.280), the grille backing at headlight areas extends to y = -2.282
    grille_frame = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.80, 0.12, 0.30), verts=grille_frame["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -2.318, 0.73), verts=grille_frame["verts"])

    # Headlight mounting pads extending forward to touch headlights back (at y = -2.280 -> reaches -2.282)
    for hx in [-0.72, 0.72]:
        pad = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.28, 0.04, 0.18), verts=pad["verts"])
        bmesh.ops.translate(bm, vec=(hx, -2.30, 0.74), verts=pad["verts"])

    # 4. Horizontal Grille Slats (3 prominent chrome/matte slats across center)
    for slat_z in [0.65, 0.73, 0.81]:
        slat = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(1.05, 0.03, 0.03), verts=slat["verts"])
        bmesh.ops.translate(bm, vec=(0.0, -2.385, slat_z), verts=slat["verts"])

    # 5. Center Brand Emblem / Badge
    badge = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.18, 0.03, 0.07), verts=badge["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -2.395, 0.73), verts=badge["verts"])

    me = bpy.data.meshes.new("FrontGrilleAndBumper")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("FrontGrilleAndBumper", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(bumper_mat)
    obj.data.materials.append(grille_mat)
    obj.data.materials.append(chrome_mat)

    # Assign materials
    for poly in obj.data.polygons:
        if poly.center.z < 0.58:
            poly.material_index = 0
        elif abs(poly.center.y - (-2.385)) < 0.02 or abs(poly.center.y - (-2.395)) < 0.02:
            poly.material_index = 2
        else:
            poly.material_index = 1

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.01
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj
