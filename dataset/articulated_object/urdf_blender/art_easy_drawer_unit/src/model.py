"""BedsideDrawerUnit — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

CONTRACT: build ONE mesh object per URDF link, named EXACTLY like the link, placed
at its REST-POSE WORLD position (= URDF q=0, the pose the plan's bboxes describe).
Z is UP, -Y is FRONT, +X is RIGHT.
"""
import bpy
import bmesh
import math
from mathutils import Vector

# Clear any existing objects in the scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def create_material(name, base_color, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = base_color
        bsdf.inputs['Roughness'].default_value = roughness
        bsdf.inputs['Metallic'].default_value = metallic
    return mat

mat_oak = create_material("OakWood", (0.72, 0.53, 0.33, 1.0), roughness=0.4, metallic=0.0)
mat_birch = create_material("BirchPlywood", (0.84, 0.72, 0.54, 1.0), roughness=0.5, metallic=0.0)
mat_brass = create_material("BrushedBrass", (0.88, 0.70, 0.22, 1.0), roughness=0.22, metallic=0.92)

def add_box(bm, center, size, mat_index=0):
    cx, cy, cz = center
    sx, sy, sz = size
    verts = [
        bm.verts.new((cx - sx/2, cy - sy/2, cz - sz/2)),
        bm.verts.new((cx + sx/2, cy - sy/2, cz - sz/2)),
        bm.verts.new((cx + sx/2, cy + sy/2, cz - sz/2)),
        bm.verts.new((cx - sx/2, cy + sy/2, cz - sz/2)),
        bm.verts.new((cx - sx/2, cy - sy/2, cz + sz/2)),
        bm.verts.new((cx + sx/2, cy - sy/2, cz + sz/2)),
        bm.verts.new((cx + sx/2, cy + sy/2, cz + sz/2)),
        bm.verts.new((cx - sx/2, cy + sy/2, cz + sz/2)),
    ]
    faces = [
        (0, 1, 2, 3), # bottom (-Z)
        (4, 7, 6, 5), # top (+Z)
        (0, 4, 5, 1), # front (-Y)
        (2, 6, 7, 3), # back (+Y)
        (0, 3, 7, 4), # left (-X)
        (1, 5, 6, 2), # right (+X)
    ]
    for f_idx in faces:
        face = bm.faces.new([verts[i] for i in f_idx])
        face.material_index = mat_index

def add_tapered_leg(bm, top_center, top_radius, bot_center, bot_radius, segments=16, mat_index=0):
    tx, ty, tz = top_center
    bx, by, bz = bot_center
    top_verts = []
    bot_verts = []
    for i in range(segments):
        ang = 2 * math.pi * i / segments
        top_verts.append(bm.verts.new((tx + top_radius * math.cos(ang), ty + top_radius * math.sin(ang), tz)))
        bot_verts.append(bm.verts.new((bx + bot_radius * math.cos(ang), by + bot_radius * math.sin(ang), bz)))
    
    for i in range(segments):
        i_next = (i + 1) % segments
        face = bm.faces.new([bot_verts[i], bot_verts[i_next], top_verts[i_next], top_verts[i]])
        face.material_index = mat_index
    bm.faces.new(reversed(bot_verts)).material_index = mat_index
    bm.faces.new(top_verts).material_index = mat_index

