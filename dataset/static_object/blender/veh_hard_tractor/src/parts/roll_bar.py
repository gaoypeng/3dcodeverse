"""RollBar — roll-over protection structure (ROPS).

Inverted U-shaped heavy tubular steel roll bar arching behind and above the operator seat, anchored firmly to the rear axle / chassis horns.
Material: glossy red powder-coated structural steel tubing. Instances: 1.
Bbox: center (0.000, 0.680, 1.700) extents (1.050, 0.150, 1.300)
      x in [-0.525, 0.525]  y in [0.605, 0.755]  z in [1.050, 2.350]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

ROLL_BAR_CENTER = (0.000, 0.680, 1.700)
ROLL_BAR_EXTENTS = (1.050, 0.150, 1.300)

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
        (verts[0], verts[2], verts[3], verts[1]),
        (verts[4], verts[5], verts[7], verts[6]),
        (verts[0], verts[1], verts[5], verts[4]),
        (verts[2], verts[6], verts[7], verts[3]),
        (verts[0], verts[4], verts[6], verts[2]),
        (verts[1], verts[3], verts[7], verts[5]),
    ]
    for f in faces:
        face = bm.faces.new(f)
        face.material_index = mat_index

def build_roll_bar():
    bm = bmesh.new()

    mat_red = make_material("AgriRedMat", (0.80, 0.05, 0.05), roughness=0.25, metallic=0.1)
    mat_bracket = make_material("DarkChassisMat", (0.15, 0.15, 0.17), roughness=0.45, metallic=0.8)

    # RollBar exact bounds:
    # x in [-0.525, 0.525] (span 1.050)
    # y in [0.605, 0.755] (depth 0.150)
    # z in [1.050, 2.350] (height 1.300)
    yc = 0.680
    tube_r = 0.045
    
    path_points = []
    n_leg = 8
    # Bottom z starts at 1.095 so that with tube_r=0.045 / base plate z reaches 1.050
    for i in range(n_leg):
        z = 1.095 + (2.180 - 1.095) * i / (n_leg - 1)
        path_points.append(Vector((-0.480, yc, z)))
    
    n_corner = 6
    corner_r = 0.125
    center_l = Vector((-0.355, yc, 2.180))
    for i in range(1, n_corner):
        phi = math.pi - (math.pi / 2) * i / (n_corner - 1)
        px = center_l.x + corner_r * math.cos(phi)
        pz = center_l.z + corner_r * math.sin(phi)
        path_points.append(Vector((px, yc, pz)))

    center_r = Vector((0.355, yc, 2.180))
    for i in range(1, n_corner - 1):
        phi = (math.pi / 2) - (math.pi / 2) * i / (n_corner - 1)
        px = center_r.x + corner_r * math.cos(phi)
        pz = center_r.z + corner_r * math.sin(phi)
        path_points.append(Vector((px, yc, pz)))

    for i in range(n_leg):
        z = 2.180 - (2.180 - 1.095) * i / (n_leg - 1)
        path_points.append(Vector((0.480, yc, z)))

    n_tube_seg = 12
    thetas = [2.0 * math.pi * j / n_tube_seg for j in range(n_tube_seg)]
    rings = []

    for idx, pt in enumerate(path_points):
        if idx == 0:
            tan = (path_points[1] - pt).normalized()
        elif idx == len(path_points) - 1:
            tan = (pt - path_points[-2]).normalized()
        else:
            tan = (path_points[idx+1] - path_points[idx-1]).normalized()

        normal_y = Vector((0, 1, 0))
        binormal = tan.cross(normal_y).normalized()
        normal_y = binormal.cross(tan).normalized()

        ring = []
        for th in thetas:
            offset = (normal_y * math.cos(th) + binormal * math.sin(th)) * tube_r
            v = bm.verts.new(pt + offset)
            ring.append(v)
        rings.append(ring)

    for idx in range(len(rings) - 1):
        for j in range(n_tube_seg):
            next_j = (j + 1) % n_tube_seg
            v1 = rings[idx][j]
            v2 = rings[idx][next_j]
            v3 = rings[idx+1][next_j]
            v4 = rings[idx+1][j]
            f = bm.faces.new((v1, v2, v3, v4))
            f.material_index = 0

    f_bot_l = bm.faces.new(rings[0])
    f_bot_l.material_index = 0
    f_bot_r = bm.faces.new(reversed(rings[-1]))
    f_bot_r.material_index = 0

    # Mounting base plates (z in [1.050, 1.100], y in [0.605, 0.755])
    for sign_x in [-1.0, 1.0]:
        add_box(bm, (0.470 * sign_x, yc, 1.075), (0.110, 0.150, 0.050), mat_index=1)

    # Crossbar reinforcement gussets
    for sign_x in [-1.0, 1.0]:
        add_box(bm, (0.360 * sign_x, yc, 2.220), (0.040, 0.060, 0.040), mat_index=0)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("RollBar")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("RollBar", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_red)
    obj.data.materials.append(mat_bracket)

    return obj
