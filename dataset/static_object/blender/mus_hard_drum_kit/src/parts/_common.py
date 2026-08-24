"""Shared materials and geometry helpers for DrumKit parts."""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix, Euler

def get_or_make_material(name, rgb, roughness=0.5, metallic=0.0):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat

def get_drum_materials():
    return {
        "shell_blue": get_or_make_material("DrumShellBlue", (0.02, 0.05, 0.22), roughness=0.15, metallic=0.2),
        "snare_silver": get_or_make_material("SnareShellChrome", (0.85, 0.86, 0.88), roughness=0.1, metallic=0.95),
        "chrome": get_or_make_material("HardwareChrome", (0.9, 0.9, 0.92), roughness=0.15, metallic=0.95),
        "black_metal": get_or_make_material("BlackHardware", (0.05, 0.05, 0.05), roughness=0.4, metallic=0.8),
        "head_black": get_or_make_material("DrumHeadBlack", (0.03, 0.03, 0.03), roughness=0.3, metallic=0.0),
        "head_white": get_or_make_material("DrumHeadWhite", (0.92, 0.92, 0.90), roughness=0.35, metallic=0.0),
        "head_clear": get_or_make_material("DrumHeadClear", (0.85, 0.88, 0.90), roughness=0.15, metallic=0.1),
        "bronze": get_or_make_material("CymbalBronze", (0.82, 0.58, 0.22), roughness=0.22, metallic=0.85),
        "rubber_black": get_or_make_material("BlackRubber", (0.08, 0.08, 0.08), roughness=0.7, metallic=0.0),
        "felt_white": get_or_make_material("FeltWhite", (0.88, 0.86, 0.82), roughness=0.9, metallic=0.0),
        "felt_black": get_or_make_material("FeltBlack", (0.1, 0.1, 0.1), roughness=0.9, metallic=0.0),
        "vinyl_black": get_or_make_material("VinylBlack", (0.04, 0.04, 0.04), roughness=0.35, metallic=0.05),
    }

def mesh_from_bmesh(name, bm, materials=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.update()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    if materials:
        for m in materials:
            obj.data.materials.append(m)
    return obj

def add_cylinder(bm, radius, depth, segments=32, transform=None, mat_idx=0):
    tr = transform if transform is not None else Matrix.Identity(4)
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius, depth=depth, matrix=tr)
    # create_cone returns 'verts'
    created_verts = set(res["verts"])
    for v in created_verts:
        for f in v.link_faces:
            if all(vert in created_verts for vert in f.verts):
                f.material_index = mat_idx
    return res

def add_cone(bm, radius1, radius2, depth, segments=32, transform=None, mat_idx=0):
    tr = transform if transform is not None else Matrix.Identity(4)
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius1, radius2=radius2, depth=depth, matrix=tr)
    created_verts = set(res["verts"])
    for v in created_verts:
        for f in v.link_faces:
            if all(vert in created_verts for vert in f.verts):
                f.material_index = mat_idx
    return res

def add_box(bm, size, transform=None, mat_idx=0):
    tr = transform if transform is not None else Matrix.Identity(4)
    # create_cube creates a unit cube centered at origin scaled by size
    scale_mat = Matrix.Diagonal((size[0], size[1], size[2], 1.0))
    res = bmesh.ops.create_cube(bm, size=1.0, matrix=tr @ scale_mat)
    created_verts = set(res["verts"])
    for v in created_verts:
        for f in v.link_faces:
            if all(vert in created_verts for vert in f.verts):
                f.material_index = mat_idx
    return res

