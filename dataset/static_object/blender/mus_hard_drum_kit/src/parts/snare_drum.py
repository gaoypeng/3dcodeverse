"""SnareDrum — 14x5.5 inch snare drum on adjustable tripod basket stand."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_tube, add_box, add_tripod_stand
)

SNARE_CENTER = (0.360, 0.240, 0.420)
SNARE_EXTENTS = (0.420, 0.420, 0.760)

def build_snare_drum():
    mats = get_drum_materials()
    mat_list = [
        mats["snare_silver"],  # 0 (chrome snare shell)
        mats["chrome"],        # 1 (hoops, basket, stand)
        mats["head_white"],    # 2 (coated white batter head)
        mats["head_clear"],    # 3 (snare side bottom head)
        mats["rubber_black"]   # 4 (feet & basket rubber tips)
    ]
    
    bm = bmesh.new()
    center_pos = Vector((0.360, 0.240, 0.680)) # top of snare drum around z=0.75
    stand_base = (0.360, 0.240, 0.0)
    
    # 1. Stand Base (Tripod)
    add_tripod_stand(bm, base_center=stand_base, top_z=0.58, leg_radius=0.18, pole_r=0.012, chrome_mat=1, rubber_mat=4)
    
    # 2. Snare Basket mechanism
    # Central basket joint / collar at z = 0.58
    tr_basket_hub = Matrix.Translation((0.360, 0.240, 0.58))
    add_cylinder(bm, 0.018, 0.04, segments=16, transform=tr_basket_hub, mat_idx=1)
    
    # 3 Basket arms holding the bottom hoop of the snare
    snare_radius = 0.178 # 14-inch drum diam = 0.356 m -> radius 0.178
    snare_depth = 0.14  # 5.5-inch depth = 0.14 m
    snare_rot = Euler((0.08, 0.05, 0.0), 'XYZ')
    rot_mat = snare_rot.to_matrix().to_4x4()
    
    for i in range(3):
        ang = 2.0 * math.pi * i / 3.0 + 0.5
        p_hub = Vector((0.360, 0.240, 0.58))
        # Arm reaches to the lower hoop edge
        arm_end = center_pos + rot_mat @ Vector(((snare_radius + 0.015) * math.cos(ang), (snare_radius + 0.015) * math.sin(ang), -snare_depth * 0.5))
        diff = arm_end - p_hub
        mid = (p_hub + arm_end) * 0.5
        rot_arm = diff.to_track_quat('Z', 'X').to_matrix().to_4x4()
        add_cylinder(bm, 0.006, diff.length, segments=10, transform=Matrix.Translation(mid) @ rot_arm, mat_idx=1)
        
        # Rubber claw grip at the top of each basket arm
        tr_claw = Matrix.Translation(arm_end)
        add_box(bm, (0.018, 0.018, 0.025), transform=tr_claw, mat_idx=4)
        
    # 3. Snare Shell
    tr_snare = Matrix.Translation(center_pos) @ rot_mat
    add_cylinder(bm, snare_radius, snare_depth, segments=40, transform=tr_snare, mat_idx=0)
    
    # Top Coated White Head
    tr_thead = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, snare_depth * 0.501))) @ rot_mat
    add_cylinder(bm, snare_radius * 0.98, 0.003, segments=40, transform=tr_thead, mat_idx=2)
    
    # Bottom Clear Resonant Head
    tr_bhead = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, -snare_depth * 0.501))) @ rot_mat
    add_cylinder(bm, snare_radius * 0.98, 0.003, segments=40, transform=tr_bhead, mat_idx=3)
    
    # Chrome Rims / Hoops
    tr_thoop = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, snare_depth * 0.5 - 0.008))) @ rot_mat
    add_tube(bm, outer_r=snare_radius + 0.014, inner_r=snare_radius - 0.002, depth=0.02, segments=40, transform=tr_thoop, mat_idx=1)
    
    tr_bhoop = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, -snare_depth * 0.5 + 0.008))) @ rot_mat
    add_tube(bm, outer_r=snare_radius + 0.014, inner_r=snare_radius - 0.002, depth=0.02, segments=40, transform=tr_bhoop, mat_idx=1)
    
    # 8 Snare Lugs around perimeter
    n_lugs = 8
    for i in range(n_lugs):
        ang = 2.0 * math.pi * i / n_lugs
        lx = (snare_radius + 0.006) * math.cos(ang)
        ly = (snare_radius + 0.006) * math.sin(ang)
        lug_pos = center_pos + rot_mat @ Vector((lx, ly, 0))
        add_box(bm, (0.014, 0.014, 0.08), transform=Matrix.Translation(lug_pos) @ rot_mat, mat_idx=1)
        
    # Snare Strainer / Throw-off mechanism on side
    strainer_pos = center_pos + rot_mat @ Vector((0, -(snare_radius + 0.018), 0))
    add_box(bm, (0.04, 0.02, 0.07), transform=Matrix.Translation(strainer_pos) @ rot_mat, mat_idx=1)
    
    return mesh_from_bmesh("SnareDrum", bm, mat_list)
