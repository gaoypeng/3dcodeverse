"""LocomotiveCab — driver cab cabin with arched windows and curved roof."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_satin_dark_red

random.seed(0)

# Plan numbers:
# center (0.000, 0.045, 0.175) extents (0.160, 0.150, 0.170)
# x in [-0.080, 0.080], y in [-0.030, 0.120], z in [0.090, 0.260]
# Cab sits on chassis (z = 0.090) behind boiler (boiler ends at y = -0.020).

def build_locomotive_cab() -> bpy.types.Object:
    # Build cab geometry directly using solid panels/walls to form the hollow cabin with windows
    bm = bmesh.new()

    # Dimensions:
    # Cabin body: X in [-0.075, 0.075] (width 0.150), Y in [-0.025, 0.115] (length 0.140), Z in [0.090, 0.245] (height 0.155)
    # Floor: Z = 0.090 to 0.100
    # Left / Right side walls with window cutout
    # Front wall with window cutout
    # Back wall with door cutout
    # Overhanging curved roof: X in [-0.080, 0.080], Y in [-0.030, 0.120], Z in [0.245, 0.260]

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

    # Floor panel
    add_box((0.0, 0.045, 0.095), (0.150, 0.140, 0.010))

    # Left wall (X = -0.070): has lower section, upper front pillar, upper rear pillar, top header
    # Left lower wall: Z in [0.100, 0.160]
    add_box((-0.070, 0.045, 0.130), (0.010, 0.140, 0.060))
    # Left front pillar: Z in [0.160, 0.220], Y in [-0.025, 0.010]
    add_box((-0.070, -0.0075, 0.190), (0.010, 0.035, 0.060))
    # Left rear pillar: Z in [0.160, 0.220], Y in [0.080, 0.115]
    add_box((-0.070, 0.0975, 0.190), (0.010, 0.035, 0.060))
    # Left top header: Z in [0.220, 0.245]
    add_box((-0.070, 0.045, 0.2325), (0.010, 0.140, 0.025))

    # Right wall (X = +0.070):
    # Right lower wall:
    add_box((0.070, 0.045, 0.130), (0.010, 0.140, 0.060))
    # Right front pillar:
    add_box((0.070, -0.0075, 0.190), (0.010, 0.035, 0.060))
    # Right rear pillar:
    add_box((0.070, 0.0975, 0.190), (0.010, 0.035, 0.060))
    # Right top header:
    add_box((0.070, 0.045, 0.2325), (0.010, 0.140, 0.025))

    # Front wall (Y = -0.020):
    # Front lower wall: Z in [0.100, 0.160]
    add_box((0.0, -0.020, 0.130), (0.130, 0.010, 0.060))
    # Front center pillar: Z in [0.160, 0.220], X in [-0.015, 0.015]
    add_box((0.0, -0.020, 0.190), (0.030, 0.010, 0.060))
    # Front top header: Z in [0.220, 0.245]
    add_box((0.0, -0.020, 0.2325), (0.130, 0.010, 0.025))

    # Back wall (Y = +0.110): left post and right post around door opening
    # Back left post: X in [-0.065, -0.035], Z in [0.100, 0.245]
    add_box((-0.050, 0.110, 0.1725), (0.030, 0.010, 0.145))
    # Back right post: X in [0.035, 0.065], Z in [0.100, 0.245]
    add_box((0.050, 0.110, 0.1725), (0.030, 0.010, 0.145))
    # Back door header: X in [-0.035, 0.035], Z in [0.225, 0.245]
    add_box((0.0, 0.110, 0.235), (0.070, 0.010, 0.020))

    # Roof: curved/overhanging roof
    # Extents: X: 0.160 (-0.080 to 0.080), Y: 0.150 (-0.030 to 0.120), Z: 0.245 to 0.260 (thickness 0.015)
    bm_roof = bmesh.new()
    bmesh.ops.create_cube(bm_roof, size=1.0)
    bmesh.ops.scale(bm_roof, vec=(0.160, 0.150, 0.015), verts=bm_roof.verts)
    bmesh.ops.translate(bm_roof, vec=(0.0, 0.045, 0.2525), verts=bm_roof.verts)

    # Bevel roof top edges
    bmesh.ops.bevel(bm_roof, geom=bm_roof.edges[:], offset=0.003, segments=2, profile=0.5, affect='EDGES')

    m = bpy.data.meshes.new("_tmp_roof")
    bm_roof.to_mesh(m)
    bm.from_mesh(m)
    bpy.data.meshes.remove(m)
    bm_roof.free()

    obj = obj_from_bmesh("LocomotiveCab", bm)
    obj.data.materials.append(mat_satin_dark_red())
    return obj
