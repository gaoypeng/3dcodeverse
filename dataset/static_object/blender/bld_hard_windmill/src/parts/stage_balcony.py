"""StageBalcony — Wrap-around wooden gallery stage and safety railing (part module; imported by src/model.py).

Circular/octagonal wooden walkway platform at z=2.00 m extending to 5.20 m outer diameter, supported by 16 angled diagonal timber braces below. Upper perimeter has an 0.85 m high wooden post-and-rail balustrade.
Material: rough-hewn dark oak timber beams and planks. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, 0.000, 2.100) extents (5.200, 5.200, 1.600)
# x in [-2.600, 2.600], y in [-2.600, 2.600], z in [1.300, 2.900]
STAGE_BALCONY_CENTER = (0.000, 0.000, 2.100)
STAGE_BALCONY_EXTENTS = (5.200, 5.200, 1.600)

def make_material(name, rgb, roughness=0.7, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_stage_balcony() -> bpy.types.Object:
    """Stage balcony at z=2.00 m (local origin at z=2.10 m, so platform top at local z = -0.05).
    Z ranges from 1.300 to 2.900 m (local z from -0.800 to +0.800 m).
    Extents: 5.200 x 5.200 x 1.600 m. Outer radius = 2.600 m.
    """
    bm = bmesh.new()
    
    # 1. Main octagonal / 16-sided walkway deck platform (local z = -0.10 to -0.05, height 0.08)
    # Outer radius 2.600, inner radius 1.850 (fits snugly around the base/smock junction)
    # Let's create an octagonal deck or 16-sided deck. An octagonal deck gives exact 5.2m width.
    # We can create outer prism and inner cut, or build concentric polygonal rings.
    
    n_sides = 16
    # Deck outer radius = 2.600 (gives extents 5.200 when n_sides=16 and vertices aligned)
    r_outer = 2.600
    r_inner = 1.850
    z_deck_top = -0.050
    z_deck_bot = -0.120
    
    # Build deck faces
    deck_verts_top_outer = []
    deck_verts_top_inner = []
    deck_verts_bot_outer = []
    deck_verts_bot_inner = []
    
    for i in range(n_sides):
        ang = 2.0 * math.pi * i / n_sides
        c, s = math.cos(ang), math.sin(ang)
        deck_verts_top_outer.append(bm.verts.new((r_outer * c, r_outer * s, z_deck_top)))
        deck_verts_top_inner.append(bm.verts.new((r_inner * c, r_inner * s, z_deck_top)))
        deck_verts_bot_outer.append(bm.verts.new((r_outer * c, r_outer * s, z_deck_bot)))
        deck_verts_bot_inner.append(bm.verts.new((r_inner * c, r_inner * s, z_deck_bot)))
        
    for i in range(n_sides):
        i_next = (i + 1) % n_sides
        # Top face
        bm.faces.new([deck_verts_top_outer[i], deck_verts_top_outer[i_next], deck_verts_top_inner[i_next], deck_verts_top_inner[i]])
        # Bottom face
        bm.faces.new([deck_verts_bot_outer[i], deck_verts_bot_inner[i], deck_verts_bot_inner[i_next], deck_verts_bot_outer[i_next]])
        # Outer rim
        bm.faces.new([deck_verts_top_outer[i], deck_verts_bot_outer[i], deck_verts_bot_outer[i_next], deck_verts_top_outer[i_next]])
        # Inner rim
        bm.faces.new([deck_verts_top_inner[i], deck_verts_top_inner[i_next], deck_verts_bot_inner[i_next], deck_verts_bot_inner[i]])

    # 2. Balustrade Railing (posts and rails)
    # Railing top rail is at z_deck_top + 0.850 = +0.800 (world z = 2.900)
    z_rail_top = 0.800
    z_rail_mid = 0.350
    
    # 16 vertical posts at outer perimeter
    r_post = 2.550
    for i in range(n_sides):
        ang = 2.0 * math.pi * i / n_sides
        c, s = math.cos(ang), math.sin(ang)
        px, py = r_post * c, r_post * s
        
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.06, 0.06, z_rail_top - z_deck_top), verts=res['verts'])
        bmesh.ops.translate(bm, vec=(px, py, (z_rail_top + z_deck_top) * 0.5), verts=res['verts'])

    # Top rail connecting posts
    for i in range(n_sides):
        i_next = (i + 1) % n_sides
        ang1 = 2.0 * math.pi * i / n_sides
        ang2 = 2.0 * math.pi * i_next / n_sides
        p1 = Vector((r_post * math.cos(ang1), r_post * math.sin(ang1), z_rail_top - 0.025))
        p2 = Vector((r_post * math.cos(ang2), r_post * math.sin(ang2), z_rail_top - 0.025))
        mid = (p1 + p2) * 0.5
        dir_v = p2 - p1
        
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.07, 0.05, dir_v.length), verts=res['verts'])
        up = Vector((0, 0, 1))
        rot_axis = up.cross(dir_v.normalized())
        rot_angle = up.angle(dir_v.normalized())
        if rot_axis.length > 1e-5:
            bmesh.ops.rotate(bm, matrix=Matrix.Rotation(rot_angle, 4, rot_axis.normalized()), verts=res['verts'])
        bmesh.ops.translate(bm, vec=mid, verts=res['verts'])

    # Mid rail connecting posts
    for i in range(n_sides):
        i_next = (i + 1) % n_sides
        ang1 = 2.0 * math.pi * i / n_sides
        ang2 = 2.0 * math.pi * i_next / n_sides
        p1 = Vector((r_post * math.cos(ang1), r_post * math.sin(ang1), z_rail_mid))
        p2 = Vector((r_post * math.cos(ang2), r_post * math.sin(ang2), z_rail_mid))
        mid = (p1 + p2) * 0.5
        dir_v = p2 - p1
        
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.05, 0.04, dir_v.length), verts=res['verts'])
        up = Vector((0, 0, 1))
        rot_axis = up.cross(dir_v.normalized())
        rot_angle = up.angle(dir_v.normalized())
        if rot_axis.length > 1e-5:
            bmesh.ops.rotate(bm, matrix=Matrix.Rotation(rot_angle, 4, rot_axis.normalized()), verts=res['verts'])
        bmesh.ops.translate(bm, vec=mid, verts=res['verts'])

    # 3. 16 Diagonal wooden struts below the balcony
    # From the wall (radius ~ 1.95, z = 1.30 -> local z = -0.800) to outer edge (radius ~ 2.45, z = 1.98 -> local z = -0.120)
    for i in range(n_sides):
        ang = 2.0 * math.pi * i / n_sides
        c, s = math.cos(ang), math.sin(ang)
        
        p_wall = Vector((1.950 * c, 1.950 * s, -0.800))
        p_rim = Vector((2.480 * c, 2.480 * s, -0.120))
        mid = (p_wall + p_rim) * 0.5
        dir_v = p_rim - p_wall
        
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.08, 0.08, dir_v.length), verts=res['verts'])
        up = Vector((0, 0, 1))
        rot_axis = up.cross(dir_v.normalized())
        rot_angle = up.angle(dir_v.normalized())
        if rot_axis.length > 1e-5:
            bmesh.ops.rotate(bm, matrix=Matrix.Rotation(rot_angle, 4, rot_axis.normalized()), verts=res['verts'])
        bmesh.ops.translate(bm, vec=mid, verts=res['verts'])

    me = bpy.data.meshes.new("StageBalcony")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("StageBalcony", me)
    obj.location = (0.0, 0.0, 2.100)
    bpy.context.scene.collection.objects.link(obj)
    
    # Material: weathered oak timber
    mat_stage = make_material("StageWoodMat", (0.28, 0.20, 0.13), roughness=0.8)
    obj.data.materials.append(mat_stage)
    
    return obj
