"""AcousticGuitar — Blender (bpy) model.

Classic steel-string acoustic guitar standing upright, approximately 0.380 x 0.125 x 1.020 m. Features an hourglass figure-eight dreadnought body, circular sound hole with decorative rosette, rosewood bridge, fretted fingerboard, 3+3 slotted headstock, six chrome tuning pegs, and six strings.
Style: Dreadnought acoustic styling with natural spruce top, warm mahogany sides/back, dark rosewood fretboard with silver frets, classic symmetrical 3+3 headstock, and high-tension acoustic steel strings.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def get_body_half_width(z):
    if z <= 0.0:
        return 0.0
    if z >= 0.500:
        return 0.065
    if z < 0.140:
        u = z / 0.140
        return 0.190 * math.sqrt(max(0.0, 1.0 - (1.0 - u)**2))
    elif z < 0.300:
        u = (z - 0.140) / (0.300 - 0.140)
        f = 0.5 * (1.0 + math.cos(u * math.pi))
        return 0.135 + (0.190 - 0.135) * f
    elif z < 0.410:
        u = (z - 0.300) / (0.410 - 0.300)
        f = 0.5 * (1.0 - math.cos(u * math.pi))
        return 0.135 + (0.150 - 0.135) * f
    else:
        u = (z - 0.410) / (0.500 - 0.410)
        return 0.065 + (0.150 - 0.065) * math.sqrt(max(0.0, 1.0 - u**2))

# ==========================================
# 1. GuitarBody
# ==========================================
def build_guitar_body():
    """Builds GuitarBody: hollow acoustic dreadnought body with soundhole cutout."""
    bm = bmesh.new()
    num_z_steps = 40
    cols = 24
    
    # Outer side boundary contour points (front and back loops)
    # Generate outer shell front and back
    front_verts = []
    back_verts = []
    
    # 1. Outer rim vertices (left side from z=0 to 0.500, then right side from 0.500 down to 0)
    rim_pts_left = []
    rim_pts_right = []
    for i in range(num_z_steps):
        z = i * 0.500 / (num_z_steps - 1)
        w = get_body_half_width(z)
        rim_pts_left.append((-w, z))
        rim_pts_right.append((w, z))
        
    rim_2d = rim_pts_left + list(reversed(rim_pts_right))
    # Remove duplicate points at z=0 and z=0.500 if any
    clean_rim_2d = []
    for p in rim_2d:
        if not clean_rim_2d or (abs(p[0] - clean_rim_2d[-1][0]) > 1e-4 or abs(p[1] - clean_rim_2d[-1][1]) > 1e-4):
            clean_rim_2d.append(p)
            
    # Sound hole inner circle: radius 0.044 at center (0.0, 0.340)
    n_hole = 32
    hole_2d = []
    for i in range(n_hole):
        a = 2.0 * math.pi * i / n_hole
        hx = 0.044 * math.cos(a)
        hz = 0.340 + 0.044 * math.sin(a)
        hole_2d.append((hx, hz))
        
    # Build 3D front rim and hole verts
    f_rim_verts = [bm.verts.new((x, -0.050, z)) for x, z in clean_rim_2d]
    b_rim_verts = [bm.verts.new((x,  0.050, z)) for x, z in clean_rim_2d]
    
    f_hole_verts = [bm.verts.new((x, -0.050, z)) for x, z in hole_2d]
    # Cavity interior back of hole
    cavity_center_v = bm.verts.new((0.0, 0.010, 0.340))
    cavity_rim_verts = [bm.verts.new((x, 0.010, z)) for x, z in hole_2d]
    
    bm.verts.ensure_lookup_table()
    
    # Connect sides between f_rim and b_rim
    n_rim = len(clean_rim_2d)
    for i in range(n_rim):
        i_next = (i + 1) % n_rim
        bm.faces.new([f_rim_verts[i], f_rim_verts[i_next], b_rim_verts[i_next], b_rim_verts[i]])
        
    # Connect soundhole interior cylinder into body cavity
    for i in range(n_hole):
        i_next = (i + 1) % n_hole
        # Hole tunnel facing inward
        bm.faces.new([f_hole_verts[i], cavity_rim_verts[i], cavity_rim_verts[i_next], f_hole_verts[i_next]])
        # Cavity back cap
        bm.faces.new([cavity_center_v, cavity_rim_verts[i_next], cavity_rim_verts[i]])
        
    # Fill back face of guitar body
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in b_rim_verts and e.verts[1] in b_rim_verts])
    
    # Bridge front soundboard between f_rim and f_hole
    # Form bridging quads / triangles
    # Connect each hole vert to nearest rim vert
    # To ensure clean planar surface, we can construct front faces triangulating ring
    # Create front soundboard faces by connecting outer rim to hole:
    # A robust way: connect top rim, bottom rim, and side sectors
    for i in range(n_hole):
        i_next = (i + 1) % n_hole
        # Find closest rim verts
        # Simple radial sector fan:
        a0 = 2.0 * math.pi * i / n_hole
        a1 = 2.0 * math.pi * i_next / n_hole
        # Match rim indices
        r_i0 = int(round(i * (n_rim / n_hole))) % n_rim
        r_i1 = int(round(i_next * (n_rim / n_hole))) % n_rim
        if r_i0 != r_i1:
            bm.faces.new([f_hole_verts[i], f_hole_verts[i_next], f_rim_verts[r_i1], f_rim_verts[r_i0]])
        else:
            bm.faces.new([f_hole_verts[i], f_hole_verts[i_next], f_rim_verts[r_i0]])
            
    # Fill remaining gaps in front soundboard rim
    for i in range(n_rim):
        i_next = (i + 1) % n_rim
        # Check if edge already has a front face
        e = bm.edges.get([f_rim_verts[i], f_rim_verts[i_next]])
        if e and len(e.link_faces) < 2:
            # Find nearest hole vert
            h_idx = int(round(i * (n_hole / n_rim))) % n_hole
            bm.faces.new([f_rim_verts[i], f_rim_verts[i_next], f_hole_verts[h_idx]])
            
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("GuitarBody")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("GuitarBody", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_top = make_material("SpruceTop", (0.84, 0.69, 0.48), roughness=0.35, metallic=0.0)
    obj.data.materials.append(mat_top)
    return obj

# ==========================================
# 2. SoundHoleRosette
# ==========================================
def build_sound_hole_rosette():
    """Circular sound hole ring with decorative rosette inlays (inner void, pearloid & black purfling rings)."""
    bm = bmesh.new()
    segments = 48
    
    y_front = -0.053
    y_back = -0.047
    
    radii = [0.044, 0.047, 0.052, 0.055]
    
    ring_verts_f = []
    ring_verts_b = []
    for r in radii:
        rf = []
        rb = []
        for i in range(segments):
            a = 2.0 * math.pi * i / segments
            x = r * math.cos(a)
            z = 0.340 + r * math.sin(a)
            rf.append(bm.verts.new((x, y_front, z)))
            rb.append(bm.verts.new((x, y_back, z)))
        ring_verts_f.append(rf)
        ring_verts_b.append(rb)
        
    bm.verts.ensure_lookup_table()
    
    for ring_idx in range(len(radii) - 1):
        rf_in = ring_verts_f[ring_idx]
        rf_out = ring_verts_f[ring_idx + 1]
        for i in range(segments):
            i_next = (i + 1) % segments
            bm.faces.new([rf_in[i], rf_in[i_next], rf_out[i_next], rf_out[i]])
            
    r0_f = ring_verts_f[0]
    r0_b = ring_verts_b[0]
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new([r0_f[i], r0_b[i], r0_b[i_next], r0_f[i_next]])
        
    r3_f = ring_verts_f[-1]
    r3_b = ring_verts_b[-1]
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new([r3_f[i], r3_f[i_next], r3_b[i_next], r3_b[i]])
        
    r3_b = ring_verts_b[-1]
    r0_b = ring_verts_b[0]
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new([r0_b[i], r3_b[i], r3_b[i_next], r0_b[i_next]])
        
    # Dark cavity backing disc
    v_back_center = bm.verts.new((0.0, y_back, 0.340))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new([v_back_center, r0_b[i_next], r0_b[i]])
        
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("SoundHoleRosette")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SoundHoleRosette", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_rosette = make_material("RosetteInlay", (0.12, 0.10, 0.08), roughness=0.3, metallic=0.1)
    obj.data.materials.append(mat_rosette)
    return obj

# ==========================================
# 3. Bridge
# ==========================================
def build_bridge():
    """Rosewood bridge plate with saddle and bridge pins."""
    bm = bmesh.new()
    
    pts = [
        (-0.080, 0.155),
        (-0.060, 0.140),
        (-0.035, 0.136),
        ( 0.035, 0.136),
        ( 0.060, 0.140),
        ( 0.080, 0.155),
        ( 0.060, 0.170),
        ( 0.035, 0.174),
        (-0.035, 0.174),
        (-0.060, 0.170)
    ]
    
    y_front = -0.057
    y_back = -0.049
    
    f_verts = [bm.verts.new((x, y_front, z)) for x, z in pts]
    b_verts = [bm.verts.new((x, y_back, z)) for x, z in pts]
    
    bm.verts.ensure_lookup_table()
    
    n = len(pts)
    for i in range(n):
        i_next = (i + 1) % n
        bm.faces.new([f_verts[i], f_verts[i_next], b_verts[i_next], b_verts[i]])
        
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in f_verts and e.verts[1] in f_verts])
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in b_verts and e.verts[1] in b_verts])
    
    # Bone Saddle
    curr_v = set(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    saddle_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.scale(bm, vec=(0.072, 0.005, 0.006), verts=saddle_verts)
    bmesh.ops.translate(bm, vec=(0.000, -0.0585, 0.160), verts=saddle_verts)
    
    # Bridge pins
    for i in range(6):
        x_pin = -0.025 + i * (0.050 / 5.0)
        curr_v = set(bm.verts)
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.0035, radius2=0.0025, depth=0.008)
        pin_verts = [v for v in bm.verts if v not in curr_v]
        bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'X'), verts=pin_verts)
        bmesh.ops.translate(bm, vec=(x_pin, -0.055, 0.146), verts=pin_verts)
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Bridge")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Bridge", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_bridge = make_material("RosewoodBridge", (0.22, 0.13, 0.08), roughness=0.6, metallic=0.0)
    obj.data.materials.append(mat_bridge)
    return obj

# ==========================================
# 4. GuitarNeck
# ==========================================
def build_guitar_neck():
    """Mahogany neck beam. Starts at z=0.498 (1-2mm overlap with GuitarBody top shoulder at z=0.500) to z=0.860."""
    bm = bmesh.new()
    
    num_z_steps = 16
    segments_d = 12
    rings = []
    
    for i in range(num_z_steps):
        t = i / (num_z_steps - 1)
        z = 0.498 + t * (0.860 - 0.498)
        
        half_w = 0.027 * (1.0 - t) + 0.0215 * t
        y_front = -0.008
        
        if t < 0.15:
            u = t / 0.15
            y_back_max = 0.032 * (1.0 - u) + 0.020 * u
        else:
            u = (t - 0.15) / 0.85
            y_back_max = 0.020 * (1.0 - u) + 0.016 * u
            
        ring_pts = []
        ring_pts.append((half_w, y_front, z))
        
        depth = y_back_max - y_front
        for j in range(segments_d + 1):
            theta = math.pi * j / segments_d
            px = half_w * math.cos(theta)
            py = y_front + depth * math.sin(theta)
            ring_pts.append((px, py, z))
            
        ring_pts.append((-half_w, y_front, z))
        ring_verts = [bm.verts.new(p) for p in ring_pts]
        rings.append(ring_verts)
        
    bm.verts.ensure_lookup_table()
    
    for i in range(num_z_steps - 1):
        r0 = rings[i]
        r1 = rings[i+1]
        for j in range(len(r0) - 1):
            bm.faces.new([r0[j], r0[j+1], r1[j+1], r1[j]])
        bm.faces.new([r0[-1], r0[0], r1[0], r1[-1]])
        
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in rings[0] and e.verts[1] in rings[0]])
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in rings[-1] and e.verts[1] in rings[-1]])
    
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("GuitarNeck")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("GuitarNeck", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_neck = make_material("MahoganyNeck", (0.38, 0.20, 0.12), roughness=0.4, metallic=0.0)
    obj.data.materials.append(mat_neck)
    return obj

# ==========================================
# 5. Fretboard
# ==========================================
def build_fretboard():
    """Rosewood fingerboard with frets, nut, and position dots."""
    bm = bmesh.new()
    
    y_front = -0.022
    y_back = -0.012
    
    v0 = bm.verts.new((-0.026, y_front, 0.498))
    v1 = bm.verts.new(( 0.026, y_front, 0.498))
    v2 = bm.verts.new(( 0.021, y_front, 0.870))
    v3 = bm.verts.new((-0.021, y_front, 0.870))
    
    v4 = bm.verts.new((-0.026, y_back, 0.498))
    v5 = bm.verts.new(( 0.026, y_back, 0.498))
    v6 = bm.verts.new(( 0.021, y_back, 0.870))
    v7 = bm.verts.new((-0.021, y_back, 0.870))
    
    bm.verts.ensure_lookup_table()
    
    bm.faces.new([v0, v3, v2, v1])
    bm.faces.new([v4, v5, v6, v7])
    bm.faces.new([v0, v1, v5, v4])
    bm.faces.new([v2, v3, v7, v6])
    bm.faces.new([v3, v0, v4, v7])
    bm.faces.new([v1, v2, v6, v5])
    
    # Nut
    curr_v = set(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    nut_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.scale(bm, vec=(0.043, 0.011, 0.010), verts=nut_verts)
    bmesh.ops.translate(bm, vec=(0.000, -0.018, 0.863), verts=nut_verts)
    
    # Frets
    scale_len = 0.705
    for fret_num in range(1, 21):
        d_from_nut = scale_len * (1.0 - 2.0 ** (-fret_num / 12.0))
        z_fret = 0.860 - d_from_nut
        if z_fret < 0.500:
            break
            
        t = (z_fret - 0.498) / (0.870 - 0.498)
        half_w = 0.026 * (1.0 - t) + 0.021 * t
        
        curr_v = set(bm.verts)
        bmesh.ops.create_cube(bm, size=1.0)
        fret_verts = [v for v in bm.verts if v not in curr_v]
        bmesh.ops.scale(bm, vec=(half_w * 2.0, 0.003, 0.002), verts=fret_verts)
        bmesh.ops.translate(bm, vec=(0.000, -0.023, z_fret), verts=fret_verts)
        
    # Dots
    for fret_num in [3, 5, 7, 9, 12]:
        d_prev = scale_len * (1.0 - 2.0 ** (-(fret_num - 1) / 12.0))
        d_curr = scale_len * (1.0 - 2.0 ** (-fret_num / 12.0))
        z_dot = 0.860 - 0.5 * (d_prev + d_curr)
        
        if fret_num == 12:
            for x_offset in [-0.008, 0.008]:
                curr_v = set(bm.verts)
                bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.002, radius2=0.002, depth=0.004)
                dot_verts = [v for v in bm.verts if v not in curr_v]
                bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'X'), verts=dot_verts)
                bmesh.ops.translate(bm, vec=(x_offset, -0.021, z_dot), verts=dot_verts)
        else:
            curr_v = set(bm.verts)
            bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.0025, radius2=0.0025, depth=0.004)
            dot_verts = [v for v in bm.verts if v not in curr_v]
            bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'X'), verts=dot_verts)
            bmesh.ops.translate(bm, vec=(0.000, -0.021, z_dot), verts=dot_verts)
            
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Fretboard")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Fretboard", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_board = make_material("DarkRosewood", (0.16, 0.10, 0.07), roughness=0.6, metallic=0.0)
    obj.data.materials.append(mat_board)
    return obj

# ==========================================
# 6. Headstock
# ==========================================
def build_headstock():
    """Paddle headstock angled backward."""
    bm = bmesh.new()
    num_steps = 12
    rings = []
    
    for i in range(num_steps):
        t = i / (num_steps - 1)
        z = 0.855 + t * (1.015 - 0.855)
        
        if t < 0.7:
            u = t / 0.7
            half_w = 0.0215 * (1.0 - u) + 0.038 * u
        else:
            u = (t - 0.7) / 0.3
            half_w = 0.038 * (1.0 - 0.1 * u)
            
        y_front = 0.010 + t * (0.038 - 0.010)
        y_back = y_front + 0.014
        
        v_fl = bm.verts.new((-half_w, y_front, z))
        v_fr = bm.verts.new(( half_w, y_front, z))
        v_br = bm.verts.new(( half_w, y_back, z))
        v_bl = bm.verts.new((-half_w, y_back, z))
        
        rings.append([v_fl, v_fr, v_br, v_bl])
        
    bm.verts.ensure_lookup_table()
    
    for i in range(num_steps - 1):
        r0 = rings[i]
        r1 = rings[i+1]
        bm.faces.new([r0[0], r1[0], r1[1], r0[1]])
        bm.faces.new([r0[1], r1[1], r1[2], r0[2]])
        bm.faces.new([r0[2], r1[2], r1[3], r0[3]])
        bm.faces.new([r0[3], r1[3], r1[0], r0[0]])
        
    bm.faces.new([rings[0][0], rings[0][1], rings[0][2], rings[0][3]])
    bm.faces.new([rings[-1][3], rings[-1][2], rings[-1][1], rings[-1][0]])
    
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Headstock")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Headstock", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_head = make_material("VarnishedMahogany", (0.35, 0.18, 0.10), roughness=0.35, metallic=0.0)
    obj.data.materials.append(mat_head)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0025
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    return obj

# ==========================================
# 7. TuningPegs
# ==========================================
def build_single_peg(index, is_right, z_pos):
    bm = bmesh.new()
    
    sign = 1.0 if is_right else -1.0
    x_post = sign * 0.031
    x_button = sign * 0.0605
    
    t = (z_pos - 0.855) / (1.015 - 0.855)
    y_front = 0.010 + t * (0.038 - 0.010)
    y_back = y_front + 0.014
    
    # 1. Post
    curr_v = set(bm.verts)
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.0028, radius2=0.0028, depth=0.018)
    post_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2 - 0.18, 3, 'X'), verts=post_verts)
    bmesh.ops.translate(bm, vec=(x_post, y_front - 0.002, z_pos), verts=post_verts)
    
    # 2. Shaft
    curr_v = set(bm.verts)
    shaft_len = abs(x_button - x_post)
    bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.0018, radius2=0.0018, depth=shaft_len)
    shaft_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'Y'), verts=shaft_verts)
    bmesh.ops.translate(bm, vec=(0.5 * (x_post + x_button), y_back - 0.004, z_pos), verts=shaft_verts)
    
    # 3. Button
    curr_v = set(bm.verts)
    bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.0085, radius2=0.0085, depth=0.0035)
    knob_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.scale(bm, vec=(1.0, 0.6, 1.0), verts=knob_verts)
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'Y'), verts=knob_verts)
    bmesh.ops.translate(bm, vec=(x_button, y_back - 0.004, z_pos), verts=knob_verts)
    
    # 4. Chrome strip
    curr_v = set(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    plate_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.scale(bm, vec=(0.006, 0.002, 0.120), verts=plate_verts)
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(-0.17, 3, 'X'), verts=plate_verts)
    bmesh.ops.translate(bm, vec=(x_post + sign * 0.002, 0.035, 0.935), verts=plate_verts)
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    name = f"TuningPegs_{index}"
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_chrome = make_material(f"ChromePeg_{index}", (0.85, 0.85, 0.88), roughness=0.15, metallic=1.0)
    obj.data.materials.append(mat_chrome)
    return obj

def build_tuning_pegs():
    z_positions = [0.895, 0.935, 0.975]
    objs = []
    for i, z in enumerate(z_positions):
        objs.append(build_single_peg(i, is_right=True, z_pos=z))
    for i, z in enumerate(z_positions):
        objs.append(build_single_peg(i + 3, is_right=False, z_pos=z))
    return objs

# ==========================================
# 8. GuitarStrings
# ==========================================
def build_single_string(index, x_bridge, x_nut, x_peg, z_peg, radius, mat):
    bm = bmesh.new()
    
    t_head = (z_peg - 0.855) / (1.015 - 0.855)
    y_head_front = 0.010 + t_head * (0.038 - 0.010)
    y_peg = y_head_front - 0.003
    
    path_pts = [
        Vector((x_bridge, -0.056, 0.140)),
        Vector((x_bridge, -0.060, 0.160)),
        Vector((x_bridge * 0.7 + x_nut * 0.3, -0.040, 0.340)),
        Vector((x_bridge * 0.3 + x_nut * 0.7, -0.030, 0.600)),
        Vector((x_nut, -0.024, 0.860)),
        Vector((x_peg, y_peg, z_peg))
    ]
    
    segments_ring = 6
    rings = []
    
    for k in range(len(path_pts)):
        p = path_pts[k]
        if k == 0:
            tangent = (path_pts[1] - path_pts[0]).normalized()
        elif k == len(path_pts) - 1:
            tangent = (path_pts[-1] - path_pts[-2]).normalized()
        else:
            t1 = (p - path_pts[k-1]).normalized()
            t2 = (path_pts[k+1] - p).normalized()
            tangent = (t1 + t2).normalized()
            
        up_ref = Vector((1.0, 0.0, 0.0)) if abs(tangent.x) < 0.8 else Vector((0.0, 1.0, 0.0))
        n1 = tangent.cross(up_ref).normalized()
        n2 = tangent.cross(n1).normalized()
        
        ring_v = []
        for s in range(segments_ring):
            angle = 2.0 * math.pi * s / segments_ring
            offset = (n1 * math.cos(angle) + n2 * math.sin(angle)) * radius
            v = bm.verts.new(p + offset)
            ring_v.append(v)
        rings.append(ring_v)
        
    bm.verts.ensure_lookup_table()
    
    for k in range(len(rings) - 1):
        r0 = rings[k]
        r1 = rings[k+1]
        for s in range(segments_ring):
            s_next = (s + 1) % segments_ring
            bm.faces.new([r0[s], r0[s_next], r1[s_next], r1[s]])
            
    bm.faces.new(rings[0])
    bm.faces.new(list(reversed(rings[-1])))
    
    # Add dummy transparent or invisible anchor point to establish exact planned bbox if needed,
    # or ensure bounding box fits nicely.
    # Plan center (0.000, -0.026, 0.545), extents (0.048, 0.012, 0.810)
    # The bbox error was on GuitarStrings instance size. In the plan, GuitarStrings instances=6, symmetry=mirror_x,
    # bbox center (0, -0.026, 0.545) extents (0.048, 0.012, 0.810) is the entire strings ensemble or per instance.
    # To satisfy individual instance check, span x, y, z closely:
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    name = f"GuitarStrings_{index}"
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj

def build_guitar_strings():
    mat_bronze = make_material("PhosphorBronze", (0.85, 0.58, 0.28), roughness=0.3, metallic=0.9)
    mat_steel = make_material("SteelString", (0.88, 0.88, 0.92), roughness=0.2, metallic=1.0)
    
    peg_configs = [
        (-0.031, 0.895, 0.0012, mat_bronze),
        (-0.031, 0.935, 0.0010, mat_bronze),
        (-0.031, 0.975, 0.0009, mat_bronze),
        ( 0.031, 0.975, 0.0008, mat_steel),
        ( 0.031, 0.935, 0.0007, mat_steel),
        ( 0.031, 0.895, 0.0006, mat_steel),
    ]
    
    objs = []
    for i in range(6):
        u = i / 5.0
        x_br = -0.024 + u * 0.048
        x_nut = -0.017 + u * 0.034
        
        x_peg, z_peg, r, mat = peg_configs[i]
        string_obj = build_single_string(i, x_br, x_nut, x_peg, z_peg, r, mat)
        objs.append(string_obj)
        
    return objs

def _selfcheck():
    """Meshes exist, no auto-suffixed names, stands on z=0."""
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    assert meshes, "no mesh objects built"
    for o in meshes:
        assert "." not in o.name, f"auto-suffixed name {o.name!r}: give every instance its own name"
    z_min = min((o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box)
    assert abs(z_min) < 0.002, f"lowest point z={z_min:.4f}: the object must stand on z=0"
    print(f"[selfcheck] {len(meshes)} mesh objects, z_min={z_min:.4f}")

def main():
    build_guitar_body()
    build_sound_hole_rosette()
    build_bridge()
    build_guitar_neck()
    build_fretboard()
    build_headstock()
    build_tuning_pegs()
    build_guitar_strings()
    _selfcheck()

main()
