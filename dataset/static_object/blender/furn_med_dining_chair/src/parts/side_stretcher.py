"""SideStretcher — structural side rung connecting front and rear legs."""
import math
import bpy
import bmesh
from mathutils import Vector
from parts._common import get_wood_material, obj_from_bmesh

# Plan bbox: center (0.180, -0.005, 0.180) extents (0.020, 0.350, 0.020)
# Min: (0.170, -0.180, 0.170), Max: (0.190, 0.170, 0.190)
SIDE_STRETCHER_CENTER = (0.180, -0.005, 0.180)
SIDE_STRETCHER_EXTENTS = (0.020, 0.350, 0.020)

def build_side_stretcher() -> list[bpy.types.Object]:
    """Builds SideStretcher_0 and SideStretcher_1."""
    objs = []
    
    r = 0.009
    
    # Front leg at z=0.180 has center around (side*0.184, -0.179) with radius ~0.0135.
    # Surface of front leg facing stretcher is at y ≈ -0.1655.
    # Back leg at z=0.180 has center around (side*0.181, +0.167) with radius ~0.0145.
    # Surface of back leg facing stretcher is at y ≈ +0.1525.
    # Stretcher extending from y = -0.167 to y = +0.154 gives ~1.5mm overlap at each end!
    
    for i, side in enumerate([1.0, -1.0]):
        bm = bmesh.new()
        
        p_front = Vector((side * 0.180, -0.167, 0.180))
        p_back = Vector((side * 0.180, 0.154, 0.180))
        
        axis = (p_back - p_front).normalized()
        up_ref = Vector((0, 0, 1))
        tangent = axis.cross(up_ref).normalized()
        bitangent = axis.cross(tangent).normalized()
        
        segments = 16
        rings = 12
        ring_verts = []
        
        for ring_idx in range(rings + 1):
            t = ring_idx / rings
            p_center = p_front + t * (p_back - p_front)
            
            swell = 1.0 + 0.08 * math.sin(math.pi * t)
            current_r = min(0.0095, r * swell)
            
            verts = []
            for s in range(segments):
                angle = 2.0 * math.pi * s / segments
                offset = (math.cos(angle) * tangent + math.sin(angle) * bitangent) * current_r
                v = bm.verts.new(p_center + offset)
                verts.append(v)
            ring_verts.append(verts)
            
        bm.verts.ensure_lookup_table()
        
        # Side faces
        for ring_idx in range(rings):
            for s in range(segments):
                s_next = (s + 1) % segments
                v1 = ring_verts[ring_idx][s]
                v2 = ring_verts[ring_idx][s_next]
                v3 = ring_verts[ring_idx + 1][s_next]
                v4 = ring_verts[ring_idx + 1][s]
                bm.faces.new([v1, v2, v3, v4])
                
        # Front cap
        v_front_c = bm.verts.new(p_front)
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([v_front_c, ring_verts[0][s], ring_verts[0][s_next]])
            
        # Back cap
        v_back_c = bm.verts.new(p_back)
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([v_back_c, ring_verts[rings][s_next], ring_verts[rings][s]])
            
        bm.faces.ensure_lookup_table()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
        obj = obj_from_bmesh(f"SideStretcher_{i}", bm)
        obj.name = f"SideStretcher_{i}"
        obj.data.materials.append(get_wood_material())
        objs.append(obj)
        
    return objs
