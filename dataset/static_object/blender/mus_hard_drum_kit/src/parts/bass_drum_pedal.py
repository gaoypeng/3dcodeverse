"""BassDrumPedal — single kick pedal with footboard, dual posts, spring, and felt beater."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_cone, add_box
)

PEDAL_CENTER = (0.000, 0.180, 0.120)
PEDAL_EXTENTS = (0.180, 0.260, 0.240)

def build_bass_drum_pedal():
    mats = get_drum_materials()
    mat_list = [
        mats["chrome"],       # 0 (uprights, clamp, shaft)
        mats["black_metal"],  # 1 (base plate, footboard)
        mats["felt_white"],   # 2 (beater head)
        mats["rubber_black"]  # 3 (clamp pads)
    ]
    
    bm = bmesh.new()
    
    # Base plate resting on ground z=0..0.008
    # Center y around 0.18, spans y from 0.06 to 0.30
    tr_base = Matrix.Translation((0.0, 0.18, 0.004))
    add_box(bm, (0.14, 0.24, 0.008), transform=tr_base, mat_idx=1)
    
    # Hoop Clamp at the front (y = 0.065) attaching to bass drum rear hoop
    tr_clamp = Matrix.Translation((0.0, 0.075, 0.015))
    add_box(bm, (0.09, 0.03, 0.02), transform=tr_clamp, mat_idx=0)
    
    # Dual Upright Posts (x = +/- 0.055, y = 0.10, z from 0.01 to 0.18)
    upright_h = 0.17
    for sx in [-0.055, 0.055]:
        tr_post = Matrix.Translation((sx, 0.10, 0.01 + upright_h * 0.5))
        add_cylinder(bm, 0.008, upright_h, segments=12, transform=tr_post, mat_idx=0)
    
    # Horizontal top axle shaft at z = 0.175, y = 0.10
    tr_axle = Matrix.Translation((0.0, 0.10, 0.175)) @ Matrix.Rotation(math.pi / 2.0, 4, 'Y')
    add_cylinder(bm, 0.006, 0.13, segments=12, transform=tr_axle, mat_idx=0)
    
    # Cam and sprocket at center
    tr_cam = Matrix.Translation((0.0, 0.10, 0.175))
    add_cylinder(bm, 0.016, 0.018, segments=16, transform=tr_cam @ Matrix.Rotation(math.pi/2, 4, 'Y'), mat_idx=0)
    
    # Side Tension Spring on right post
    tr_spring = Matrix.Translation((0.068, 0.10, 0.10))
    add_cylinder(bm, 0.007, 0.09, segments=10, transform=tr_spring, mat_idx=0)
    
    # Footboard (angled from heel plate y=0.28, z=0.015 up to hinge y=0.12, z=0.055)
    p_heel = Vector((0.0, 0.27, 0.015))
    p_toe = Vector((0.0, 0.12, 0.055))
    diff_fb = p_toe - p_heel
    mid_fb = (p_heel + p_toe) * 0.5
    rot_fb = diff_fb.to_track_quat('Y', 'Z').to_matrix().to_4x4()
    tr_fb = Matrix.Translation(mid_fb) @ rot_fb
    add_box(bm, (0.075, diff_fb.length, 0.008), transform=tr_fb, mat_idx=1)
    
    # Heel plate
    tr_heel = Matrix.Translation(p_heel)
    add_box(bm, (0.08, 0.04, 0.014), transform=tr_heel, mat_idx=1)
    
    # Chain / linkage connecting toe of footboard to cam
    tr_chain = Matrix.Translation((0.0, 0.11, 0.115))
    add_box(bm, (0.012, 0.016, 0.11), transform=tr_chain, mat_idx=0)
    
    # Beater Shaft & Head
    # Axle at (0, 0.10, 0.175) -> shaft extends forward/upward toward bass drum batter head (y=0.07, z=0.31)
    p_axle = Vector((0.0, 0.10, 0.175))
    p_beater = Vector((0.0, 0.075, 0.225))
    diff_b = p_beater - p_axle
    mid_b = (p_axle + p_beater) * 0.5
    rot_b = diff_b.to_track_quat('Z', 'X').to_matrix().to_4x4()
    tr_shaft = Matrix.Translation(mid_b) @ rot_b
    add_cylinder(bm, 0.0035, diff_b.length + 0.02, segments=8, transform=tr_shaft, mat_idx=0)
    
    # Felt Beater Head (cylinder oriented along Y, pressed near batter head)
    tr_head = Matrix.Translation(p_beater) @ Matrix.Rotation(math.pi / 2.0, 4, 'X')
    add_cylinder(bm, 0.018, 0.032, segments=16, transform=tr_head, mat_idx=2)
    
    return mesh_from_bmesh("BassDrumPedal", bm, mat_list)
