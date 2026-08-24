"""Leg — supporting splayed leg (part module; imported by src/model.py).

Turned round wooden leg tapering from 32 mm diameter at the top to 24 mm at the floor, angled outward by approximately 8 degrees. Top penetrates 5 mm into seat underside; bottom cut flush to ground plane.
Material: natural light oak, smooth matte varnish.  Instances: 3 (radial).  Attaches to: Seat (must touch, no gap).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan numbers
LEG_TOP_R = 0.032 / 2.0   # 16 mm radius
LEG_BOT_R = 0.024 / 2.0   # 12 mm radius

Z_TOP = 0.420
Z_BOT = 0.000
R_TOP = 0.107
R_BOT = 0.169

LEG_INSTANCES = 3


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_leg_mesh(name: str, angle_rad: float, mat) -> bpy.types.Object:
    """Construct one tapered leg angled outward, bottom cut flush to z=0, top cut flush to z=0.420."""
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    p_bot = Vector((R_BOT * cos_a, R_BOT * sin_a, Z_BOT))
    p_top = Vector((R_TOP * cos_a, R_TOP * sin_a, Z_TOP))

    axis = (p_top - p_bot).normalized()
    raw_length = (p_top - p_bot).length

    bm = bmesh.new()
    extra = 0.02
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=32,
        radius1=LEG_BOT_R - 0.0005,
        radius2=LEG_TOP_R + 0.0005,
        depth=raw_length + 2 * extra
    )

    center = (p_bot + p_top) / 2.0
    rot_quat = Vector((0, 0, 1)).rotation_difference(axis)
    rot_mat = rot_quat.to_matrix().to_4x4()

    bmesh.ops.transform(bm, matrix=rot_mat, verts=bm.verts)
    bmesh.ops.translate(bm, vec=center, verts=bm.verts)

    # Bisect at z=0 (clear below 0)
    bmesh.ops.bisect_plane(
        bm,
        geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
        plane_co=(0, 0, 0.0),
        plane_no=(0, 0, 1.0),
        clear_inner=True,
        clear_outer=False
    )
    # Bisect at z=0.420 (clear above 0.420)
    bmesh.ops.bisect_plane(
        bm,
        geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
        plane_co=(0, 0, Z_TOP),
        plane_no=(0, 0, 1.0),
        clear_inner=False,
        clear_outer=True
    )

    # Cap holes
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary])

    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.shade_smooth()
    obj.data.materials.append(mat)
    return obj


def build_leg():
    """Build 3 radial legs Leg_0, Leg_1, Leg_2."""
    mat = make_material("LegWood", (0.68, 0.50, 0.32), roughness=0.45, metallic=0.0)
    objs = []
    base_angles = [-math.pi / 2.0, math.pi / 6.0, 5.0 * math.pi / 6.0]

    for i, angle in enumerate(base_angles):
        obj = build_leg_mesh(f"Leg_{i}", angle, mat)
        objs.append(obj)

    return objs
