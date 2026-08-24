"""BottomHead — lower resonant drumhead membrane."""
import bpy
import bmesh
import math
from parts._common import make_material, obj_from_bmesh

# Plan numbers: center (0, 0, 0.015), extents (0.356, 0.356, 0.002) -> z in [0.014, 0.016]
# Clear resonant membrane

def build_bottom_head() -> bpy.types.Object:
    mat = make_material("BottomHeadMat", (0.75, 0.80, 0.85), roughness=0.2, metallic=0.1)
    bm = bmesh.new()
    
    r = 0.178
    depth = 0.002
    segments = 64
    
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=segments,
        radius1=r,
        radius2=r,
        depth=depth
    )
    
    obj = obj_from_bmesh("BottomHead", bm, location=(0.0, 0.0, 0.015), material=mat)
    return obj
