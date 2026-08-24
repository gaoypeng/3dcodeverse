"""TenderChassis — coal wagon body hitched behind locomotive."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_gloss_dark_green

random.seed(0)

# Plan numbers:
# center (0.000, 0.285, 0.110) extents (0.150, 0.250, 0.140)
# x in [-0.075, 0.075], y in [0.160, 0.410], z in [0.040, 0.180]

def build_tender_chassis() -> bpy.types.Object:
    bm = bmesh.new()

    def add_box(center, size):
        bm_b = bmesh.new()
        bmesh.ops.create_cube(bm_b, size=1.0)
        bmesh.ops.scale(bm_b, vec=size, verts=bm_b.verts)
        bmesh.ops.translate(bm_b, vec=center, verts=bm_b.verts)
        m = bpy.data.meshes.new("_tmp_p")
        bm_b.to_mesh(m)
        bm.from_mesh(m)
        bpy.data.meshes.remove(m)
        bm_b.free()

    # 1. Under-chassis subframe & hitch:
    # Frame base: X in [-0.070, 0.070], Y in [0.160, 0.410], Z in [0.055, 0.080]
    # Length = 0.250 (from 0.160 to 0.410, center 0.285)
    add_box((0.0, 0.285, 0.0675), (0.140, 0.246, 0.025))

    # Axle bearing mounts extending down to Z = 0.040 at wheel centers Y = 0.215, 0.355
    add_box((0.0, 0.215, 0.0475), (0.130, 0.030, 0.015))
    add_box((0.0, 0.355, 0.0475), (0.130, 0.030, 0.015))

    # 2. Hopper body (box walls):
    # Floor: Z in [0.080, 0.090], X in [-0.073, 0.073], Y in [0.165, 0.405]
    add_box((0.0, 0.285, 0.085), (0.146, 0.240, 0.010))

    # Left Wall: X in [-0.073, -0.063], Z in [0.090, 0.170]
    add_box((-0.068, 0.285, 0.130), (0.010, 0.240, 0.080))
    # Right Wall: X in [0.063, 0.073], Z in [0.090, 0.170]
    add_box((0.068, 0.285, 0.130), (0.010, 0.240, 0.080))

    # Front Wall: Y in [0.165, 0.175], Z in [0.090, 0.170]
    add_box((0.0, 0.170, 0.130), (0.126, 0.010, 0.080))
    # Rear Wall: Y in [0.395, 0.405], Z in [0.090, 0.170]
    add_box((0.0, 0.400, 0.130), (0.126, 0.010, 0.080))

    # Top lip rim molding: Z in [0.170, 0.180]
    # Spanning X in [-0.075, 0.075], Y in [0.160, 0.410]
    # Left rim bar
    add_box((-0.070, 0.285, 0.175), (0.010, 0.250, 0.010))
    # Right rim bar
    add_box((0.070, 0.285, 0.175), (0.010, 0.250, 0.010))
    # Front rim bar
    add_box((0.0, 0.165, 0.175), (0.150, 0.010, 0.010))
    # Rear rim bar
    add_box((0.0, 0.405, 0.175), (0.150, 0.010, 0.010))

    obj = obj_from_bmesh("TenderChassis", bm)
    obj.data.materials.append(mat_gloss_dark_green())
    return obj
