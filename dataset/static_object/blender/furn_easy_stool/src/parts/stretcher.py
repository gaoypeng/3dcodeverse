"""Stretcher — horizontal rung bracing the legs (part module; imported by src/model.py).

Cylindrical dowel stretcher of diameter 18 mm spanning horizontally between adjacent legs at height z=0.150 m to provide rigid triangulation.
Material: natural light oak, smooth matte varnish.  Instances: 3 (radial).  Attaches to: Leg (must touch, no gap).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan numbers
DOWEL_R = 0.018 / 2.0  # 9 mm radius
Z_STRETCHER = 0.150
STRETCHER_INSTANCES = 3


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_stretcher_mesh(name: str, p1: Vector, p2: Vector, mat) -> bpy.types.Object:
    """Build a horizontal cylinder between p1 and p2."""
    axis = (p2 - p1).normalized()
    length = (p2 - p1).length
    center = (p1 + p2) / 2.0

    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=24,
        radius1=DOWEL_R,
        radius2=DOWEL_R,
        depth=length
    )

    rot_quat = Vector((0, 0, 1)).rotation_difference(axis)
    rot_mat = rot_quat.to_matrix().to_4x4()

    bmesh.ops.transform(bm, matrix=rot_mat, verts=bm.verts)
    bmesh.ops.translate(bm, vec=center, verts=bm.verts)

    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.shade_smooth()
    obj.data.materials.append(mat)
    return obj


def build_stretcher():
    """Build 3 stretchers connecting adjacent legs."""
    mat = make_material("StretcherWood", (0.68, 0.50, 0.32), roughness=0.45, metallic=0.0)
    objs = []

    r_leg = 0.169 + (0.107 - 0.169) * (Z_STRETCHER / 0.420)
    leg_r_at_z = 0.012 + (0.016 - 0.012) * (Z_STRETCHER / 0.420)

    base_angles = [-math.pi / 2.0, math.pi / 6.0, 5.0 * math.pi / 6.0]

    leg_pos = [
        Vector((r_leg * math.cos(a), r_leg * math.sin(a), Z_STRETCHER))
        for a in base_angles
    ]

    pairs = [
        (1, 2),
        (2, 0),
        (0, 1)
    ]

    for idx, (i, j) in enumerate(pairs):
        p_i = leg_pos[i]
        p_j = leg_pos[j]
        direction = (p_j - p_i).normalized()

        p_start = p_i + direction * (leg_r_at_z - 0.0015)
        p_end = p_j - direction * (leg_r_at_z - 0.0015)

        obj = build_stretcher_mesh(f"Stretcher_{idx}", p_start, p_end, mat)
        objs.append(obj)

    return objs
