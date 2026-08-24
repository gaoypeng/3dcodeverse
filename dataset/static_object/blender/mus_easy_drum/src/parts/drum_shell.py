"""DrumShell — main acoustic cylindrical resonant body."""
import bpy
import bmesh
import math
from parts._common import make_material, obj_from_bmesh

# Plan numbers: center (0, 0, 0.085), extents (0.356, 0.356, 0.140)
# Outer diameter = 0.356 -> radius = 0.178. Height = 0.140 (z from 0.015 to 0.155)

def build_drum_shell() -> bpy.types.Object:
    mat = make_material("DrumShellMat", (0.85, 0.85, 0.88), roughness=0.18, metallic=0.95)
    bm = bmesh.new()
    
    r_outer = 0.178
    r_inner = 0.1755 # 2.5mm shell thickness
    h = 0.140
    segments = 64
    
    # Create hollow cylinder with bead in the middle
    # Center z is 0.085. Bottom z = 0.015, Top z = 0.155
    # Let's create cylinder vertices along Z in local space (-h/2 to +h/2)
    # Profile points in (r, z):
    # inner wall: (r_inner, -h/2) to (r_inner, h/2)
    # top rim / bearing edge: (r_inner, h/2) -> (r_outer, h/2)
    # outer wall: with center bead at z=0 (r = r_outer + 0.0015 bead)
    # bottom rim: (r_outer, -h/2) -> (r_inner, -h/2)
    
    # We can create concentric rings and stitch them
    z_bot = -h / 2
    z_top = h / 2
    z_mid = 0.0
    
    # 5 height rings for outer wall: bot, mid-0.015, mid, mid+0.015, top
    # Bead radius stays <= r_outer (so max outer diameter is strictly 0.356)
    r_bead = r_outer
    r_shell_body = r_outer - 0.0012
    
    profile = [
        # Outer surface (from top to bottom)
        (r_shell_body, z_top),
        (r_shell_body, 0.018),
        (r_bead, 0.006),
        (r_bead, -0.006),
        (r_shell_body, -0.018),
        (r_shell_body, z_bot),
        # Inner surface (bottom to top)
        (r_inner, z_bot),
        (r_inner, z_top),
    ]
    
    # Generate mesh by revolving profile
    ring_verts = []
    for r, z in profile:
        ring = []
        for s in range(segments):
            angle = 2 * math.pi * s / segments
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            v = bm.verts.new((x, y, z))
            ring.append(v)
        ring_verts.append(ring)
    
    bm.verts.ensure_lookup_table()
    
    # Connect rings into quads
    for p in range(len(profile)):
        p_next = (p + 1) % len(profile)
        r1 = ring_verts[p]
        r2 = ring_verts[p_next]
        for s in range(segments):
            s_next = (s + 1) % segments
            # Create face (r1[s], r1[s_next], r2[s_next], r2[s])
            bm.faces.new([r1[s], r1[s_next], r2[s_next], r2[s]])
            
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    obj = obj_from_bmesh("DrumShell", bm, location=(0.0, 0.0, 0.085), material=mat)
    return obj
