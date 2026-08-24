"""TopHead — upper batter drumhead membrane."""
import bpy
import bmesh
import math
from parts._common import make_material, obj_from_bmesh

# Plan numbers: center (0, 0, 0.155), extents (0.356, 0.356, 0.003) -> z in [0.1535, 0.1565]
# Coated white mylar drumhead

def build_top_head() -> bpy.types.Object:
    mat = make_material("TopHeadMat", (0.92, 0.91, 0.89), roughness=0.55, metallic=0.0)
    bm = bmesh.new()
    
    r = 0.178
    depth = 0.003
    segments = 64
    
    # Flat disc / thin cylinder
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=segments,
        radius1=r,
        radius2=r,
        depth=depth
    )
    
    obj = obj_from_bmesh("TopHead", bm, location=(0.0, 0.0, 0.155), material=mat)
    return obj
