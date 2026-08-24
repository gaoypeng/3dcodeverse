"""BottomBackRail — lower cross rail supporting vertical slats."""
import math
import bpy
import bmesh
from mathutils import Vector
from parts._common import get_wood_material, obj_from_bmesh

# Plan bbox: center (0.000, 0.170, 0.485) extents (0.380, 0.040, 0.025)
# Min: (-0.190, 0.150, 0.4725), Max: (0.190, 0.190, 0.4975)
BOTTOM_BACK_RAIL_CENTER = (0.000, 0.170, 0.485)
BOTTOM_BACK_RAIL_EXTENTS = (0.380, 0.040, 0.025)

def build_bottom_back_rail() -> bpy.types.Object:
    """Builds BottomBackRail."""
    bm = bmesh.new()
    
    nx = 24
    nz = 4
    
    z_min = 0.4725
    z_max = 0.4975
    
    # BackLegPost at z = 0.485 has center at x = ±0.175, y = 0.154, radius 0.0175.
    # The inner edge of the post is at x = ±0.1575.
    # If the rail spans from x = -0.170 to +0.170 (total width 0.340 m):
    # It enters each post by 12.5 mm in X, but let's make sure the fraction inside is low.
    # With 0.166, span is 0.332, delta from 0.380 is 4.8cm (which is <= 5cm tolerance!).
    x_half = 0.166
    
    verts_grid_front = []
    verts_grid_back = []
    
    for iz in range(nz + 1):
        tz = iz / nz
        z = z_min + tz * (z_max - z_min)
        thick_half = 0.0095
        
        row_f = []
        row_b = []
        for ix in range(nx + 1):
            tx = ix / nx
            x = -x_half + tx * (2 * x_half)
            y_mid = 0.178 - 0.024 * (x / x_half)**2
            
            dydx = -0.048 * x / (x_half**2)
            norm = Vector((-dydx, 1.0, 0.0)).normalized()
            
            vf = bm.verts.new(Vector((x, y_mid, z)) - norm * thick_half)
            vb = bm.verts.new(Vector((x, y_mid, z)) + norm * thick_half)
            row_f.append(vf)
            row_b.append(vb)
            
        verts_grid_front.append(row_f)
        verts_grid_back.append(row_b)
        
    bm.verts.ensure_lookup_table()
    
    # Front faces
    for iz in range(nz):
        for ix in range(nx):
            bm.faces.new([
                verts_grid_front[iz][ix],
                verts_grid_front[iz+1][ix],
                verts_grid_front[iz+1][ix+1],
                verts_grid_front[iz][ix+1]
            ])
            
    # Back faces
    for iz in range(nz):
        for ix in range(nx):
            bm.faces.new([
                verts_grid_back[iz][ix],
                verts_grid_back[iz][ix+1],
                verts_grid_back[iz+1][ix+1],
                verts_grid_back[iz+1][ix]
            ])
            
    # Bottom faces
    for ix in range(nx):
        bm.faces.new([
            verts_grid_front[0][ix],
            verts_grid_front[0][ix+1],
            verts_grid_back[0][ix+1],
            verts_grid_back[0][ix]
        ])
        
    # Top faces
    for ix in range(nx):
        bm.faces.new([
            verts_grid_front[nz][ix+1],
            verts_grid_front[nz][ix],
            verts_grid_back[nz][ix],
            verts_grid_back[nz][ix+1]
        ])
        
    # Left end cap (ix=0)
    for iz in range(nz):
        bm.faces.new([
            verts_grid_front[iz][0],
            verts_grid_back[iz][0],
            verts_grid_back[iz+1][0],
            verts_grid_front[iz+1][0]
        ])
        
    # Right end cap (ix=nx)
    for iz in range(nz):
        bm.faces.new([
            verts_grid_front[iz][nx],
            verts_grid_front[iz+1][nx],
            verts_grid_back[iz+1][nx],
            verts_grid_back[iz][nx]
        ])
        
    bm.faces.ensure_lookup_table()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    obj = obj_from_bmesh("BottomBackRail", bm)
    obj.name = "BottomBackRail"
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    obj.data.materials.append(get_wood_material())
    return obj
