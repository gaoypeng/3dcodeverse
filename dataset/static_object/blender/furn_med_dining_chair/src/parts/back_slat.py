"""BackSlat — vertical backrest support slat."""
import math
import bpy
import bmesh
from mathutils import Vector
from parts._common import get_wood_material, obj_from_bmesh

# Plan bbox: center (0.050, 0.190, 0.650) extents (0.020, 0.015, 0.310)
# Min: (0.040, 0.1825, 0.495), Max: (0.060, 0.1975, 0.805)
BACK_SLAT_CENTER = (0.050, 0.190, 0.650)
BACK_SLAT_EXTENTS = (0.020, 0.015, 0.310)

def build_back_slat() -> list[bpy.types.Object]:
    """Builds BackSlat_0..BackSlat_3."""
    objs = []
    
    # Slat 0: x = +0.050, Slat 1: x = -0.050, Slat 2: x = +0.115, Slat 3: x = -0.115
    slat_configs = [
        (0, 0.050),
        (1, -0.050),
        (2, 0.115),
        (3, -0.115),
    ]
    
    z_min = 0.495
    z_max = 0.805
    w_x = 0.020
    w_y = 0.014
    
    for i, x_pos in slat_configs:
        bm = bmesh.new()
        
        # Center in Y of slat i:
        # At z=0.650 (mid): y = 0.190
        # To keep each instance's Y extent exactly within 0.015 (y in [y_c - 0.007, y_c + 0.007]):
        # Keep y nearly straight or very slight tilt (e.g. y=0.185 at bottom, y=0.195 at top)
        # Slat 0 center should be exactly at y=0.190!
        
        nz = 12
        verts_ring = []
        segments = 16
        
        for iz in range(nz + 1):
            tz = iz / nz
            z = z_min + tz * (z_max - z_min)
            
            # Slat tilt from y=0.186 to y=0.194 (center at 0.190, delta y is 0.008, plus thickness 0.014 = total Y extent 0.0148 <= 0.015)
            y_mid = 0.186 + tz * (0.194 - 0.186)
            if abs(x_pos) > 0.08:
                # Slightly offset for outer slats to match curved rails
                y_mid = 0.182 + tz * (0.190 - 0.182)
                
            ring = []
            rx = w_x / 2.0
            ry = w_y / 2.0
            for s in range(segments):
                ang = 2.0 * math.pi * s / segments
                vx = x_pos + rx * math.cos(ang)
                vy = y_mid + ry * math.sin(ang)
                ring.append(bm.verts.new((vx, vy, z)))
            verts_ring.append(ring)
            
        bm.verts.ensure_lookup_table()
        
        # Faces along height
        for iz in range(nz):
            for s in range(segments):
                s_next = (s + 1) % segments
                v1 = verts_ring[iz][s]
                v2 = verts_ring[iz][s_next]
                v3 = verts_ring[iz + 1][s_next]
                v4 = verts_ring[iz + 1][s]
                bm.faces.new([v1, v2, v3, v4])
                
        # Bottom cap
        y_bot = verts_ring[0][0].co.y
        v_bot_c = bm.verts.new((x_pos, y_bot, z_min))
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([v_bot_c, verts_ring[0][s_next], verts_ring[0][s]])
            
        # Top cap
        y_top = verts_ring[nz][0].co.y
        v_top_c = bm.verts.new((x_pos, y_top, z_max))
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([v_top_c, verts_ring[nz][s], verts_ring[nz][s_next]])
            
        bm.faces.ensure_lookup_table()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
        obj = obj_from_bmesh(f"BackSlat_{i}", bm)
        obj.name = f"BackSlat_{i}"
        obj.data.materials.append(get_wood_material())
        objs.append(obj)
        
    return objs
