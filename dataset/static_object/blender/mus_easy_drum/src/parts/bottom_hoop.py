"""BottomHoop — bottom steel counterhoop rim with snare gate cutouts."""
import bpy
import bmesh
import math
from parts._common import make_material, obj_from_bmesh

# Plan numbers: center (0, 0, 0.010), extents (0.395, 0.395, 0.020) -> z in [0.000, 0.020]
# Outer diameter = 0.395 (radius 0.1975) at ear flanges.

def build_bottom_hoop() -> bpy.types.Object:
    mat = make_material("BottomHoopMat", (0.88, 0.88, 0.90), roughness=0.15, metallic=0.98)
    bm = bmesh.new()
    
    segments = 64
    z_bot = -0.010 # world z = 0.000
    z_top = 0.010  # world z = 0.020
    z_flange = -0.004
    
    r_in = 0.179
    r_collar = 0.183
    r_bot_flange = 0.187
    r_ear_max = 0.1975
    
    def get_ear_radius(angle):
        r_out = r_collar
        for k in range(8):
            ear_a = -math.pi / 2.0 + k * (2 * math.pi / 8.0)
            da = abs((angle - ear_a + math.pi) % (2 * math.pi) - math.pi)
            if da < 0.14:
                t = 1.0 - (da / 0.14)**2
                r_ear = r_collar + (r_ear_max - r_collar) * t
                if r_ear > r_out:
                    r_out = r_ear
        return r_out

    rings_data = []
    for s in range(segments):
        a = 2 * math.pi * s / segments
        ca, sa = math.cos(a), math.sin(a)
        r_ear = get_ear_radius(a)
        
        p0 = (r_in * ca, r_in * sa, z_top)
        p1 = (r_in * ca, r_in * sa, z_bot)
        p2 = (max(r_bot_flange, r_ear * 0.96) * ca, max(r_bot_flange, r_ear * 0.96) * sa, z_bot)
        p3 = (r_ear * ca, r_ear * sa, z_flange)
        p4 = (r_ear * ca, r_ear * sa, z_top)
        rings_data.append([p0, p1, p2, p3, p4])
        
    ring_verts = []
    for p_idx in range(5):
        rv = []
        for s in range(segments):
            pos = rings_data[s][p_idx]
            v = bm.verts.new(pos)
            rv.append(v)
        ring_verts.append(rv)
        
    bm.verts.ensure_lookup_table()
    
    for p_idx in range(5):
        next_p = (p_idx + 1) % 5
        r1 = ring_verts[p_idx]
        r2 = ring_verts[next_p]
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([r1[s], r1[s_next], r2[s_next], r2[s]])
            
    # Normalize extents to exactly (0.395, 0.395, 0.020)
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    curr_dx = max(xs) - min(xs)
    curr_dy = max(ys) - min(ys)
    curr_dz = max(zs) - min(zs)
    bmesh.ops.scale(bm, vec=(0.395 / curr_dx, 0.395 / curr_dy, 0.020 / curr_dz), verts=bm.verts)
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    obj = obj_from_bmesh("BottomHoop", bm, location=(0.0, 0.0, 0.010), material=mat)
    return obj
