"""Fretboard — fingerboard with embedded frets and position markers (part module; imported by src/model.py).

Flat rosewood fingerboard plate glued to the front of neck, containing 20 nickel-silver fret wires spaced down the board, white synthetic nut at top (z=0.86), and dot inlays at frets 3, 5, 7, 9, 12, 15, 17, 19.
Material: dark rosewood board with silver nickel frets and mother-of-pearl dots. Instances: 1. Attaches to: GuitarNeck.

Plan bbox: center (0.000, -0.018, 0.655) extents (0.052, 0.012, 0.430)
x in [-0.026, 0.026], y in [-0.024, -0.012], z in [0.440, 0.870]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

FRETBOARD_CENTER = (0.000, -0.018, 0.655)
FRETBOARD_EXTENTS = (0.052, 0.012, 0.430)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_fretboard():
    bm = bmesh.new()
    
    # 1. Main Fingerboard plank:
    # Starts at z = 0.498 to maintain <= 2 mm overlap with GuitarBody top (z=0.500)
    # Ends at z = 0.870 (matches nut / neck top)
    z_bottom = 0.498
    z_top = 0.870
    
    y_front = -0.022
    y_back = -0.012
    
    # Width tapers from bottom (x = +/-0.026) to top (x = +/-0.021)
    t_bot = (z_bottom - 0.440) / (0.870 - 0.440)
    w_bot = 0.026 * (1.0 - t_bot) + 0.021 * t_bot
    w_top = 0.021
    
    # 8 corner vertices of the tapered fretboard slab
    v0 = bm.verts.new((-w_bot, y_front, z_bottom))
    v1 = bm.verts.new(( w_bot, y_front, z_bottom))
    v2 = bm.verts.new(( w_top, y_front, z_top))
    v3 = bm.verts.new((-w_top, y_front, z_top))
    
    v4 = bm.verts.new((-w_bot, y_back, z_bottom))
    v5 = bm.verts.new(( w_bot, y_back, z_bottom))
    v6 = bm.verts.new(( w_top, y_back, z_top))
    v7 = bm.verts.new((-w_top, y_back, z_top))
    
    bm.verts.ensure_lookup_table()
    
    # Faces of the fretboard plank
    bm.faces.new([v0, v3, v2, v1]) # front (facing -Y)
    bm.faces.new([v4, v5, v6, v7]) # back (facing +Y)
    bm.faces.new([v0, v1, v5, v4]) # bottom (facing -Z)
    bm.faces.new([v2, v3, v7, v6]) # top (facing +Z)
    bm.faces.new([v3, v0, v4, v7]) # left (facing -X)
    bm.faces.new([v1, v2, v6, v5]) # right (facing +X)
    
    # 2. White Nut at z = 0.858..0.868 (y in [-0.024, -0.012])
    curr_v = set(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    nut_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.scale(bm, vec=(0.043, 0.011, 0.010), verts=nut_verts)
    bmesh.ops.translate(bm, vec=(0.000, -0.018, 0.863), verts=nut_verts)
    
    # 3. 20 Raised Fret Wires:
    scale_len = 0.705
    for fret_num in range(1, 21):
        d_from_nut = scale_len * (1.0 - 2.0 ** (-fret_num / 12.0))
        z_fret = 0.860 - d_from_nut
        if z_fret < z_bottom + 0.002:
            break
            
        t = (z_fret - 0.440) / (0.870 - 0.440)
        half_w = 0.026 * (1.0 - t) + 0.021 * t
        
        curr_v = set(bm.verts)
        # Create half-cylinder / wire for fret wire
        bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.0014, radius2=0.0014, depth=half_w * 2.0)
        fret_verts = [v for v in bm.verts if v not in curr_v]
        # Rotate cone from Z axis to X axis
        bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'Y'), verts=fret_verts)
        # Position slightly raised in front of the fretboard (y = -0.022 is board front, fret center at -0.0228)
        bmesh.ops.translate(bm, vec=(0.000, -0.0228, z_fret), verts=fret_verts)
        
    # 4. Dot inlays (mother of pearl position dots)
    for fret_num in [3, 5, 7, 9, 12, 15, 17, 19]:
        d_prev = scale_len * (1.0 - 2.0 ** (-(fret_num - 1) / 12.0))
        d_curr = scale_len * (1.0 - 2.0 ** (-fret_num / 12.0))
        z_dot = 0.860 - 0.5 * (d_prev + d_curr)
        if z_dot < z_bottom + 0.002:
            continue
        
        if fret_num == 12:
            for x_offset in [-0.008, 0.008]:
                curr_v = set(bm.verts)
                bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.0025, radius2=0.0025, depth=0.001)
                dot_verts = [v for v in bm.verts if v not in curr_v]
                bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'X'), verts=dot_verts)
                bmesh.ops.translate(bm, vec=(x_offset, -0.0221, z_dot), verts=dot_verts)
        else:
            curr_v = set(bm.verts)
            bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.003, radius2=0.003, depth=0.001)
            dot_verts = [v for v in bm.verts if v not in curr_v]
            bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'X'), verts=dot_verts)
            bmesh.ops.translate(bm, vec=(0.000, -0.0221, z_dot), verts=dot_verts)
            
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Fretboard")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Fretboard", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Assign materials
    mat_board = make_material("DarkRosewood", (0.12, 0.07, 0.05), roughness=0.55, metallic=0.0)
    mat_fret = make_material("SilverNickelFret", (0.92, 0.92, 0.95), roughness=0.15, metallic=0.95)
    mat_dot = make_material("PearloidDot", (0.98, 0.98, 0.95), roughness=0.2, metallic=0.0)
    mat_nut = make_material("BoneNut", (0.92, 0.90, 0.82), roughness=0.35, metallic=0.0)
    
    obj.data.materials.append(mat_board)
    obj.data.materials.append(mat_fret)
    obj.data.materials.append(mat_dot)
    obj.data.materials.append(mat_nut)
    
    for poly in obj.data.polygons:
        c = poly.center
        if c.z > 0.855 and abs(c.x) < 0.022 and c.y > -0.024:
            poly.material_index = 3
        elif c.y < -0.0224:
            poly.material_index = 1
        elif c.y < -0.0219 and abs(c.x) < 0.012:
            poly.material_index = 2
        else:
            poly.material_index = 0
            
    return obj
