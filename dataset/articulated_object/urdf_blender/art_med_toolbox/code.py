"""MetalToolbox — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

Detailed sheet metal toolbox:
- body: open storage carcass with rolled top rim, stamped bottom feet, embossed ribs, rear hinge knuckles, front catch plate.
- lid: peaked/cambered lid with perimeter lip overlapping body, rear hinge knuckles, top handle brackets, latch hinge mount.
- handle: wire bail handle with contoured black rubber grip and pivot pins.
- latch: front toggle clasp hinged at top, engaging catch plate.
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear default scene objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def create_material(name, base_color, metallic=0.0, roughness=0.5):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = base_color
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return mat

# Materials
mat_red_metal = create_material("RedSteel", (0.75, 0.08, 0.05, 1.0), metallic=0.3, roughness=0.35)
mat_chrome = create_material("NickelPlated", (0.85, 0.85, 0.88, 1.0), metallic=0.9, roughness=0.2)
mat_rubber = create_material("BlackRubber", (0.05, 0.05, 0.05, 1.0), metallic=0.0, roughness=0.8)

# -------------------------------------------------------------
# 1. BODY LINK
# Plan bbox: centre (0.000, 0.000, 0.080), extents (0.440, 0.200, 0.160)
# Height: z in [0.000, 0.158], y in [-0.100, 0.100], x in [-0.220, 0.220]
# -------------------------------------------------------------
def build_body():
    bm = bmesh.new()
    
    wall_t = 0.004
    w_out, d_out, h_out = 0.440, 0.200, 0.158  # 158mm height so lid rests at 0.160 with 2mm clearance
    
    # Outer box vertices
    v1 = bm.verts.new((-w_out/2, -d_out/2, 0.0))
    v2 = bm.verts.new(( w_out/2, -d_out/2, 0.0))
    v3 = bm.verts.new(( w_out/2,  d_out/2, 0.0))
    v4 = bm.verts.new((-w_out/2,  d_out/2, 0.0))
    
    v5 = bm.verts.new((-w_out/2, -d_out/2, h_out))
    v6 = bm.verts.new(( w_out/2, -d_out/2, h_out))
    v7 = bm.verts.new(( w_out/2,  d_out/2, h_out))
    v8 = bm.verts.new((-w_out/2,  d_out/2, h_out))
    
    # Bottom face
    bm.faces.new((v4, v3, v2, v1))
    # Outer side faces
    bm.faces.new((v1, v2, v6, v5)) # Front (-Y)
    bm.faces.new((v2, v3, v7, v6)) # Right (+X)
    bm.faces.new((v3, v4, v8, v7)) # Back (+Y)
    bm.faces.new((v4, v1, v5, v8)) # Left (-X)
    
    # Inner cavity
    w_in = w_out - 2*wall_t
    d_in = d_out - 2*wall_t
    
    iv1 = bm.verts.new((-w_in/2, -d_in/2, wall_t))
    iv2 = bm.verts.new(( w_in/2, -d_in/2, wall_t))
    iv3 = bm.verts.new(( w_in/2,  d_in/2, wall_t))
    iv4 = bm.verts.new((-w_in/2,  d_in/2, wall_t))
    
    iv5 = bm.verts.new((-w_in/2, -d_in/2, h_out))
    iv6 = bm.verts.new(( w_in/2, -d_in/2, h_out))
    iv7 = bm.verts.new(( w_in/2,  d_in/2, h_out))
    iv8 = bm.verts.new((-w_in/2,  d_in/2, h_out))
    
    # Inner bottom face
    bm.faces.new((iv1, iv2, iv3, iv4))
    # Inner side faces
    bm.faces.new((iv5, iv6, iv2, iv1)) # Front
    bm.faces.new((iv6, iv7, iv3, iv2)) # Right
    bm.faces.new((iv7, iv8, iv4, iv3)) # Back
    bm.faces.new((iv8, iv5, iv1, iv4)) # Left
    
    # Top rim faces connecting outer to inner
    bm.faces.new((v5, v6, iv6, iv5))
    bm.faces.new((v6, v7, iv7, iv6))
    bm.faces.new((v7, v8, iv8, iv7))
    bm.faces.new((v8, v5, iv5, iv8))
    
    # Embossed side ribs
    for y_pos in [-d_out/2 - 0.0015, d_out/2 + 0.0015]:
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((0, y_pos, 0.08)) @ Matrix.Diagonal((0.38, 0.003, 0.015, 1.0)))
    for x_pos in [-w_out/2 - 0.0015, w_out/2 + 0.0015]:
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((x_pos, 0, 0.08)) @ Matrix.Diagonal((0.003, 0.14, 0.015, 1.0)))
        
    # Rear hinge knuckles on body
    # Hinge line is at y=0.104, z=0.160.
    for hx in [-0.14, -0.06, 0.06, 0.14]:
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=12,
            radius1=0.0035, radius2=0.0035, depth=0.026,
            matrix=Matrix.Translation((hx, 0.104, 0.160)) @ Matrix.Rotation(math.pi/2, 4, 'Y')
        )

    # Front lower latch catch bracket (riveted plate with catch hook)
    # At x=0, y=-0.101, z=0.130
    bmesh.ops.create_cube(
        bm, size=1.0,
        matrix=Matrix.Translation((0.0, -0.101, 0.130)) @ Matrix.Diagonal((0.036, 0.002, 0.026, 1.0))
    )
    # Catch hook lip
    bmesh.ops.create_cube(
        bm, size=1.0,
        matrix=Matrix.Translation((0.0, -0.103, 0.125)) @ Matrix.Diagonal((0.026, 0.002, 0.004, 1.0))
    )

    me = bpy.data.meshes.new('body')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('body', me)
    obj.data.materials.append(mat_red_metal)
    bpy.context.scene.collection.objects.link(obj)
    return obj


# -------------------------------------------------------------
# 2. LID LINK
# Plan bbox: centre (0.000, 0.000, 0.190), extents (0.448, 0.208, 0.060)
# Height: z in [0.160, 0.220], y in [-0.104, 0.104], x in [-0.224, 0.224]
# Pivot: (0.000, 0.104, 0.160)
# -------------------------------------------------------------
def build_lid():
    bm = bmesh.new()
    
    w_lid = 0.448
    d_lid = 0.208
    z_bottom = 0.162 # bottom of skirt, 4mm clearance to body rib
    z_lip_top = 0.176
    z_peak = 0.218 # height of top crown
    wall_t = 0.002
    
    # Outer skirt
    s_v1 = bm.verts.new((-w_lid/2, -d_lid/2, z_bottom))
    s_v2 = bm.verts.new(( w_lid/2, -d_lid/2, z_bottom))
    s_v3 = bm.verts.new(( w_lid/2,  d_lid/2, z_bottom))
    s_v4 = bm.verts.new((-w_lid/2,  d_lid/2, z_bottom))
    
    # Rim bend perimeter
    r_v1 = bm.verts.new((-w_lid/2, -d_lid/2, z_lip_top))
    r_v2 = bm.verts.new(( w_lid/2, -d_lid/2, z_lip_top))
    r_v3 = bm.verts.new(( w_lid/2,  d_lid/2, z_lip_top))
    r_v4 = bm.verts.new((-w_lid/2,  d_lid/2, z_lip_top))
    
    # Top peaked crown vertices
    top_w = w_lid/2 - 0.02
    top_d = 0.06
    c_v1 = bm.verts.new((-top_w, -top_d, z_peak))
    c_v2 = bm.verts.new(( top_w, -top_d, z_peak))
    c_v3 = bm.verts.new(( top_w,  top_d, z_peak))
    c_v4 = bm.verts.new((-top_w,  top_d, z_peak))
    
    # Outer Skirt Faces
    bm.faces.new((s_v1, s_v2, r_v2, r_v1)) # Front skirt
    bm.faces.new((s_v2, s_v3, r_v3, r_v2)) # Right skirt
    bm.faces.new((s_v3, s_v4, r_v4, r_v3)) # Back skirt
    bm.faces.new((s_v4, s_v1, r_v1, r_v4)) # Left skirt
    
    # Top Slanted Faces
    bm.faces.new((r_v1, r_v2, c_v2, c_v1)) # Front slope
    bm.faces.new((r_v2, r_v3, c_v3, c_v2)) # Right slope
    bm.faces.new((r_v3, r_v4, c_v4, c_v3)) # Back slope
    bm.faces.new((r_v4, r_v1, c_v1, c_v4)) # Left slope
    bm.faces.new((c_v1, c_v2, c_v3, c_v4)) # Top crown flat
    
    # Inner cavity / underside (gives inner clearance w_lid > w_out = 0.448 > 0.440, d_lid > d_out = 0.208 > 0.200)
    # Inner skirt: width = 0.448 - 0.004 = 0.444 (> 0.440 body), depth = 0.208 - 0.004 = 0.204 (> 0.200 body)
    i_s_v1 = bm.verts.new((-w_lid/2 + wall_t, -d_lid/2 + wall_t, z_bottom))
    i_s_v2 = bm.verts.new(( w_lid/2 - wall_t, -d_lid/2 + wall_t, z_bottom))
    i_s_v3 = bm.verts.new(( w_lid/2 - wall_t,  d_lid/2 - wall_t, z_bottom))
    i_s_v4 = bm.verts.new((-w_lid/2 + wall_t,  d_lid/2 - wall_t, z_bottom))
    
    i_r_v1 = bm.verts.new((-w_lid/2 + wall_t, -d_lid/2 + wall_t, z_lip_top - wall_t))
    i_r_v2 = bm.verts.new(( w_lid/2 - wall_t, -d_lid/2 + wall_t, z_lip_top - wall_t))
    i_r_v3 = bm.verts.new(( w_lid/2 - wall_t,  d_lid/2 - wall_t, z_lip_top - wall_t))
    i_r_v4 = bm.verts.new((-w_lid/2 + wall_t,  d_lid/2 - wall_t, z_lip_top - wall_t))
    
    i_c_v1 = bm.verts.new((-top_w + wall_t, -top_d + wall_t, z_peak - wall_t))
    i_c_v2 = bm.verts.new(( top_w - wall_t, -top_d + wall_t, z_peak - wall_t))
    i_c_v3 = bm.verts.new(( top_w - wall_t,  top_d - wall_t, z_peak - wall_t))
    i_c_v4 = bm.verts.new((-top_w + wall_t,  top_d - wall_t, z_peak - wall_t))
    
    # Skirt bottom rim face
    bm.faces.new((s_v1, i_s_v1, i_s_v2, s_v2))
    bm.faces.new((s_v2, i_s_v2, i_s_v3, s_v3))
    bm.faces.new((s_v3, i_s_v3, i_s_v4, s_v4))
    bm.faces.new((s_v4, i_s_v4, i_s_v1, s_v1))
    
    # Inner faces
    bm.faces.new((i_r_v1, i_r_v2, i_s_v2, i_s_v1))
    bm.faces.new((i_r_v2, i_r_v3, i_s_v3, i_s_v2))
    bm.faces.new((i_r_v3, i_r_v4, i_s_v4, i_s_v3))
    bm.faces.new((i_r_v4, i_r_v1, i_s_v1, i_s_v4))
    
    bm.faces.new((i_c_v1, i_c_v2, i_r_v2, i_r_v1))
    bm.faces.new((i_c_v2, i_c_v3, i_r_v3, i_r_v2))
    bm.faces.new((i_c_v3, i_c_v4, i_r_v4, i_r_v3))
    bm.faces.new((i_c_v4, i_c_v1, i_r_v1, i_r_v4))
    bm.faces.new((i_c_v4, i_c_v3, i_c_v2, i_c_v1))

    # Lid hinge knuckles on rear (5 knuckles)
    # y=0.104, z=0.160
    for hx in [-0.18, -0.10, 0.0, 0.10, 0.18]:
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=12,
            radius1=0.0035, radius2=0.0035, depth=0.026,
            matrix=Matrix.Translation((hx, 0.104, 0.160)) @ Matrix.Rotation(math.pi/2, 4, 'Y')
        )
        
    # Upper latch mounting bracket / pivot eye on front rim of lid (1 piece)
    bmesh.ops.create_cube(
        bm, size=1.0,
        matrix=Matrix.Translation((0.0, -0.1042, 0.170)) @ Matrix.Diagonal((0.024, 0.0016, 0.006, 1.0))
    )

    me = bpy.data.meshes.new('lid')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('lid', me)
    obj.data.materials.append(mat_red_metal)
    bpy.context.scene.collection.objects.link(obj)
    return obj


# -------------------------------------------------------------
# 3. HANDLE LINK
# Plan bbox: centre (0.000, 0.000, 0.255), extents (0.160, 0.024, 0.070)
# Height: z in [0.220, 0.290], y in [-0.012, 0.012], x in [-0.080, 0.080]
# Pivot: (0.000, 0.000, 0.222)
# -------------------------------------------------------------
def build_handle():
    bm = bmesh.new()
    
    wire_r = 0.0025
    
    # Left leg wire (from z=0.220 to z=0.285)
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=12,
        radius1=wire_r, radius2=wire_r, depth=0.065,
        matrix=Matrix.Translation((-0.075, 0.0, 0.2525))
    )
    # Right leg wire
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=12,
        radius1=wire_r, radius2=wire_r, depth=0.065,
        matrix=Matrix.Translation(( 0.075, 0.0, 0.2525))
    )
    
    # Inward pivot pins at bottom of legs (x=±0.075 towards center, welded into legs)
    for px in [-0.0725, 0.0725]:
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=12,
            radius1=wire_r, radius2=wire_r, depth=0.010,
            matrix=Matrix.Translation((px, 0.0, 0.222)) @ Matrix.Rotation(math.pi/2, 4, 'Y')
        )
        
    # Top steel wire crossbar connecting legs
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=12,
        radius1=wire_r, radius2=wire_r, depth=0.150,
        matrix=Matrix.Translation((0.0, 0.0, 0.285)) @ Matrix.Rotation(math.pi/2, 4, 'Y')
    )
    
    # Rubber grip (cylindrical sleeve with ergonomic rib contours)
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=16,
        radius1=0.0075, radius2=0.0075, depth=0.090,
        matrix=Matrix.Translation((0.0, 0.0, 0.285)) @ Matrix.Rotation(math.pi/2, 4, 'Y')
    )
    # Grip finger ribs
    for rx in [-0.030, -0.015, 0.0, 0.015, 0.030]:
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=16,
            radius1=0.0085, radius2=0.0085, depth=0.005,
            matrix=Matrix.Translation((rx, 0.0, 0.285)) @ Matrix.Rotation(math.pi/2, 4, 'Y')
        )

    me = bpy.data.meshes.new('handle')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('handle', me)
    obj.data.materials.append(mat_rubber)
    bpy.context.scene.collection.objects.link(obj)
    return obj


# -------------------------------------------------------------
# 4. LATCH LINK
# Plan bbox: centre (0.000, -0.106, 0.145), extents (0.045, 0.015, 0.050)
# Height: z in [0.120, 0.170], y in [-0.1135, -0.0985] (must not penetrate lid)
# Pivot: (0.000, -0.105, 0.170)
# -------------------------------------------------------------
def build_latch():
    bm = bmesh.new()
    
    # Upper hinge loop (around pivot z=0.170, y=-0.105)
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=12,
        radius1=0.0018, radius2=0.0018, depth=0.020,
        matrix=Matrix.Translation((0.0, -0.105, 0.170)) @ Matrix.Rotation(math.pi/2, 4, 'Y')
    )
        
    # Main toggle clasp plate (stamped sheet metal arm)
    # Center y at -0.1062, thickness 0.0015 in Y, width 0.038 in X, height from z=0.124 to 0.168
    bmesh.ops.create_cube(
        bm, size=1.0,
        matrix=Matrix.Translation((0.0, -0.1062, 0.146)) @ Matrix.Diagonal((0.038, 0.0015, 0.044, 1.0))
    )
    
    # Toggle wire loop / catch hook at bottom (z=0.122 to 0.126)
    bmesh.ops.create_cube(
        bm, size=1.0,
        matrix=Matrix.Translation((0.0, -0.1082, 0.124)) @ Matrix.Diagonal((0.034, 0.0035, 0.004, 1.0))
    )
    
    # Embossed central rib on latch
    bmesh.ops.create_cube(
        bm, size=1.0,
        matrix=Matrix.Translation((0.0, -0.1075, 0.147)) @ Matrix.Diagonal((0.012, 0.0010, 0.030, 1.0))
    )

    me = bpy.data.meshes.new('latch')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('latch', me)
    obj.data.materials.append(mat_chrome)
    bpy.context.scene.collection.objects.link(obj)
    return obj


# Build all links
body_obj = build_body()
lid_obj = build_lid()
handle_obj = build_handle()
latch_obj = build_latch()

# Sanity: every link object exists
for _n in ['body', 'lid', 'handle', 'latch']:
    assert _n in bpy.data.objects, _n
