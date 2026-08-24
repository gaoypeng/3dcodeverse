"""TenderWheel — smaller flanged wheels for tender wagon (4 wheels total)."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_cast_iron

random.seed(0)

# Plan numbers:
# center (0.075, 0.285, 0.035) extents (0.020, 0.070, 0.070)
# 4 wheels total: 2 on right (+X=0.075), 2 on left (-X=-0.075)
# Y positions: 0.215, 0.355 (center 0.285, span 0.140)
# Wheel diameter: 0.070 -> radius = 0.035. Center Z = 0.035 (bottom at z=0.000, top at z=0.070)
# Instances named: TenderWheel_0 .. TenderWheel_3

def make_single_tender_wheel(name: str, center: tuple, side_sign: float) -> bpy.types.Object:
    bm = bmesh.new()

    # Axis along X
    # Wheel radius = 0.035, thickness = 0.016
    bm_tread = bmesh.new()
    bmesh.ops.create_cone(
        bm_tread, cap_ends=True, segments=28,
        radius1=0.035, radius2=0.035, depth=0.014
    )
    bmesh.ops.rotate(bm_tread, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_tread.verts)

    # Flange on inner side
    flange_x = -side_sign * 0.006
    bm_flange = bmesh.new()
    bmesh.ops.create_cone(
        bm_flange, cap_ends=True, segments=28,
        radius1=0.035, radius2=0.035, depth=0.004
    )
    bmesh.ops.rotate(bm_flange, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_flange.verts)
    bmesh.ops.translate(bm_flange, vec=(flange_x, 0, 0), verts=bm_flange.verts)

    # Center hub disc / axle stub reaching inward
    bm_hub = bmesh.new()
    bmesh.ops.create_cone(
        bm_hub, cap_ends=True, segments=16,
        radius1=0.012, radius2=0.012, depth=0.018
    )
    bmesh.ops.rotate(bm_hub, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_hub.verts)

    # Merge parts
    for m in [bm_tread, bm_flange, bm_hub]:
        m_mesh = bpy.data.meshes.new("_tmp_tw")
        m.to_mesh(m_mesh)
        bm.from_mesh(m_mesh)
        bpy.data.meshes.remove(m_mesh)
        m.free()

    bmesh.ops.translate(bm, vec=center, verts=bm.verts)

    obj = obj_from_bmesh(name, bm)
    obj.data.materials.append(mat_cast_iron())
    return obj

def build_tender_wheel() -> list[bpy.types.Object]:
    # 4 wheels:
    # Right: (0.075, 0.215, 0.035), (0.075, 0.355, 0.035)
    # Left:  (-0.075, 0.215, 0.035), (-0.075, 0.355, 0.035)
    wheels = []
    # 0, 1 right side
    wheels.append(make_single_tender_wheel("TenderWheel_0", (0.075, 0.215, 0.035), side_sign=1.0))
    wheels.append(make_single_tender_wheel("TenderWheel_1", (0.075, 0.355, 0.035), side_sign=1.0))
    # 2, 3 left side
    wheels.append(make_single_tender_wheel("TenderWheel_2", (-0.075, 0.215, 0.035), side_sign=-1.0))
    wheels.append(make_single_tender_wheel("TenderWheel_3", (-0.075, 0.355, 0.035), side_sign=-1.0))
    return wheels
