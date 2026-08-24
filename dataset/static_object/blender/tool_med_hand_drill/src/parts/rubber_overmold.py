"""RubberOvermold — Textured rubber grip surface on handle and rear housing.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, 0.042, 0.130) extents (0.050, 0.055, 0.140)
# x in [-0.025, 0.025]
# y in [0.0145, 0.0695]
# z in [0.060, 0.200]

def make_material(name, rgb, roughness=0.85, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_rubber_overmold():
    mat = make_material("BlackRubberMat", (0.05, 0.05, 0.05), roughness=0.85, metallic=0.0)

    bm = bmesh.new()

    # 1. Main Grip Wrap:
    # A single continuous, beautifully ergonomic wrap around the handle from z=0.060 to z=0.166
    # Target plan center: y=0.042, z=0.130
    grip_profile = [
        # (z, yc, rx, ry, theta_min, theta_max)
        (0.060, 0.040, 0.0225, 0.0200, -0.15 * math.pi, 1.15 * math.pi),
        (0.075, 0.038, 0.0210, 0.0190, -0.15 * math.pi, 1.15 * math.pi),
        (0.095, 0.034, 0.0195, 0.0185, -0.15 * math.pi, 1.15 * math.pi),
        (0.115, 0.030, 0.0200, 0.0190, -0.15 * math.pi, 1.15 * math.pi),
        (0.135, 0.024, 0.0210, 0.0195, -0.15 * math.pi, 1.15 * math.pi),
        (0.150, 0.018, 0.0220, 0.0200, -0.15 * math.pi, 1.15 * math.pi),
        (0.165, 0.012, 0.0225, 0.0205, -0.15 * math.pi, 1.15 * math.pi),
    ]

    n_theta = 16
    t_in = -0.0006  # 0.6 mm embed into pistol grip for solid weld
    t_out = 0.0020  # 2.0 mm thickness

    slice_loops = []
    for z, yc, rx, ry, th_min, th_max in grip_profile:
        inner_verts = []
        outer_verts = []
        for i in range(n_theta):
            t = th_min + (th_max - th_min) * (i / (n_theta - 1))
            cos_t = math.cos(t)
            sin_t = math.sin(t)
            px_i = (rx + t_in) * cos_t
            py_i = yc + (ry + t_in) * sin_t
            inner_verts.append(bm.verts.new((px_i, py_i, z)))
            px_o = (rx + t_out) * cos_t
            py_o = yc + (ry + t_out) * sin_t
            outer_verts.append(bm.verts.new((px_o, py_o, z)))
        
        loop = outer_verts + list(reversed(inner_verts))
        slice_loops.append(loop)

    n_loop = len(slice_loops[0])
    for s in range(len(slice_loops) - 1):
        l1 = slice_loops[s]
        l2 = slice_loops[s + 1]
        for i in range(n_loop):
            i_next = (i + 1) % n_loop
            bm.faces.new([l1[i], l1[i_next], l2[i_next], l2[i]])

    bm.faces.new([slice_loops[0][i] for i in reversed(range(n_loop))])
    bm.faces.new([slice_loops[-1][i] for i in range(n_loop)])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("RubberOvermold")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("RubberOvermold", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)

    return obj
