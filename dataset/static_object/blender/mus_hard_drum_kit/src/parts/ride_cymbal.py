"""RideCymbal — 20-inch heavy ride cymbal on an adjustable boom stand."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_cone, add_box, add_tripod_stand
)

RIDE_CENTER = (-0.620, -0.120, 0.620)
RIDE_EXTENTS = (0.560, 0.560, 1.180)

def build_ride_cymbal():
    mats = get_drum_materials()
    mat_list = [
        mats["bronze"],        # 0 (cymbal)
        mats["chrome"],        # 1 (stand & boom arm)
        mats["rubber_black"],  # 2 (rubber feet)
        mats["felt_black"]     # 3 (felts)
    ]
    
    bm = bmesh.new()
    base_center = (-0.620, -0.120, 0.0)
    
    # 1. Main Tripod Stand (standing on ground up to height z=0.88)
    add_tripod_stand(bm, base_center=base_center, top_z=0.88, leg_radius=0.23, pole_r=0.013, chrome_mat=1, rubber_mat=2)
    
    # Boom joint clamp at top of vertical pole (z=0.88)
    tr_joint = Matrix.Translation((-0.620, -0.120, 0.88))
    add_box(bm, (0.045, 0.05, 0.05), transform=tr_joint, mat_idx=1)
    # Wing nut on clamp
    add_box(bm, (0.045, 0.01, 0.02), transform=Matrix.Translation((-0.650, -0.120, 0.88)), mat_idx=1)
    
    # Counterweight behind boom joint
    tr_cweight = Matrix.Translation((-0.720, -0.220, 0.78))
    add_cylinder(bm, 0.024, 0.07, segments=16, transform=tr_cweight, mat_idx=1)
    
    # Boom arm extending from counterweight to ride cymbal mount
    # Ride cymbal position: (-0.54, 0.02, 1.02)
    p_cymbal = Vector((-0.540, 0.020, 1.020))
    p_cweight = Vector((-0.720, -0.220, 0.780))
    diff_boom = p_cymbal - p_cweight
    mid_boom = (p_cymbal + p_cweight) * 0.5
    rot_boom = diff_boom.to_track_quat('Z', 'X').to_matrix().to_4x4()
    add_cylinder(bm, 0.0075, diff_boom.length, segments=12, transform=Matrix.Translation(mid_boom) @ rot_boom, mat_idx=1)
    
    # Cymbal tilter assembly
    tr_tilter = Matrix.Translation(p_cymbal)
    add_cylinder(bm, 0.013, 0.035, segments=12, transform=tr_tilter, mat_idx=1)
    
    # Ride Cymbal (20-inch diam = 0.508m -> radius 0.254m) with pronounced bell, tilted slightly
    rot_cymbal = Euler((-0.15, -0.10, 0.0), 'XYZ')
    tr_c_rot = rot_cymbal.to_matrix().to_4x4()
    tr_c_base = Matrix.Translation(p_cymbal + Vector((0, 0, 0.02))) @ tr_c_rot
    
    # Lower felt
    tr_felt_bot = tr_c_base @ Matrix.Translation((0, 0, -0.006))
    add_cylinder(bm, 0.02, 0.012, segments=16, transform=tr_felt_bot, mat_idx=3)
    
    # Large heavy Bell
    tr_bell = tr_c_base @ Matrix.Translation((0, 0, 0.012))
    add_cone(bm, radius1=0.052, radius2=0.016, depth=0.025, segments=36, transform=tr_bell, mat_idx=0)
    
    # Ride Cymbal Bow
    tr_bow = tr_c_base @ Matrix.Translation((0, 0, 0.004))
    add_cone(bm, radius1=0.254, radius2=0.052, depth=0.011, segments=48, transform=tr_bow, mat_idx=0)
    
    # Upper felt
    tr_felt_top = tr_c_base @ Matrix.Translation((0, 0, 0.020))
    add_cylinder(bm, 0.02, 0.012, segments=16, transform=tr_felt_top, mat_idx=3)
    
    # Wing nut on top
    tr_wing = tr_c_base @ Matrix.Translation((0, 0, 0.035))
    add_box(bm, (0.04, 0.008, 0.016), transform=tr_wing, mat_idx=1)
    
    return mesh_from_bmesh("RideCymbal", bm, mat_list)
