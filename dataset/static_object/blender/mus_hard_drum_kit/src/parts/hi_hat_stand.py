"""HiHatStand — hi-hat stand with dual 14-inch cymbals, foot pedal, and tripod base."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_cone, add_box, add_tripod_stand
)

HIHAT_CENTER = (0.650, 0.120, 0.550)
HIHAT_EXTENTS = (0.520, 0.520, 1.080)

def _add_cymbal(bm, center_pos, radius=0.178, rot_matrix=None, flip=False, mat_idx=0):
    tr = Matrix.Translation(center_pos)
    if rot_matrix:
        tr = tr @ rot_matrix
    if flip:
        tr = tr @ Matrix.Rotation(math.pi, 4, 'X')
        
    # Cymbal profile: central bell cone + outer flared disc
    # 1. Central bell
    tr_bell = tr @ Matrix.Translation((0, 0, 0.008))
    add_cone(bm, radius1=0.035, radius2=0.012, depth=0.018, segments=36, transform=tr_bell, mat_idx=mat_idx)
    # 2. Main body taper/bow
    tr_bow = tr @ Matrix.Translation((0, 0, 0.002))
    add_cone(bm, radius1=radius, radius2=0.035, depth=0.008, segments=40, transform=tr_bow, mat_idx=mat_idx)

def build_hi_hat_stand():
    mats = get_drum_materials()
    mat_list = [
        mats["bronze"],        # 0 (cymbals)
        mats["chrome"],        # 1 (stand hardware, clutch, pull rod)
        mats["black_metal"],   # 2 (pedal footplate)
        mats["rubber_black"],  # 3 (feet)
        mats["felt_black"]     # 4 (felt washers)
    ]
    
    bm = bmesh.new()
    base_center = (0.650, 0.120, 0.0)
    
    # 1. Tripod Base (standing on ground z=0..0.024 up to central shaft z=1.08)
    add_tripod_stand(bm, base_center=base_center, top_z=1.06, leg_radius=0.22, pole_r=0.012, chrome_mat=1, rubber_mat=3)
    
    # 2. Hi-Hat Pedal at base (connected to lower pull rod, extending towards drummer +Y / -X)
    # Pedal angle pointing slightly toward throne
    pedal_dir = Vector((-0.08, 0.16, 0.0))
    pedal_len = pedal_dir.length
    pedal_mid = Vector((0.650, 0.120, 0.015)) + pedal_dir * 0.5
    rot_p = pedal_dir.to_track_quat('Y', 'Z').to_matrix().to_4x4()
    
    # Base frame of pedal
    add_box(bm, (0.09, pedal_len, 0.01), transform=Matrix.Translation(pedal_mid) @ rot_p, mat_idx=2)
    # Footplate angled upward
    tr_footplate = Matrix.Translation(pedal_mid + Vector((0, 0, 0.02))) @ rot_p @ Matrix.Rotation(0.12, 4, 'X')
    add_box(bm, (0.07, pedal_len * 0.9, 0.008), transform=tr_footplate, mat_idx=2)
    # Heel plate
    add_box(bm, (0.08, 0.04, 0.018), transform=Matrix.Translation(Vector((0.650, 0.120, 0.015)) + pedal_dir) @ rot_p, mat_idx=2)
    
    # 3. Cymbal Seat & Lower Cymbal (14-inch diam = 0.356m, radius=0.178m) at z = 0.94
    cymbal_z = 0.95
    c_center = Vector((0.650, 0.120, cymbal_z))
    
    # Bottom cymbal seat (felt washer & plastic cup)
    tr_seat = Matrix.Translation(c_center + Vector((0, 0, -0.01)))
    add_cylinder(bm, 0.025, 0.015, segments=20, transform=tr_seat, mat_idx=4)
    
    # Bottom Cymbal (facing up)
    _add_cymbal(bm, center_pos=c_center, radius=0.178, flip=False, mat_idx=0)
    
    # Top Cymbal (facing down or up with slight gap) at z = 0.965
    top_c_center = c_center + Vector((0, 0, 0.015))
    _add_cymbal(bm, center_pos=top_c_center, radius=0.178, flip=False, mat_idx=0)
    
    # Hi-Hat Clutch assembly on top
    tr_clutch_felt = Matrix.Translation(top_c_center + Vector((0, 0, 0.012)))
    add_cylinder(bm, 0.018, 0.012, segments=16, transform=tr_clutch_felt, mat_idx=4)
    
    tr_clutch_metal = Matrix.Translation(top_c_center + Vector((0, 0, 0.035)))
    add_cylinder(bm, 0.012, 0.035, segments=16, transform=tr_clutch_metal, mat_idx=1)
    # Wing nut on clutch
    add_box(bm, (0.035, 0.008, 0.015), transform=Matrix.Translation(top_c_center + Vector((0, 0, 0.045))), mat_idx=1)
    
    # Pull rod tip extending above clutch to z=1.06
    tr_tip = Matrix.Translation(Vector((0.650, 0.120, 1.02)))
    add_cylinder(bm, 0.004, 0.08, segments=8, transform=tr_tip, mat_idx=1)
    
    return mesh_from_bmesh("HiHatStand", bm, mat_list)
