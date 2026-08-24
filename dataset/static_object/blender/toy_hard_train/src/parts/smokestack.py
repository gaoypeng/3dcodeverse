"""Smokestack — vertical exhaust chimney with flared rim."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_polished_brass

random.seed(0)

# Plan numbers:
# center (0.000, -0.290, 0.235) extents (0.050, 0.050, 0.090)
# x in [-0.025, 0.025], y in [-0.315, -0.265], z in [0.190, 0.280]

def build_smokestack() -> bpy.types.Object:
    bm = bmesh.new()

    # Base collar sitting on top of boiler:
    # Starts at z = 0.219 (1 mm overlap with boiler top at 0.220) up to z = 0.230
    bm_base = bmesh.new()
    bmesh.ops.create_cone(
        bm_base, cap_ends=True, segments=24,
        radius1=0.024, radius2=0.021, depth=0.016
    )
    bmesh.ops.translate(bm_base, vec=(0.0, -0.290, 0.226), verts=bm_base.verts)

    # Main pipe column: z = 0.234 to 0.265 (depth 0.031)
    bm_pipe = bmesh.new()
    bmesh.ops.create_cone(
        bm_pipe, cap_ends=True, segments=24,
        radius1=0.018, radius2=0.020, depth=0.031
    )
    bmesh.ops.translate(bm_pipe, vec=(0.0, -0.290, 0.2495), verts=bm_pipe.verts)

    # Flared crown rim: z = 0.265 to 0.280 (depth 0.015, radius up to 0.025)
    bm_rim = bmesh.new()
    bmesh.ops.create_cone(
        bm_rim, cap_ends=True, segments=24,
        radius1=0.020, radius2=0.025, depth=0.015
    )
    bmesh.ops.translate(bm_rim, vec=(0.0, -0.290, 0.2725), verts=bm_rim.verts)

    for m in [bm_base, bm_pipe, bm_rim]:
        m_mesh = bpy.data.meshes.new("_tmp_sm")
        m.to_mesh(m_mesh)
        bm.from_mesh(m_mesh)
        bpy.data.meshes.remove(m_mesh)
        m.free()

    obj = obj_from_bmesh("Smokestack", bm)
    obj.data.materials.append(mat_polished_brass())
    return obj
