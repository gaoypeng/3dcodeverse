"""Boiler — cylindrical steam boiler and steam dome."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_gloss_dark_green, mat_polished_brass

random.seed(0)

# Plan numbers:
# center (0.000, -0.180, 0.155) extents (0.130, 0.320, 0.130)
# x in [-0.065, 0.065], y in [-0.340, -0.020], z in [0.090, 0.220]
# Boiler cylinder touches chassis at z = 0.090 (chassis top is at z = 0.090)

def build_boiler() -> bpy.types.Object:
    bm = bmesh.new()

    # The boiler is a horizontal cylinder running along Y axis.
    # Radius = 0.065 (diameter 0.130, so X in [-0.065, 0.065], Z in [0.090, 0.220], center Z = 0.155)
    # Length Y = 0.320, from Y = -0.340 to Y = -0.020 (center Y = -0.180).
    
    # Create cylinder along Z first, then rotate to align with Y
    # Radius = 0.065, length = 0.300 (leaving 0.020 for rounded front boiler cap/smokebox door)
    cap_thick = 0.020
    body_len = 0.320 - cap_thick # 0.300
    body_center_y = -0.020 - body_len / 2.0 # -0.170

    bm_cyl = bmesh.new()
    bmesh.ops.create_cone(
        bm_cyl,
        cap_ends=True,
        segments=32,
        radius1=0.065,
        radius2=0.065,
        depth=body_len
    )
    # Rotate 90 deg around X axis so cylinder runs along Y
    bmesh.ops.rotate(bm_cyl, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'X'), verts=bm_cyl.verts)
    bmesh.ops.translate(bm_cyl, vec=(0.0, body_center_y, 0.155), verts=bm_cyl.verts)
    
    # Front rounded smokebox cap (half-sphere or bevelled disc) at Y = -0.320 to -0.340
    bm_cap = bmesh.new()
    bmesh.ops.create_cone(
        bm_cap,
        cap_ends=True,
        segments=32,
        radius1=0.065,
        radius2=0.050,
        depth=cap_thick
    )
    bmesh.ops.rotate(bm_cap, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'X'), verts=bm_cap.verts)
    bmesh.ops.translate(bm_cap, vec=(0.0, -0.320 - cap_thick/2.0, 0.155), verts=bm_cap.verts)

    # Steam dome / sand dome perched on top of boiler (Z up to 0.220, or within boiler bbox)
    # Dome located around Y = -0.100, radius 0.028, sitting atop the boiler
    bm_dome = bmesh.new()
    bmesh.ops.create_cone(
        bm_dome,
        cap_ends=True,
        segments=24,
        radius1=0.026,
        radius2=0.024,
        depth=0.035
    )
    # Put dome top at z = 0.220 (which matches BOILER_MAX Z = 0.220)
    bmesh.ops.translate(bm_dome, vec=(0.0, -0.120, 0.220 - 0.035/2.0), verts=bm_dome.verts)

    # Front headlight / lamp perched on front boiler top
    bm_light = bmesh.new()
    bmesh.ops.create_cone(
        bm_light,
        cap_ends=True,
        segments=16,
        radius1=0.012,
        radius2=0.014,
        depth=0.020
    )
    bmesh.ops.rotate(bm_light, cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'X'), verts=bm_light.verts)
    bmesh.ops.translate(bm_light, vec=(0.0, -0.330, 0.210), verts=bm_light.verts)

    # Merge meshes into bm
    for m in [bm_cyl, bm_cap, bm_dome, bm_light]:
        m.to_mesh(bpy.data.meshes.new("_tmp"))
        bm.from_mesh(bpy.data.meshes["_tmp"])
        bpy.data.meshes.remove(bpy.data.meshes["_tmp"])
        m.free()

    obj = obj_from_bmesh("Boiler", bm)
    obj.data.materials.append(mat_gloss_dark_green())
    return obj
