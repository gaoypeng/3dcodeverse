"""Seat — horizontal contoured seat slab."""
import math
import bpy
import bmesh
from mathutils import Vector
from parts._common import get_wood_material, obj_from_bmesh

# Plan bbox: center (0.000, -0.010, 0.436) extents (0.450, 0.440, 0.028)
# x in [-0.225, 0.225], y in [-0.230, 0.210], z in [0.422, 0.450]
SEAT_CENTER = (0.000, -0.010, 0.436)
SEAT_EXTENTS = (0.450, 0.440, 0.028)

def build_seat() -> bpy.types.Object:
    """Solid teak wood seat board, 28 mm thick, contoured with rounded corners and gentle dish."""
    bm = bmesh.new()
    
    nx, ny = 24, 24
    w_front = 0.450  # width at front
    # Back width at rear corner: seat reaches back legs at x ≈ ±0.177, y ≈ 0.154.
    # At y = 0.210 (rear edge), width is around 0.350 so it clears the back post or meets nicely.
    # At y ≈ 0.154, seat width is 0.354 (x = ±0.177), where leg center is at ±0.177 with radius 0.016 (inner edge at ±0.161).
    # If seat width at y=0.154 is around 0.320 (x=±0.160), it kisses the leg with 1 mm overlap!
    
    y_front = -0.230
    y_back = 0.210
    z_base = 0.422
    t = 0.028
    
    verts_bottom = []
    verts_top = []
    
    for iy in range(ny + 1):
        v_frac = iy / ny
        y = y_front + v_frac * (y_back - y_front)
        
        # Seat profile in width:
        # At front (y = -0.230): w = 0.450
        # At y = -0.05: w = 0.440
        # At rear (y = 0.154 to 0.210): w = 0.320
        if v_frac < 0.6:
            w = 0.450 - (v_frac / 0.6) * 0.040
        else:
            w = 0.410 - ((v_frac - 0.6) / 0.4) * 0.090
            
        row_b = []
        row_t = []
        for ix in range(nx + 1):
            u_frac = ix / nx
            x = (u_frac - 0.5) * w
            
            r_norm = math.sqrt((x / 0.225)**2 + ((y - (-0.02)) / 0.18)**2)
            dish = 0.0035 * max(0.0, 1.0 - min(1.0, r_norm**2))
            
            zb = z_base
            zt = z_base + t - dish
            zt = min(0.450, max(z_base + 0.022, zt))
            
            vb = bm.verts.new((x, y, zb))
            vt = bm.verts.new((x, y, zt))
            row_b.append(vb)
            row_t.append(vt)
        verts_bottom.append(row_b)
        verts_top.append(row_t)
        
    bm.verts.ensure_lookup_table()
    
    # Faces for bottom (facing -Z) and top (facing +Z)
    for iy in range(ny):
        for ix in range(nx):
            # bottom
            bm.faces.new([
                verts_bottom[iy][ix],
                verts_bottom[iy+1][ix],
                verts_bottom[iy+1][ix+1],
                verts_bottom[iy][ix+1]
            ])
            # top
            bm.faces.new([
                verts_top[iy][ix],
                verts_top[iy][ix+1],
                verts_top[iy+1][ix+1],
                verts_top[iy+1][ix]
            ])
            
    # Side walls
    for ix in range(nx):
        bm.faces.new([
            verts_bottom[0][ix],
            verts_bottom[0][ix+1],
            verts_top[0][ix+1],
            verts_top[0][ix]
        ])
    for ix in range(nx):
        bm.faces.new([
            verts_bottom[ny][ix+1],
            verts_bottom[ny][ix],
            verts_top[ny][ix],
            verts_top[ny][ix+1]
        ])
    for iy in range(ny):
        bm.faces.new([
            verts_bottom[iy+1][0],
            verts_bottom[iy][0],
            verts_top[iy][0],
            verts_top[iy+1][0]
        ])
    for iy in range(ny):
        bm.faces.new([
            verts_bottom[iy][nx],
            verts_bottom[iy+1][nx],
            verts_top[iy+1][nx],
            verts_top[iy][nx]
        ])
        
    bm.faces.ensure_lookup_table()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    obj = obj_from_bmesh("Seat", bm)
    obj.name = "Seat"
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.003
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    obj.data.materials.append(get_wood_material())
    return obj