def add_u_handle(bm, center_x, center_z, front_face_y, width=0.120, depth=0.015, height=0.012, thickness=0.010, mat_index=2):
    """Creates a continuous solid U-shaped pull handle with zero internal non-manifold boundaries."""
    # Front bar is at front_face_y - depth to front_face_y - depth + thickness
    # Legs extend from front_face_y - depth + thickness to front_face_y + 0.002 (embedded in front panel)
    w_half = width / 2
    h_half = height / 2
    y_front = front_face_y - depth
    y_bar_back = y_front + thickness
    y_embed = front_face_y + 0.002
    
    z_bot = center_z - h_half
    z_top = center_z + h_half
    
    x_l_outer = center_x - w_half
    x_l_inner = center_x - w_half + thickness
    x_r_inner = center_x + w_half - thickness
    x_r_outer = center_x + w_half
    
    # 12 bottom vertices (z = z_bot):
    # Outer loop:
    v0  = bm.verts.new((x_l_outer, y_embed,    z_bot))
    v1  = bm.verts.new((x_l_outer, y_front,    z_bot))
    v2  = bm.verts.new((x_r_outer, y_front,    z_bot))
    v3  = bm.verts.new((x_r_outer, y_embed,    z_bot))
    v4  = bm.verts.new((x_r_inner, y_embed,    z_bot))
    v5  = bm.verts.new((x_r_inner, y_bar_back, z_bot))
    v6  = bm.verts.new((x_l_inner, y_bar_back, z_bot))
    v7  = bm.verts.new((x_l_inner, y_embed,    z_bot))
    
    # 8 top vertices (z = z_top):
    v8  = bm.verts.new((x_l_outer, y_embed,    z_top))
    v9  = bm.verts.new((x_l_outer, y_front,    z_top))
    v10 = bm.verts.new((x_r_outer, y_front,    z_top))
    v11 = bm.verts.new((x_r_outer, y_embed,    z_top))
    v12 = bm.verts.new((x_r_inner, y_embed,    z_top))
    v13 = bm.verts.new((x_r_inner, y_bar_back, z_top))
    v14 = bm.verts.new((x_l_inner, y_bar_back, z_top))
    v15 = bm.verts.new((x_l_inner, y_embed,    z_top))
    
    # Top and bottom U-shaped faces (subdivided into 3 quads each):
    # Left standoff cap bottom/top:
    bm.faces.new([v0, v1, v6, v7]).material_index = mat_index
    bm.faces.new([v15, v14, v9, v8]).material_index = mat_index
    # Center bar bottom/top:
    bm.faces.new([v1, v2, v5, v6]).material_index = mat_index
    bm.faces.new([v14, v13, v10, v9]).material_index = mat_index
    # Right standoff cap bottom/top:
    bm.faces.new([v5, v2, v3, v4]).material_index = mat_index
    bm.faces.new([v12, v11, v10, v13]).material_index = mat_index
    
    # Side quads:
    # Outer front:
    bm.faces.new([v1, v2, v10, v9]).material_index = mat_index
    # Outer left:
    bm.faces.new([v0, v1, v9, v8]).material_index = mat_index
    # Outer right:
    bm.faces.new([v2, v3, v11, v10]).material_index = mat_index
    # Inner bar back:
    bm.faces.new([v6, v5, v13, v14]).material_index = mat_index
    # Inner left standoff:
    bm.faces.new([v7, v6, v14, v15]).material_index = mat_index
    # Inner right standoff:
    bm.faces.new([v5, v4, v12, v13]).material_index = mat_index
    # Standoff mount ends (embedded in front panel):
    bm.faces.new([v3, v4, v12, v11]).material_index = mat_index
    bm.faces.new([v7, v0, v8, v15]).material_index = mat_index


# ==============================================================================
# CARCASS LINK
# ==============================================================================
# BBox target: center (0, 0, 0.275), extents (0.50, 0.45, 0.55) -> X: [-0.25, 0.25], Y: [-0.225, 0.225], Z: [0.0, 0.55]
# Cabinet box sits from Z=0.10 to Z=0.55 (height 0.45m).
# 4 legs from Z=0.00 to Z=0.10.

bm_carcass = bmesh.new()

t_wall = 0.020  # wall thickness 20mm

# 1. Top panel: Z in [0.53, 0.55] (thickness 0.02m)
add_box(bm_carcass, (0.0, 0.0, 0.54), (0.50, 0.45, t_wall), mat_index=0)

# 2. Bottom panel: Z in [0.10, 0.12] (thickness 0.02m)
add_box(bm_carcass, (0.0, 0.0, 0.11), (0.50, 0.45, t_wall), mat_index=0)

# 3. Left panel: X in [-0.25, -0.23], Z between 0.12 and 0.53 (height 0.41m)
add_box(bm_carcass, (-0.24, 0.0, 0.325), (t_wall, 0.45, 0.41), mat_index=0)