def add_tube(bm, outer_r, inner_r, depth, segments=32, transform=None, mat_idx=0):
    tr = transform if transform is not None else Matrix.Identity(4)
    half_d = depth * 0.5
    top_outer, bot_outer, top_inner, bot_inner = [], [], [], []
    for i in range(segments):
        ang = 2.0 * math.pi * i / segments
        ca, sa = math.cos(ang), math.sin(ang)
        v1 = bm.verts.new(tr @ Vector((outer_r * ca, outer_r * sa, half_d)))
        v2 = bm.verts.new(tr @ Vector((outer_r * ca, outer_r * sa, -half_d)))
        v3 = bm.verts.new(tr @ Vector((inner_r * ca, inner_r * sa, half_d)))
        v4 = bm.verts.new(tr @ Vector((inner_r * ca, inner_r * sa, -half_d)))
        top_outer.append(v1)
        bot_outer.append(v2)
        top_inner.append(v3)
        bot_inner.append(v4)
    
    for i in range(segments):
        next_i = (i + 1) % segments
        # Outer wall
        f1 = bm.faces.new([top_outer[i], top_outer[next_i], bot_outer[next_i], bot_outer[i]])
        # Inner wall
        f2 = bm.faces.new([bot_inner[i], bot_inner[next_i], top_inner[next_i], top_inner[i]])
        # Top cap
        f3 = bm.faces.new([top_inner[i], top_inner[next_i], top_outer[next_i], top_outer[i]])
        # Bottom cap
        f4 = bm.faces.new([bot_outer[i], bot_outer[next_i], bot_inner[next_i], bot_inner[i]])
        f1.material_index = mat_idx
        f2.material_index = mat_idx
        f3.material_index = mat_idx
        f4.material_index = mat_idx

def add_cylinder_z(bm, radius, z_min, z_max, center_xy=(0, 0), segments=32, mat_idx=0):
    """Cylinder standing along world Z from z_min to z_max."""
    depth = z_max - z_min
    mid_z = (z_min + z_max) * 0.5
    tr = Matrix.Translation((center_xy[0], center_xy[1], mid_z))
    return add_cylinder(bm, radius, depth, segments=segments, transform=tr, mat_idx=mat_idx)

def add_tripod_stand(bm, base_center, top_z, leg_radius=0.20, pole_r=0.012, leg_r=0.008, chrome_mat=0, rubber_mat=1):
    """Generates standard drum / cymbal tripod base standing on z=0."""
    cx, cy = base_center[0], base_center[1]
    collar_z = 0.22
    brace_collar_z = 0.10
    
    # Main upright vertical pole from z=0.02 to top_z
    add_cylinder_z(bm, pole_r, z_min=0.02, z_max=top_z, center_xy=(cx, cy), segments=16, mat_idx=chrome_mat)
    
    for i in range(3):
        ang = 2.0 * math.pi * i / 3.0 + 0.3
        lx = cx + leg_radius * math.cos(ang)
        ly = cy + leg_radius * math.sin(ang)
        
        # Rubber foot strictly from z=0.0 to z=0.024
        add_cylinder_z(bm, 0.015, z_min=0.0, z_max=0.024, center_xy=(lx, ly), segments=12, mat_idx=rubber_mat)
        
        # Leg strut from collar (cx, cy, collar_z) to foot top (lx, ly, 0.024)
        p1 = Vector((cx, cy, collar_z))
        p2 = Vector((lx, ly, 0.024))
        diff = p2 - p1
        dist = diff.length
        mid = (p1 + p2) * 0.5
        rot = diff.to_track_quat('Z', 'X').to_matrix().to_4x4()
        tr_leg = Matrix.Translation(mid) @ rot
        add_cylinder(bm, leg_r, dist, segments=12, transform=tr_leg, mat_idx=chrome_mat)
        
        # Double brace strut from lower collar to mid of leg
        p_brace_top = Vector((cx, cy, brace_collar_z))
        p_brace_bot = (p1 + p2) * 0.5
        b_diff = p_brace_bot - p_brace_top
        b_dist = b_diff.length
        b_mid = (p_brace_top + p_brace_bot) * 0.5
        b_rot = b_diff.to_track_quat('Z', 'X').to_matrix().to_4x4()
        tr_brace = Matrix.Translation(b_mid) @ b_rot
        add_cylinder(bm, leg_r * 0.75, b_dist, segments=10, transform=tr_brace, mat_idx=chrome_mat)
