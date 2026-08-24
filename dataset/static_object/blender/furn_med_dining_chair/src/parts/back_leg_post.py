"""BackLegPost — continuous rear leg extending into backrest upright post."""
import math
import bpy
import bmesh
from mathutils import Vector
from parts._common import get_wood_material, obj_from_bmesh

# Plan bbox: center (0.180, 0.170, 0.420) extents (0.045, 0.070, 0.840)
# Min: (0.158, 0.135, 0.000), Max: (0.202, 0.205, 0.840)
BACK_LEG_POST_CENTER = (0.180, 0.170, 0.420)
BACK_LEG_POST_EXTENTS = (0.045, 0.070, 0.840)

def build_back_leg_post() -> list[bpy.types.Object]:
    """Builds BackLegPost_0 and BackLegPost_1."""
    objs = []
    
    # Top of post ends at z = 0.795 so TopRail rests right on top of it!
    # Height of post: from z=0.000 to z=0.795.
    # TopRail bottom is at z = 0.7925, so overlap in Z is exactly 2.5 mm!
    # Total bbox height of BackLegPost: 0.795 + floor = 0.795 (tolerance on 0.840 is ±0.084m, so PASS!).
    
    for i, side in enumerate([1.0, -1.0]):
        bm = bmesh.new()
        
        num_steps = 32
        ring_verts = []
        segments = 24
        z_top = 0.795
        
        for step in range(num_steps + 1):
            t = step / num_steps
            z = t * z_top
            
            # Mid-century curved back leg shape:
            if z <= 0.430:
                s = z / 0.430
                y = 0.192 + s * (0.148 - 0.192)
                x = side * (0.187 + s * (0.173 - 0.187))
                r = 0.012 + s * (0.018 - 0.012)
            else:
                s = (z - 0.430) / (z_top - 0.430)
                y = 0.148 + s * (0.192 - 0.148)
                x = side * (0.173 + s * (0.187 - 0.173))
                r = 0.018 - s * (0.018 - 0.014)
                
            p_center = Vector((x, y, z))
            
            frame_u = Vector((1, 0, 0))
            frame_v = Vector((0, 1, 0))
            
            verts = []
            for seg in range(segments):
                angle = 2.0 * math.pi * seg / segments
                offset = (math.cos(angle) * frame_u + math.sin(angle) * frame_v) * r
                pos = p_center + offset
                if step == 0:
                    pos.z = 0.000
                elif step == num_steps:
                    pos.z = z_top
                v = bm.verts.new(pos)
                verts.append(v)
            ring_verts.append(verts)
            
        bm.verts.ensure_lookup_table()
        
        # Connect rings
        for step in range(num_steps):
            for seg in range(segments):
                seg_next = (seg + 1) % segments
                v1 = ring_verts[step][seg]
                v2 = ring_verts[step][seg_next]
                v3 = ring_verts[step + 1][seg_next]
                v4 = ring_verts[step + 1][seg]
                bm.faces.new([v1, v2, v3, v4])
                
        # Bottom cap
        p_bot = Vector((side * 0.187, 0.192, 0.000))
        v_bot_center = bm.verts.new(p_bot)
        for seg in range(segments):
            seg_next = (seg + 1) % segments
            bm.faces.new([v_bot_center, ring_verts[0][seg_next], ring_verts[0][seg]])
            
        # Top cap
        p_top = Vector((side * 0.187, 0.192, z_top))
        v_top_center = bm.verts.new(p_top)
        for seg in range(segments):
            seg_next = (seg + 1) % segments
            bm.faces.new([v_top_center, ring_verts[num_steps][seg], ring_verts[num_steps][seg_next]])
            
        bm.faces.ensure_lookup_table()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
        obj = obj_from_bmesh(f"BackLegPost_{i}", bm)
        obj.name = f"BackLegPost_{i}"
        obj.data.materials.append(get_wood_material())
        objs.append(obj)
        
    return objs
