"""LocomotiveChassis — main structural frame of the locomotive engine."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_satin_dark_red

random.seed(0)

# Plan numbers:
# center (0.000, -0.140, 0.075) extents (0.150, 0.480, 0.030)
# x in [-0.075, 0.075], y in [-0.380, 0.100], z in [0.060, 0.090]

def build_locomotive_chassis() -> bpy.types.Object:
    bm = bmesh.new()

    # Main rectangular wooden/metal undercarriage frame
    # Extents: X: 0.150 (-0.075 to 0.075), Y: 0.480 (-0.380 to 0.100), Z: 0.030 (0.060 to 0.090)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.150, 0.480, 0.030), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.0, -0.140, 0.075), verts=bm.verts)

    # Bevel horizontal edges for toy finish
    edges_to_bevel = [e for e in bm.edges]
    bmesh.ops.bevel(bm, geom=edges_to_bevel, offset=0.002, segments=2, profile=0.5, affect='EDGES')

    # Front bumper block / coupler mount (slight visual detail within bbox)
    # Rear hitch tongue extending towards tender (Y=0.100)
    
    obj = obj_from_bmesh("LocomotiveChassis", bm)
    obj.data.materials.append(mat_satin_dark_red())
    return obj
