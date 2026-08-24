"""CrashCymbal — 16-inch crash cymbal mounted on a tall boom tripod stand."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_cone, add_box, add_tripod_stand
)

CRASH_CENTER = (0.550, -0.320, 0.680)
CRASH_EXTENTS = (0.520, 0.520, 1.300)

def build_crash_cymbal():
    mats = get_drum_materials()
    mat_list = [
        mats["bronze"],        # 0 (cymbal)
        mats["chrome"],        # 1 (stand & boom arm)
        mats["rubber_black"],  # 2 (rubber feet)
        mats["felt_black"]     # 3 (felts)
    ]
    
    bm = bmesh.new()
    base_center = (0.550, -0.320, 0.0)
    
    # 1. Main Tripod Stand (standing on ground up to height z=0.95)
    add_tripod_stand(bm, base_center=base_center, top_z=0.95, leg_radius=0.22, pole_r=0.012, chrome_mat=1, rubber_mat=2)
    
    # Boom joint / tilter clamp at top of vertical pole (z=0.95)
    tr_joint = Matrix.Translation((0.550, -0.320, 0.95))
    add_box(bm, (0.04, 0.05, 0.05), transform=tr_joint, mat_idx=1)
    # Wing nut on clamp
    add_box(bm, (0.045, 0.01, 0.02), transform=Matrix.Translation((0.580, -0.320, 0.95)), mat_idx=1)
    
    # Counterweight behind boom joint
    tr_cweight = Matrix.Translation((0.640, -0.380, 0.85))
    add_cylinder(bm, 0.02, 0.06, segments=16, transform=tr_cweight, mat_idx=1)
    
    # Boom arm extending from counterweight through joint up to cymbal mount
    # Cymbal position: (0.48, -0.22, 1.15)
    p_cymbal = Vector((0.480, -0.220, 1.150))
    p_cweight = Vector((0.640, -0.380, 0.850))
    diff_boom = p_cymbal - p_cweight
    mid_boom = (p_cymbal + p_cweight) * 0.5
    rot_boom = diff_boom.to_track_quat('Z', 'X').to_matrix().to_4x4()
    add_cylinder(bm, 0.007, diff_boom.length, segments=12, transform=Matrix.Translation(mid_boom) @ rot_boom, mat_idx=1)
    
    # Cymbal tilter assembly at end of boom
    tr_tilter = Matrix.Translation(p_cymbal)
    add_cylinder(bm, 0.012, 0.035, segments=12, transform=tr_tilter, mat_idx=1)
    
    # Crash Cymbal (16-inch diam = 0.406m -> radius 0.203m) tilted slightly toward drummer
    rot_cymbal = Euler((-0.18, 0.12, 0.0), 'XYZ')
    tr_c_rot = rot_cymbal.to_matrix().to_4x4()
    tr_c_base = Matrix.Translation(p_cymbal + Vector((0, 0, 0.02))) @ tr_c_rot
    
    # Lower felt
    tr_felt_bot = tr_c_base @ Matrix.Translation((0, 0, -0.006))
    add_cylinder(bm, 0.018, 0.012, segments=16, transform=tr_felt_bot, mat_idx=3)
    
    # Cymbal Bell
    tr_bell = tr_c_base @ Matrix.Translation((0, 0, 0.01))
    add_cone(bm, radius1=0.04, radius2=0.014, depth=0.02, segments=36, transform=tr_bell, mat_idx=0)
    
    # Cymbal Bow (lathed disc with slight taper)
    tr_bow = tr_c_base @ Matrix.Translation((0, 0, 0.003))
    add_cone(bm, radius1=0.203, radius2=0.04, depth=0.009, segments=44, transform=tr_bow, mat_idx=0)
    
    # Upper felt
    tr_felt_top = tr_c_base @ Matrix.Translation((0, 0, 0.018))
    add_cylinder(bm, 0.018, 0.012, segments=16, transform=tr_felt_top, mat_idx=3)
    
    # Wing nut on top
    tr_wing = tr_c_base @ Matrix.Translation((0, 0, 0.032))
    add_box(bm, (0.038, 0.008, 0.016), transform=tr_wing, mat_idx=1)
    
    return mesh_from_bmesh("CrashCymbal", bm, mat_list)
