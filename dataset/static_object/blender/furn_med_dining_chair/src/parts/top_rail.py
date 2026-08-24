"""TopRail — curved crest rail spanning back posts."""
import math
import bpy
import bmesh
from mathutils import Vector
from parts._common import get_wood_material, obj_from_bmesh

# Plan bbox: center (0.000, 0.210, 0.815) extents (0.420, 0.060, 0.045)
# Min: (-0.210, 0.180, 0.7925), Max: (0.210, 0.240, 0.8375)
TOP_RAIL_CENTER = (0.000, 0.210, 0.815)
TOP_RAIL_EXTENTS = (0.420, 0.060, 0.045)

def build_top_rail() -> bpy.types.Object:
    """Builds TopRail."""
    bm = bmesh.new()
    
    nx = 32
    nz = 8
    
    z_min = 0.7925
    z_max = 0.8375
    
    # BackLegPost top is at x = ±0.187, y = 0.192, radius 0.014.
    # TopRail spans x in [-0.210, 0.210], so post intersects TopRail from beneath (z in [0.7925, 0.840]).
    # To have smooth contact without deep collision, the rail wraps the back post.
    
    verts_grid_front = []
    verts_grid_back = []
    
    for iz in range(nz + 1):
        tz = iz / nz
        z = z_min + tz * (z_max - z_min)
        
        thick_half = 0.011 * math.sin(math.pi * (0.15 + 0.7 * tz))
        thick_half = max(0.007, min(0.011, thick_half))
        
        row_f = []
        row_b = []
        for ix in range(nx + 1):
            tx = ix / nx
            x = -0.210 + tx * 0.420
            # Curve arc: at x=0, y_mid = 0.228. At x=±0.210, y_mid = 0.192
            y_mid = 0.228 - 0.036 * (x / 0.210)**2
            
            dydx = -0.072 * x / (0.210**2)
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
    
    obj = obj_from_bmesh("TopRail", bm)
    obj.name = "TopRail"
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    obj.data.materials.append(get_wood_material())
    return obj
