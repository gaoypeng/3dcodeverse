"""ConnectingRod — horizontal driving linkage rod connecting wheel hubs."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_chrome_steel

random.seed(0)

# Plan numbers:
# center (0.095, -0.140, 0.050) extents (0.010, 0.280, 0.015)
# x in [0.090, 0.100], y in [-0.280, 0.000], z in [0.043, 0.058]
# Instances: 2 (mirror_x: ConnectingRod_0 on right side +0.095, ConnectingRod_1 on left side -0.095)

def make_single_rod(name: str, x_pos: float) -> bpy.types.Object:
    bm = bmesh.new()

    # Main horizontal bar spanning the full Y length:
    # Extents: X = 0.008 (x in [x_pos - 0.004, x_pos + 0.004])
    # Y = 0.280 (y in [-0.280, 0.000], center Y = -0.140)
    # Z = 0.012 (z in [0.044, 0.056], center Z = 0.050)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.006, 0.280, 0.012), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(x_pos, -0.140, 0.050), verts=bm.verts)

    # 3 circular joint boss rings at wheel positions: Y = -0.250, -0.140, -0.030
    for y_eye in [-0.250, -0.140, -0.030]:
        bm_eye = bmesh.new()
        bmesh.ops.create_cone(
            bm_eye, cap_ends=True, segments=24,
            radius1=0.0075, radius2=0.0075, depth=0.010
        )
        bmesh.ops.rotate(bm_eye, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_eye.verts)
        bmesh.ops.translate(bm_eye, vec=(x_pos, y_eye, 0.050), verts=bm_eye.verts)

        # Pin hub connecting directly to the wheel crank pin at x = +/-0.088
        bm_pin = bmesh.new()
        bmesh.ops.create_cone(
            bm_pin, cap_ends=True, segments=16,
            radius1=0.005, radius2=0.005, depth=0.010
        )
        bmesh.ops.rotate(bm_pin, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_pin.verts)
        pin_offset_x = -0.005 if x_pos > 0 else 0.005
        bmesh.ops.translate(bm_pin, vec=(x_pos + pin_offset_x, y_eye, 0.050), verts=bm_pin.verts)

        for m in [bm_eye, bm_pin]:
            m_mesh = bpy.data.meshes.new("_tmp_eye")
            m.to_mesh(m_mesh)
            bm.from_mesh(m_mesh)
            bpy.data.meshes.remove(m_mesh)
            m.free()

    obj = obj_from_bmesh(name, bm)
    obj.data.materials.append(mat_chrome_steel())
    return obj

def build_connecting_rod() -> list[bpy.types.Object]:
    rod0 = make_single_rod("ConnectingRod_0", 0.095)
    rod1 = make_single_rod("ConnectingRod_1", -0.095)
    return [rod0, rod1]
