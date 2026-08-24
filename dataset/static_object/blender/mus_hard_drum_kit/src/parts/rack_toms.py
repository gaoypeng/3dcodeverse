"""RackToms — pair of mounted tom-toms (10-inch and 12-inch) on bass-mounted tom holder."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_tube, add_box
)

RACK_TOMS_CENTER = (0.000, -0.050, 0.780)
RACK_TOMS_EXTENTS = (0.650, 0.360, 0.380)

def _build_tom_shell(bm, center_pos, radius, depth, rot_euler, blue_mat=0, chrome_mat=1, head_mat=2):
    tr_rot = rot_euler.to_matrix().to_4x4()
    tr_center = Matrix.Translation(center_pos) @ tr_rot
    
    # Shell
    add_cylinder(bm, radius, depth, segments=36, transform=tr_center, mat_idx=blue_mat)
    
    # Top & Bottom clear heads
    tr_thead = Matrix.Translation(center_pos + tr_rot @ Vector((0, 0, depth * 0.501))) @ tr_rot
    add_cylinder(bm, radius * 0.98, 0.004, segments=36, transform=tr_thead, mat_idx=head_mat)
    
    tr_bhead = Matrix.Translation(center_pos + tr_rot @ Vector((0, 0, -depth * 0.501))) @ tr_rot
    add_cylinder(bm, radius * 0.98, 0.004, segments=36, transform=tr_bhead, mat_idx=head_mat)
    
    # Chrome Hoops
    tr_thoop = Matrix.Translation(center_pos + tr_rot @ Vector((0, 0, depth * 0.5 - 0.01))) @ tr_rot
    add_tube(bm, outer_r=radius + 0.012, inner_r=radius - 0.002, depth=0.024, segments=36, transform=tr_thoop, mat_idx=chrome_mat)
    
    tr_bhoop = Matrix.Translation(center_pos + tr_rot @ Vector((0, 0, -depth * 0.5 + 0.01))) @ tr_rot
    add_tube(bm, outer_r=radius + 0.012, inner_r=radius - 0.002, depth=0.024, segments=36, transform=tr_bhoop, mat_idx=chrome_mat)
    
    # Lugs around tom (6 lugs)
    n_lugs = 6
    for i in range(n_lugs):
        ang = 2.0 * math.pi * i / n_lugs
        lx = (radius + 0.006) * math.cos(ang)
        ly = (radius + 0.006) * math.sin(ang)
        for z_off in [-depth * 0.25, depth * 0.25]:
            lug_pos = center_pos + tr_rot @ Vector((lx, ly, z_off))
            add_box(bm, (0.014, 0.014, 0.025), transform=Matrix.Translation(lug_pos) @ tr_rot, mat_idx=chrome_mat)
        # Tension rod
        rod_pos = center_pos + tr_rot @ Vector((lx * 1.02, ly * 1.02, 0))
        add_cylinder(bm, 0.0025, depth * 0.9, segments=8, transform=Matrix.Translation(rod_pos) @ tr_rot, mat_idx=chrome_mat)

def build_rack_toms():
    mats = get_drum_materials()
    mat_list = [
        mats["shell_blue"],  # 0
        mats["chrome"],      # 1
        mats["head_clear"],  # 2
    ]
    
    bm = bmesh.new()
    
    # Tom mount tree base emerging from top of bass drum (z=0.60, y=-0.07) up to z=0.76
    tr_tree_base = Matrix.Translation((0.0, -0.07, 0.68))
    add_cylinder(bm, 0.014, 0.16, segments=16, transform=tr_tree_base, mat_idx=1)
    
    # Double tom bracket / joint block at top
    tr_joint = Matrix.Translation((0.0, -0.07, 0.76))
    add_box(bm, (0.12, 0.05, 0.04), transform=tr_joint, mat_idx=1)
    
    # Left / Small Tom (10-inch, diam 0.254m, depth 0.20m, on right side of kit from drummer, or x=-0.16)
    # Note: From audience perspective: drummer faces -Y, so drummer's left is kit's right (-X is drummer's right, +X is drummer's left).
    # High tom: x = -0.16, Low mounted tom: x = +0.16.
    # Angle tilted slightly toward player (+Y) and tilted toward center.
    rot_tom1 = Euler((0.25, 0.15, 0.0), 'XYZ')
    center_tom1 = Vector((-0.16, -0.05, 0.78))
    _build_tom_shell(bm, center_tom1, radius=0.127, depth=0.20, rot_euler=rot_tom1, blue_mat=0, chrome_mat=1, head_mat=2)
    
    # Mounting L-arm for Tom 1
    p_arm1_start = Vector((-0.05, -0.07, 0.76))
    p_arm1_mid = Vector((-0.12, -0.06, 0.76))
    p_arm1_end = center_tom1 + rot_tom1.to_matrix().to_4x4() @ Vector((0.11, 0, 0))
    # Arm horizontal
    diff1 = p_arm1_mid - p_arm1_start
    add_cylinder(bm, 0.007, diff1.length, segments=10, 
                 transform=Matrix.Translation((p_arm1_start + p_arm1_mid) * 0.5) @ diff1.to_track_quat('Z', 'X').to_matrix().to_4x4(), 
                 mat_idx=1)
    diff2 = p_arm1_end - p_arm1_mid
    add_cylinder(bm, 0.007, diff2.length, segments=10, 
                 transform=Matrix.Translation((p_arm1_mid + p_arm1_end) * 0.5) @ diff2.to_track_quat('Z', 'X').to_matrix().to_4x4(), 
                 mat_idx=1)
    
    # Right / Mid Tom (12-inch, diam 0.305m, depth 0.23m, at x = +0.17)
    rot_tom2 = Euler((0.25, -0.15, 0.0), 'XYZ')
    center_tom2 = Vector((0.17, -0.05, 0.78))
    _build_tom_shell(bm, center_tom2, radius=0.152, depth=0.23, rot_euler=rot_tom2, blue_mat=0, chrome_mat=1, head_mat=2)
    
    # Mounting L-arm for Tom 2
    p_arm2_start = Vector((0.05, -0.07, 0.76))
    p_arm2_mid = Vector((0.13, -0.06, 0.76))
    p_arm2_end = center_tom2 + rot_tom2.to_matrix().to_4x4() @ Vector((-0.13, 0, 0))
    diff3 = p_arm2_mid - p_arm2_start
    add_cylinder(bm, 0.007, diff3.length, segments=10, 
                 transform=Matrix.Translation((p_arm2_start + p_arm2_mid) * 0.5) @ diff3.to_track_quat('Z', 'X').to_matrix().to_4x4(), 
                 mat_idx=1)
    diff4 = p_arm2_end - p_arm2_mid
    add_cylinder(bm, 0.007, diff4.length, segments=10, 
                 transform=Matrix.Translation((p_arm2_mid + p_arm2_end) * 0.5) @ diff4.to_track_quat('Z', 'X').to_matrix().to_4x4(), 
                 mat_idx=1)
    
    return mesh_from_bmesh("RackToms", bm, mat_list)
