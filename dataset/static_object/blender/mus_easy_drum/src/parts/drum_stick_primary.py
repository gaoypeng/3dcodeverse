"""DrumStickPrimary — first drumstick resting diagonally across the top drumhead."""
import bpy
import bmesh
import math
from mathutils import Matrix, Vector
from parts._common import make_material, obj_from_bmesh

# Plan numbers: center (-0.010, -0.005, 0.173), extents (0.380, 0.220, 0.018)
# Bounds: x in [-0.200, 0.180], y in [-0.115, 0.105], z in [0.164, 0.182]
# Length: 0.406m (5A drumstick), butt diameter 14.4mm, neck 8mm, oval wooden tip.
# Extents span: delta_x = 0.380, delta_y = 0.220 -> hypotenuse in xy = sqrt(0.38^2 + 0.22^2) = 0.439m.
# Resting diagonally across the drumhead.

def create_drumstick_mesh() -> bmesh.types.BMesh:
    bm = bmesh.new()
    # Model stick along local X axis, butt at -x, tip at +x, length = 0.406
    # Profile along length (x_local, radius):
    # butt end: x = -0.203, r = 0.0065 (slightly rounded butt)
    # butt rim: x = -0.200, r = 0.0072 (14.4mm dia)
    # handle/shaft: x = 0.000, r = 0.0072
    # shoulder taper start: x = 0.100, r = 0.0072
    # neck: x = 0.185, r = 0.0040 (8mm dia)
    # tip start: x = 0.192, r = 0.0048 (acorn/oval tip)
    # tip apex: x = 0.203, r = 0.0010
    
    profile = [
        (-0.203, 0.0040),
        (-0.200, 0.0072),
        (0.000, 0.0072),
        (0.100, 0.0072),
        (0.180, 0.0040),
        (0.190, 0.0055),
        (0.198, 0.0050),
        (0.203, 0.0010),
    ]
    
    segments = 24
    rings = []
    for x, r in profile:
        ring = []
        for s in range(segments):
            angle = 2 * math.pi * s / segments
            y = r * math.cos(angle)
            z = r * math.sin(angle)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    bm.verts.ensure_lookup_table()
    
    # Butt cap
    butt_center = bm.verts.new((-0.2035, 0, 0))
    for s in range(segments):
        s_next = (s + 1) % segments
        bm.faces.new([butt_center, rings[0][s_next], rings[0][s]])
        
    # Tube segments
    for p in range(len(profile) - 1):
        r1 = rings[p]
        r2 = rings[p + 1]
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([r1[s], r1[s_next], r2[s_next], r2[s]])
            
    # Tip cap
    tip_center = bm.verts.new((0.2035, 0, 0))
    for s in range(segments):
        s_next = (s + 1) % segments
        bm.faces.new([tip_center, rings[-1][s], rings[-1][s_next]])
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm

def build_drum_stick_primary() -> bpy.types.Object:
    mat = make_material("DrumStickPrimaryMat", (0.86, 0.72, 0.52), roughness=0.45, metallic=0.0)
    bm = create_drumstick_mesh()
    
    # Rotate in XY plane to match delta_x = 0.380, delta_y = 0.220
    # Angle theta = atan2(0.220, 0.380) = 0.5246 rad (~30.06 deg)
    theta = math.atan2(0.220, 0.380)
    rot = Matrix.Rotation(theta, 4, 'Z')
    bmesh.ops.transform(bm, matrix=rot, verts=bm.verts)
    
    # Ensure exact extents (0.380, 0.220, 0.018)
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    curr_dx = max(xs) - min(xs)
    curr_dy = max(ys) - min(ys)
    curr_dz = max(zs) - min(zs)
    
    bmesh.ops.scale(bm, vec=(0.380 / curr_dx, 0.220 / curr_dy, 0.018 / curr_dz), verts=bm.verts)
    
    obj = obj_from_bmesh("DrumStickPrimary", bm, location=(-0.010, -0.005, 0.173), material=mat)
    return obj