# 4. Right panel: X in [0.23, 0.25], Z between 0.12 and 0.53
add_box(bm_carcass, (0.24, 0.0, 0.325), (t_wall, 0.45, 0.41), mat_index=0)

# 5. Back panel: Y in [0.200, 0.225], X between -0.23 and 0.23, Z between 0.12 and 0.53
add_box(bm_carcass, (0.0, 0.2125, 0.325), (0.46, 0.025, 0.41), mat_index=0)

# 6. Middle divider shelf: separates top and bottom compartments.
# Center at Z = 0.320, thickness 0.02m -> Z in [0.310, 0.330]
# Divider spans from front Y=-0.225 to back Y=0.200 (depth 0.425, center Y=-0.0125)
add_box(bm_carcass, (0.0, -0.0125, 0.320), (0.46, 0.425, 0.02), mat_index=0)

# 7. Four tapered wooden legs (Scandinavian splayed style)
# Leg top at Z=0.10, bottom at Z=0.00
leg_offsets = [
    (-0.19, -0.15, -0.21, -0.17), # Left front
    ( 0.19, -0.15,  0.21, -0.17), # Right front
    (-0.19,  0.15, -0.21,  0.17), # Left back
    ( 0.19,  0.15,  0.21,  0.17), # Right back
]
for tx, ty, bx, by in leg_offsets:
    add_tapered_leg(bm_carcass, (tx, ty, 0.10), 0.022, (bx, by, 0.00), 0.014, segments=16, mat_index=0)

me_carcass = bpy.data.meshes.new('carcass')
bm_carcass.to_mesh(me_carcass)
bm_carcass.free()

ob_carcass = bpy.data.objects.new('carcass', me_carcass)
ob_carcass.data.materials.append(mat_oak)
bpy.context.scene.collection.objects.link(ob_carcass)


# ==============================================================================
# DRAWERS BUILDER
# ==============================================================================
def build_drawer_mesh(name, z_center):
    bm = bmesh.new()
    
    # 1. Front panel:
    # Size: width 0.450, thickness 0.018, height 0.180
    # Front face at Y = -0.215, Back face at Y = -0.197 -> Center Y = -0.206
    front_center = (0.0, -0.206, z_center)
    front_size = (0.450, 0.018, 0.180)
    add_box(bm, front_center, front_size, mat_index=0)
    
    # 2. Drawer inner box (plywood):
    box_w = 0.430
    box_t = 0.012 # 12mm plywood
    box_y_front = -0.197
    box_y_back = 0.200
    box_depth = box_y_back - box_y_front # 0.397
    box_z_bot = z_center - 0.080
    box_z_top = z_center + 0.070
    box_h = box_z_top - box_z_bot # 0.150
    
    # Bottom board:
    add_box(bm, (0.0, (box_y_front + box_y_back)/2, box_z_bot + box_t/2), (box_w, box_depth, box_t), mat_index=1)
    # Left board:
    add_box(bm, (-box_w/2 + box_t/2, (box_y_front + box_y_back)/2, box_z_bot + box_h/2), (box_t, box_depth, box_h), mat_index=1)
    # Right board:
    add_box(bm, ( box_w/2 - box_t/2, (box_y_front + box_y_back)/2, box_z_bot + box_h/2), (box_t, box_depth, box_h), mat_index=1)
    # Back board:
    add_box(bm, (0.0, box_y_back - box_t/2, box_z_bot + box_h/2), (box_w - 2*box_t, box_t, box_h), mat_index=1)
    
    # 3. Brushed brass bar pull handle (single clean manifold U-shape):
    add_u_handle(bm, center_x=0.0, center_z=z_center, front_face_y=-0.215, width=0.120, depth=0.015, height=0.012, thickness=0.010, mat_index=2)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(mat_oak)
    ob.data.materials.append(mat_birch)
    ob.data.materials.append(mat_brass)
    bpy.context.scene.collection.objects.link(ob)
    return ob

top_drawer = build_drawer_mesh('top_drawer', 0.425)
bottom_drawer = build_drawer_mesh('bottom_drawer', 0.215)
