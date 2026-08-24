"""BassDrum — 22-inch kick drum with dual splayed spurs and dual hoop rims."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_cone, add_tube, add_box, add_cylinder_z
)

# Planned center (0.000, -0.150, 0.320) and extents (0.680, 0.520, 0.640)
BASS_DRUM_CENTER = (0.000, -0.150, 0.320)
BASS_DRUM_EXTENTS = (0.680, 0.520, 0.640)

def build_bass_drum():
    mats = get_drum_materials()
    mat_list = [
        mats["shell_blue"],  # 0
        mats["chrome"],      # 1
        mats["head_black"],  # 2 (front resonant head at -Y)
        mats["head_clear"],  # 3 (rear batter head at +Y)
        mats["rubber_black"] # 4
    ]
    
    bm = bmesh.new()
    
    # Bass drum is oriented horizontally along Y-axis.
    # Center: (0.0, -0.15, 0.32). Shell radius = 0.285m (22.4" diam with hoops ~0.60m diameter).
    # Depth = 0.44m so rear face is at y = -0.15 + 0.22 = +0.070, perfectly meeting BassDrumPedal clamp at y=0.065..0.075 with <= 2mm overlap.
    center = Vector(BASS_DRUM_CENTER)
    rot_y = Matrix.Rotation(math.pi / 2.0, 4, 'X')
    tr_drum = Matrix.Translation(center) @ rot_y
    
    radius = 0.285 # Shell radius 0.285m, shell diameter = 0.570m
    depth = 0.440 # Depth along Y
    
    # Main Drum Shell
    add_cylinder(bm, radius, depth, segments=64, transform=tr_drum, mat_idx=0)
    
    # Front Resonant Head (black) at negative Y: y = center.y - depth * 0.5 = -0.15 - 0.22 = -0.37
    tr_fhead = Matrix.Translation(center + Vector((0, -depth * 0.501, 0))) @ rot_y
    add_cylinder(bm, radius * 0.99, 0.004, segments=64, transform=tr_fhead, mat_idx=2)
    
    # Rear Batter Head (white/clear) at positive Y: y = center.y + depth * 0.5 = -0.15 + 0.22 = +0.07
    tr_bhead = Matrix.Translation(center + Vector((0, depth * 0.501, 0))) @ rot_y
    add_cylinder(bm, radius * 0.99, 0.004, segments=64, transform=tr_bhead, mat_idx=3)
    
    # Front and Rear Chrome Hoops / Rims
    hoop_w = 0.035
    tr_fhoop = Matrix.Translation(center + Vector((0, -depth * 0.5 + hoop_w * 0.5 - 0.002, 0))) @ rot_y
    add_tube(bm, outer_r=radius + 0.016, inner_r=radius - 0.002, depth=hoop_w, segments=64, transform=tr_fhoop, mat_idx=1)
    
    tr_bhoop = Matrix.Translation(center + Vector((0, depth * 0.5 - hoop_w * 0.5 + 0.002, 0))) @ rot_y
    add_tube(bm, outer_r=radius + 0.016, inner_r=radius - 0.002, depth=hoop_w, segments=64, transform=tr_bhoop, mat_idx=1)
    
    # Lugs and Tension Rods around perimeter (10 lugs around the shell)
    n_lugs = 10
    for i in range(n_lugs):
        ang = 2.0 * math.pi * i / n_lugs
        lx = (radius + 0.009) * math.cos(ang)
        lz = (radius + 0.009) * math.sin(ang)
        
        # 2 cast lugs per rod line (front & back)
        for y_off in [-0.12, 0.12]:
            lug_pos = center + Vector((lx, y_off, lz))
            tr_lug = Matrix.Translation(lug_pos)
            add_box(bm, (0.016, 0.040, 0.016), transform=tr_lug, mat_idx=1)
            
        # Tension rod connecting/spanning
        rod_pos = center + Vector((lx, 0, lz))
        tr_rod = Matrix.Translation(rod_pos) @ rot_y
        add_cylinder(bm, 0.0035, depth * 0.90, segments=8, transform=tr_rod, mat_idx=1)

    # Bass Drum Spurs (Legs) splayed out forward/downward to floor touching z=0
    # Mount on front quarter of shell (toward -Y): y = -0.27
    for side in [-1, 1]:
        mount_pos = center + Vector((side * (radius - 0.005), -0.11, -0.04))
        # Sturdy bracket on shell
        add_box(bm, (0.045, 0.045, 0.045), transform=Matrix.Translation(mount_pos), mat_idx=1)
        
        # Rubber foot on ground strictly z=0..0.024 at x = +/- 0.33, y = -0.39
        foot_x, foot_y = side * 0.33, -0.38
        add_cylinder_z(bm, 0.016, z_min=0.0, z_max=0.024, center_xy=(foot_x, foot_y), segments=16, mat_idx=4)
        
        # Spur leg extending from bracket to top of rubber foot (z=0.024)
        foot_top = Vector((foot_x, foot_y, 0.024))
        diff = foot_top - mount_pos
        mid = (mount_pos + foot_top) * 0.5
        rot_spur = diff.to_track_quat('Z', 'X').to_matrix().to_4x4()
        tr_spur = Matrix.Translation(mid + diff.normalized() * 0.004) @ rot_spur
        add_cylinder(bm, 0.009, diff.length - 0.008, segments=16, transform=tr_spur, mat_idx=1)
        
        # Spur angle adjustment clamp knob
        tr_knob = Matrix.Translation(mount_pos + Vector((side * 0.025, 0, 0)))
        add_box(bm, (0.015, 0.03, 0.015), transform=tr_knob, mat_idx=1)
        
    # Tom mounting base plate / bracket on top of bass drum (at y = 0.08, z = center.z + radius + 0.01)
    top_bracket_pos = center + Vector((0.0, 0.08, radius + 0.01))
    add_box(bm, (0.08, 0.08, 0.02), transform=Matrix.Translation(top_bracket_pos), mat_idx=1)
    # Dual receiver holes / sockets
    for sx in [-0.022, 0.022]:
        sock_pos = top_bracket_pos + Vector((sx, 0, 0.015))
        add_cylinder(bm, 0.012, 0.025, segments=12, transform=Matrix.Translation(sock_pos), mat_idx=1)

    obj = mesh_from_bmesh("BassDrum", bm, mat_list)
    # Enable smooth shading
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
