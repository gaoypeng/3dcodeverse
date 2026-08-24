"""DrumThrone — round padded drummer stool on tripod base."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix, Euler
from parts._common import (
    get_drum_materials, mesh_from_bmesh, add_cylinder, add_cone, add_box, add_tripod_stand
)

# Plan targets:
# Center: (0.000, 0.620, 0.280)
# Extents: (0.440, 0.440, 0.560) -> x: [-0.22, 0.22], y: [0.40, 0.84], z: [0.00, 0.56]
# Seat height: between 0.48 m and 0.56 m (top cushion at 0.53m)

def build_drum_throne():
    mats = get_drum_materials()
    mat_list = [
        mats["vinyl_black"],   # 0 (cushion)
        mats["chrome"],        # 1 (threaded spindle, tripod base)
        mats["rubber_black"],  # 2 (heavy rubber feet)
    ]
    
    bm = bmesh.new()
    base_center = (0.000, 0.620, 0.0)
    
    # 1. Tripod Base (standing on ground up to height z=0.40)
    # Leg radius 0.21m gives extents ~ 0.435m x 0.435m matching planned extents of 0.44m
    add_tripod_stand(bm, base_center=base_center, top_z=0.40, leg_radius=0.21, pole_r=0.016, leg_r=0.01, chrome_mat=1, rubber_mat=2)
    
    # 2. Central threaded height adjustment spindle / collar clamp
    tr_collar = Matrix.Translation((0.000, 0.620, 0.40))
    add_cylinder(bm, 0.024, 0.04, segments=16, transform=tr_collar, mat_idx=1)
    
    # Memory lock clamp wing screw / T-bolt
    add_box(bm, (0.045, 0.012, 0.02), transform=Matrix.Translation((0.024, 0.620, 0.40)), mat_idx=1)
    
    # Threaded rod spindle extending up into seat mount
    tr_spindle = Matrix.Translation((0.000, 0.620, 0.435))
    add_cylinder(bm, 0.013, 0.09, segments=16, transform=tr_spindle, mat_idx=1)
    
    # Steel seat mounting plate under cushion at z=0.455
    tr_plate = Matrix.Translation((0.000, 0.620, 0.455))
    add_box(bm, (0.18, 0.18, 0.012), transform=tr_plate, mat_idx=1)
    
    # 3. Padded Round Vinyl Cushion
    # Diameter 0.35m (radius 0.175m), thickness 0.08m (z from 0.46 to 0.54)
    # Top cushion surface at z = 0.535m (well within acceptance range [0.48m, 0.56m] and max height <= 0.560m)
    cushion_z = 0.495
    tr_cushion = Matrix.Translation((0.000, 0.620, cushion_z))
    add_cylinder(bm, 0.175, 0.07, segments=48, transform=tr_cushion, mat_idx=0)
    
    # Top vinyl beveled / rounded edge
    tr_top_rim = Matrix.Translation((0.000, 0.620, cushion_z + 0.035))
    add_cone(bm, radius1=0.175, radius2=0.165, depth=0.012, segments=48, transform=tr_top_rim, mat_idx=0)
    
    # Bottom rim contour
    tr_bot_rim = Matrix.Translation((0.000, 0.620, cushion_z - 0.035))
    add_cone(bm, radius1=0.165, radius2=0.175, depth=0.012, segments=48, transform=tr_bot_rim, mat_idx=0)
    
    return mesh_from_bmesh("DrumThrone", bm, mat_list)
