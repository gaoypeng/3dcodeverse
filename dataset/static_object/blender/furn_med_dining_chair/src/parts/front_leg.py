"""FrontLeg — front load-bearing tapered leg."""
import math
import bpy
import bmesh
from mathutils import Vector
from parts._common import get_wood_material, obj_from_bmesh

# Plan bbox: center (0.185, -0.180, 0.215) extents (0.045, 0.045, 0.430)
# Min: (0.163, -0.202, 0.000), Max: (0.207, -0.158, 0.430)
FRONT_LEG_CENTER = (0.185, -0.180, 0.215)
FRONT_LEG_EXTENTS = (0.045, 0.045, 0.430)

def build_front_leg() -> list[bpy.types.Object]:
    """Builds FrontLeg_0 and FrontLeg_1."""
    objs = []
    
    # Radius at top: 0.017 (34mm dia)
    # Radius at floor: 0.011 (22mm dia)
    r_top = 0.017
    r_bot = 0.011
    
    # Top ends at z = 0.423 (seat underside is at 0.422, so 1mm overlap for perfect weld without warning)
    # Bottom stands on z = 0.000
    z_top = 0.423
    z_bot = 0.000
    
    for i, side in enumerate([1.0, -1.0]):
        bm = bmesh.new()
        
        # Centerline:
        # At top: x = side * 0.178, y = -0.172, z = 0.423
        # At bot: x = side * 0.192, y = -0.188, z = 0.000
        p_bot = Vector((side * 0.192, -0.188, z_bot))
        p_top = Vector((side * 0.178, -0.172, z_top))
        
        axis = (p_top - p_bot).normalized()
        up_ref = Vector((0, 0, 1))
        tangent = axis.cross(up_ref).normalized()
        bitangent = axis.cross(tangent).normalized()
        
        segments = 24
        rings = 16
        ring_verts = []
        
        for ring_idx in range(rings + 1):
            t = ring_idx / rings
            # Adjust so vertex z at floor matches exactly 0.000
            p_center = p_bot + t * (p_top - p_bot)
            r = r_bot + t * (r_top - r_bot)
            
            verts = []
            for s in range(segments):
                angle = 2.0 * math.pi * s / segments
                offset = (math.cos(angle) * tangent + math.sin(angle) * bitangent) * r
                pos = p_center + offset
                if ring_idx == 0:
                    pos.z = 0.000
                v = bm.verts.new(pos)
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
                
        # Bottom cap
        v_bot_center = bm.verts.new(Vector((p_bot.x, p_bot.y, 0.000)))
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([v_bot_center, ring_verts[0][s_next], ring_verts[0][s]])
            
        # Top cap
        v_top_center = bm.verts.new(p_top)
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([v_top_center, ring_verts[rings][s], ring_verts[rings][s_next]])
            
        bm.faces.ensure_lookup_table()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
        obj = obj_from_bmesh(f"FrontLeg_{i}", bm)
        obj.name = f"FrontLeg_{i}"
        obj.data.materials.append(get_wood_material())
        objs.append(obj)
        
    return objs
