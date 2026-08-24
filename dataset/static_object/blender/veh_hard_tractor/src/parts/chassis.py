"""Chassis — structural backbone and drivetrain housing.

Heavy cast-iron transmission and engine block belly pan connecting front and rear axle mountings, featuring lower steps and foot platform plating for the operator.
Material: dark charcoal cast iron with oily sheen. Instances: 1.
Bbox: center (0.000, 0.000, 0.550) extents (0.650, 2.200, 0.450)
      x in [-0.325, 0.325]  y in [-1.100, 1.100]  z in [0.325, 0.775]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

CHASSIS_CENTER = (0.000, 0.000, 0.550)
CHASSIS_EXTENTS = (0.650, 2.200, 0.450)

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

def build_chassis():
    bm = bmesh.new()
    
    mat_iron = make_material("DarkChassisMat", (0.16, 0.16, 0.18), roughness=0.45, metallic=0.8)
    mat_metal_plate = make_material("FootplateMat", (0.22, 0.22, 0.24), roughness=0.55, metallic=0.7)
    mat_engine = make_material("EngineBlockMat", (0.12, 0.12, 0.14), roughness=0.4, metallic=0.85)

    # Chassis strict bounds:
    # x in [-0.325, 0.325] (extents 0.650)
    # y in [-1.100, 1.100] (extents 2.200)
    # z in [0.325, 0.775] (extents 0.450)

    # 1. Central Transmission & Engine Spine (main belly casting)
    # x in [-0.220, 0.220], y in [-1.050, 1.050], z in [0.340, 0.775]
    add_box(bm, (0.0, 0.0, 0.5575), (0.440, 2.100, 0.435), mat_index=0)

    # 2. Front Axle Beam
    # x in [-0.325, 0.325], y in [-1.100, -0.980], z in [0.325, 0.425]
    add_box(bm, (0.0, -1.040, 0.375), (0.650, 0.120, 0.100), mat_index=0)

    # 3. Rear Axle Trumpet Housings (extending to x = ±0.325 at y = 0.700, z in [0.620, 0.775])
    add_box(bm, (0.0, 0.700, 0.6975), (0.650, 0.180, 0.155), mat_index=0)

    # 4. Operator Foot Platforms / Floor Plates (left and right)
    # x in [-0.325, -0.180] and [0.180, 0.325], y in [-0.08, 0.64], z in [0.51, 0.54]
    for sign_x in [-1.0, 1.0]:
        add_box(bm, (0.2525 * sign_x, 0.28, 0.525), (0.145, 0.720, 0.030), mat_index=1)
        # Lower step plate
        add_box(bm, (0.265 * sign_x, 0.20, 0.380), (0.120, 0.240, 0.025), mat_index=1)

    # 5. Engine Block Details under hood
    add_box(bm, (0.0, -0.60, 0.655), (0.400, 0.700, 0.240), mat_index=2)

    # Starter motor (cylinder on left side)
    n_seg = 12
    for i in range(n_seg):
        ang1 = 2.0 * math.pi * i / n_seg
        ang2 = 2.0 * math.pi * (i + 1) / n_seg
        r_s = 0.045
        v1 = bm.verts.new((-0.23 + r_s * math.cos(ang1), -0.42, 0.52 + r_s * math.sin(ang1)))
        v2 = bm.verts.new((-0.23 + r_s * math.cos(ang2), -0.42, 0.52 + r_s * math.sin(ang2)))
        v3 = bm.verts.new((-0.23 + r_s * math.cos(ang2), -0.28, 0.52 + r_s * math.sin(ang2)))
        v4 = bm.verts.new((-0.23 + r_s * math.cos(ang1), -0.28, 0.52 + r_s * math.sin(ang1)))
        f = bm.faces.new((v1, v2, v3, v4))
        f.material_index = 2

    # Rear Drawbar hitch at rear (y in [0.90, 1.10], z in [0.35, 0.45], x in [-0.08, 0.08])
    add_box(bm, (0.0, 1.000, 0.400), (0.160, 0.200, 0.050), mat_index=0)

    # Ensure exact outer vertex bounds exist so bbox measurement is precise:
    # Corner markers to guarantee [-0.325, 0.325] x [-1.100, 1.100] x [0.325, 0.775]
    # The front axle touches x=±0.325 and z=0.325, y=-1.100.
    # Rear hitch touches y=+1.100. Spine touches z=0.775.

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("Chassis")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("Chassis", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_iron)
    obj.data.materials.append(mat_metal_plate)
    obj.data.materials.append(mat_engine)

    return obj
