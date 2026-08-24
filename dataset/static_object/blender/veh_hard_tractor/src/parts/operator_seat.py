"""OperatorSeat — driver seating.

Contoured vinyl bucket seat with low wraparound backrest and cushioned base, mounted on a dual-spring suspension pedestal.
Material: textured black weatherproof vinyl on black steel base. Instances: 1.
Bbox: center (0.000, 0.450, 1.150) extents (0.480, 0.450, 0.450)
      x in [-0.240, 0.240]  y in [0.225, 0.675]  z in [0.925, 1.375]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

OPERATOR_SEAT_CENTER = (0.000, 0.450, 1.150)
OPERATOR_SEAT_EXTENTS = (0.480, 0.450, 0.450)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def add_box(bm, center, size, mat_index=0):
    verts = []
    hx, hy, hz = size[0]/2.0, size[1]/2.0, size[2]/2.0
    cx, cy, cz = center[0], center[1], center[2]
    for dx in [-hx, hx]:
        for dy in [-hy, hy]:
            for dz in [-hz, hz]:
                verts.append(bm.verts.new((cx + dx, cy + dy, cz + dz)))
    faces = [
        (verts[0], verts[2], verts[3], verts[1]), # -X
        (verts[4], verts[5], verts[7], verts[6]), # +X
        (verts[0], verts[1], verts[5], verts[4]), # -Y
        (verts[2], verts[6], verts[7], verts[3]), # +Y
        (verts[0], verts[4], verts[6], verts[2]), # -Z
        (verts[1], verts[3], verts[7], verts[5]), # +Z
    ]
    for f in faces:
        face = bm.faces.new(f)
        face.material_index = mat_index

def build_operator_seat():
    bm = bmesh.new()

    mat_vinyl = make_material("SeatVinylMat", (0.10, 0.10, 0.10), roughness=0.6, metallic=0.05)
    mat_steel = make_material("SeatSteelBaseMat", (0.16, 0.16, 0.18), roughness=0.4, metallic=0.85)
    mat_spring = make_material("SpringSteelMat", (0.35, 0.35, 0.38), roughness=0.3, metallic=0.9)

    # OperatorSeat exact bounds:
    # x in [-0.240, 0.240] (width 0.480)
    # y in [0.225, 0.675] (depth 0.450)
    # z in [0.925, 1.375] (height 0.450)

    # 1. Base pedestal plate (z starts at 0.925)
    add_box(bm, (0.0, 0.450, 0.940), (0.280, 0.320, 0.030), mat_index=1)

    # Coil springs
    for sign_x in [-1.0, 1.0]:
        x_sp = 0.090 * sign_x
        for k in range(4):
            z_coil = 0.960 + k * 0.025
            n_seg = 12
            for i in range(n_seg):
                ang1 = 2.0 * math.pi * i / n_seg
                ang2 = 2.0 * math.pi * (i + 1) / n_seg
                r_c = 0.032
                v1 = bm.verts.new((x_sp + r_c * math.cos(ang1), 0.450 + r_c * math.sin(ang1), z_coil - 0.009))
                v2 = bm.verts.new((x_sp + r_c * math.cos(ang2), 0.450 + r_c * math.sin(ang2), z_coil - 0.009))
                v3 = bm.verts.new((x_sp + r_c * math.cos(ang2), 0.450 + r_c * math.sin(ang2), z_coil + 0.009))
                v4 = bm.verts.new((x_sp + r_c * math.cos(ang1), 0.450 + r_c * math.sin(ang1), z_coil + 0.009))
                f = bm.faces.new((v1, v2, v3, v4))
                f.material_index = 2

    # Under-seat pan
    add_box(bm, (0.0, 0.450, 1.060), (0.360, 0.360, 0.035), mat_index=1)

    # 2. Cushion (y in [0.225, 0.605], z in [1.075, 1.155], x in [-0.220, 0.220])
    add_box(bm, (0.0, 0.415, 1.115), (0.440, 0.380, 0.080), mat_index=0)

    # 3. Backrest (y in [0.595, 0.675], z in [1.130, 1.375], x in [-0.240, 0.240])
    add_box(bm, (0.0, 0.635, 1.2525), (0.480, 0.080, 0.245), mat_index=0)

    # Side bolsters
    for sign_x in [-1.0, 1.0]:
        add_box(bm, (0.210 * sign_x, 0.500, 1.200), (0.060, 0.220, 0.160), mat_index=0)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("OperatorSeat")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("OperatorSeat", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_vinyl)
    obj.data.materials.append(mat_steel)
    obj.data.materials.append(mat_spring)

    return obj
