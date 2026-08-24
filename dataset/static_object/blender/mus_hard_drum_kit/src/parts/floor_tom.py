"""FloorTom — 16x16 inch floor tom supported on three steel legs."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_tube, add_box
)

FLOOR_TOM_CENTER = (-0.500, 0.280, 0.420)
FLOOR_TOM_EXTENTS = (0.480, 0.480, 0.740)

def build_floor_tom():
    mats = get_drum_materials()
    mat_list = [
        mats["shell_blue"],    # 0
        mats["chrome"],        # 1
        mats["head_clear"],    # 2
        mats["rubber_black"]   # 3
    ]
    
    bm = bmesh.new()
    center_pos = Vector((-0.500, 0.280, 0.530)) # Center of shell height
    
    radius = 0.203  # 16-inch diameter = 0.406m -> radius ~ 0.203m
    depth = 0.406   # 16-inch depth = 0.406m
    # Slight tilt toward drummer
    rot_euler = Euler((0.06, -0.04, 0.0), 'XYZ')
    rot_mat = rot_euler.to_matrix().to_4x4()
    
    # 1. Shell
    tr_shell = Matrix.Translation(center_pos) @ rot_mat
    add_cylinder(bm, radius, depth, segments=44, transform=tr_shell, mat_idx=0)
    
    # 2. Heads (Top & Bottom clear)
    tr_thead = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, depth * 0.501))) @ rot_mat
    add_cylinder(bm, radius * 0.98, 0.004, segments=44, transform=tr_thead, mat_idx=2)
    
    tr_bhead = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, -depth * 0.501))) @ rot_mat
    add_cylinder(bm, radius * 0.98, 0.004, segments=44, transform=tr_bhead, mat_idx=2)
    
    # 3. Rims / Hoops (Top & Bottom)
    tr_thoop = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, depth * 0.5 - 0.01))) @ rot_mat
    add_tube(bm, outer_r=radius + 0.014, inner_r=radius - 0.002, depth=0.025, segments=44, transform=tr_thoop, mat_idx=1)
    
    tr_bhoop = Matrix.Translation(center_pos + rot_mat @ Vector((0, 0, -depth * 0.5 + 0.01))) @ rot_mat
    add_tube(bm, outer_r=radius + 0.014, inner_r=radius - 0.002, depth=0.025, segments=44, transform=tr_bhoop, mat_idx=1)
    
    # 4. Lugs (8 around shell)
    n_lugs = 8
    for i in range(n_lugs):
        ang = 2.0 * math.pi * i / n_lugs
        lx = (radius + 0.006) * math.cos(ang)
        ly = (radius + 0.006) * math.sin(ang)
        for z_off in [-depth * 0.28, depth * 0.28]:
            lug_pos = center_pos + rot_mat @ Vector((lx, ly, z_off))
            add_box(bm, (0.014, 0.014, 0.03), transform=Matrix.Translation(lug_pos) @ rot_mat, mat_idx=1)
        # Tension rod
        rod_pos = center_pos + rot_mat @ Vector((lx * 1.02, ly * 1.02, 0))
        add_cylinder(bm, 0.0025, depth * 0.9, segments=8, transform=Matrix.Translation(rod_pos) @ rot_mat, mat_idx=1)

    # 5. Three Floor Tom Legs with Leg Brackets & Rubber feet touching ground z=0
    for i in range(3):
        ang = 2.0 * math.pi * i / 3.0 + 0.2
        # Bracket position on lower half of drum shell
        mount_local = Vector(((radius + 0.005) * math.cos(ang), (radius + 0.005) * math.sin(ang), -depth * 0.15))
        mount_pos = center_pos + rot_mat @ mount_local
        
        # Chrome bracket clamp
        add_box(bm, (0.035, 0.035, 0.04), transform=Matrix.Translation(mount_pos) @ rot_mat, mat_idx=1)
        
        # Leg spreads outward and touches ground at z=0.012
        leg_splay_r = radius + 0.09
        foot_pos = Vector((center_pos.x + leg_splay_r * math.cos(ang), center_pos.y + leg_splay_r * math.sin(ang), 0.012))
        
        # Straight / bent leg rod
        # Upper knee bend point
        knee_pos = mount_pos + rot_mat @ Vector((0.02 * math.cos(ang), 0.02 * math.sin(ang), 0.02))
        
        # Upper rod through bracket
        diff_u = knee_pos - mount_pos
        add_cylinder(bm, 0.006, 0.08, segments=10, transform=Matrix.Translation(mount_pos) @ rot_mat, mat_idx=1)
        
        # Main leg rod from knee to foot
        # Mount position z is around 0.47, knee around 0.49.
        # Foot touches ground at z=0 (cylinder height 0.024 centred at z=0.012 -> bottom at z=0.0)
        foot_center = Vector((center_pos.x + leg_splay_r * math.cos(ang), center_pos.y + leg_splay_r * math.sin(ang), 0.012))
        
        # Rubber foot on ground
        add_cylinder(bm, 0.015, 0.024, segments=12, transform=Matrix.Translation(foot_center), mat_idx=3)
        
        # Upper knee bend point
        knee_pos = mount_pos + rot_mat @ Vector((0.02 * math.cos(ang), 0.02 * math.sin(ang), 0.02))
        
        # Upper rod through bracket
        add_cylinder(bm, 0.006, 0.08, segments=10, transform=Matrix.Translation(mount_pos) @ rot_mat, mat_idx=1)
        
        # Main leg rod from knee to foot center
        diff_leg = foot_center - knee_pos
        mid_leg = (knee_pos + foot_center) * 0.5
        rot_leg = diff_leg.to_track_quat('Z', 'X').to_matrix().to_4x4()
        add_cylinder(bm, 0.0055, diff_leg.length, segments=10, transform=Matrix.Translation(mid_leg) @ rot_leg, mat_idx=1)
        
    return mesh_from_bmesh("FloorTom", bm, mat_list)
