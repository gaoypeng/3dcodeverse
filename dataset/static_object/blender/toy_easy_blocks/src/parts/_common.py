"""Common helpers for AlphabetBlockTower parts."""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    """Principled BSDF material with flat PBR values."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def get_wood_material():
    return make_material("BeechWood", (0.86, 0.72, 0.52), roughness=0.45, metallic=0.0)


def create_letter_curves(letter: str, size: float):
    """Generate 2D curve strokes for a letter in normalized [-0.5, 0.5] coordinates.
    Returns list of stroke boxes: [(cx, cy, w, h)]
    """
    s = size
    strokes = []
    if letter == 'A':
        leg_w = 0.16 * s
        strokes.append((-0.22 * s, -0.05 * s, leg_w, 0.70 * s)) # left leg
        strokes.append((0.22 * s, -0.05 * s, leg_w, 0.70 * s))  # right leg
        strokes.append((0.0, 0.32 * s, 0.60 * s, 0.16 * s))     # top cap
        strokes.append((0.0, -0.05 * s, 0.40 * s, 0.14 * s))    # crossbar
        # Serifs / base feet
        strokes.append((-0.22 * s, -0.36 * s, 0.28 * s, 0.10 * s))
        strokes.append((0.22 * s, -0.36 * s, 0.28 * s, 0.10 * s))
    elif letter == 'B':
        w = 0.16 * s
        strokes.append((-0.25 * s, 0.0, w, 0.80 * s))
        strokes.append((-0.02 * s, 0.34 * s, 0.38 * s, 0.14 * s))
        strokes.append((0.15 * s, 0.22 * s, 0.16 * s, 0.26 * s))
        strokes.append((-0.02 * s, 0.04 * s, 0.38 * s, 0.12 * s))
        strokes.append((0.0, -0.02 * s, 0.42 * s, 0.12 * s))
        strokes.append((0.19 * s, -0.18 * s, 0.16 * s, 0.28 * s))
        strokes.append((0.0, -0.34 * s, 0.42 * s, 0.14 * s))
        strokes.append((-0.25 * s, 0.36 * s, 0.26 * s, 0.08 * s))
        strokes.append((-0.25 * s, -0.36 * s, 0.26 * s, 0.08 * s))
    elif letter == 'C':
        w = 0.16 * s
        strokes.append((-0.25 * s, 0.0, w, 0.60 * s))
        strokes.append((0.0, 0.32 * s, 0.54 * s, 0.16 * s))
        strokes.append((0.0, -0.32 * s, 0.54 * s, 0.16 * s))
        strokes.append((0.22 * s, 0.22 * s, 0.14 * s, 0.16 * s))
        strokes.append((0.22 * s, -0.22 * s, 0.14 * s, 0.16 * s))
    elif letter == 'D':
        w = 0.16 * s
        strokes.append((-0.25 * s, 0.0, w, 0.80 * s))
        strokes.append((-0.02 * s, 0.34 * s, 0.38 * s, 0.14 * s))
        strokes.append((-0.02 * s, -0.34 * s, 0.38 * s, 0.14 * s))
        strokes.append((0.20 * s, 0.0, 0.16 * s, 0.58 * s))
        strokes.append((0.12 * s, 0.22 * s, 0.14 * s, 0.18 * s))
        strokes.append((0.12 * s, -0.22 * s, 0.14 * s, 0.18 * s))
        strokes.append((-0.25 * s, 0.36 * s, 0.26 * s, 0.08 * s))
        strokes.append((-0.25 * s, -0.36 * s, 0.26 * s, 0.08 * s))
    elif letter == 'E':
        w = 0.16 * s
        strokes.append((-0.25 * s, 0.0, w, 0.80 * s))
        strokes.append((0.02 * s, 0.34 * s, 0.44 * s, 0.14 * s))
        strokes.append((-0.02 * s, 0.0, 0.36 * s, 0.12 * s))
        strokes.append((0.02 * s, -0.34 * s, 0.44 * s, 0.14 * s))
        strokes.append((0.20 * s, 0.27 * s, 0.08 * s, 0.12 * s))
        strokes.append((0.20 * s, -0.27 * s, 0.08 * s, 0.12 * s))
    return strokes


def build_alphabet_block(name: str, side_len: float, bevel_w: float, center_z: float, rot_z_deg: float,
                         letter: str, letter_rgb: tuple) -> bpy.types.Object:
    """Constructs a beveled wooden cube with recessed face panels and embossed painted letters."""
    # 1. Main block with beveled edges
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=side_len)
    
    # Bevel the edges of the base wooden cube
    edges_to_bevel = list(bm.edges)
    bmesh.ops.bevel(bm, geom=edges_to_bevel, offset=bevel_w, segments=3, profile=0.5, affect='EDGES')
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Setup materials
    wood_mat = get_wood_material()
    color_mat = make_material(f"Block_{letter}_ColorMat", letter_rgb, roughness=0.35, metallic=0.0)
    
    obj.data.materials.append(wood_mat)   # slot 0: Beech wood
    obj.data.materials.append(color_mat)  # slot 1: Paint color
    
    face_angles = [0.0, math.pi / 2, math.pi, -math.pi / 2]
    
    panel_margin = side_len * 0.12
    panel_size = side_len - 2 * panel_margin
    panel_depth = side_len * 0.022
    relief_depth = side_len * 0.028
    
    sub_objs = []
    
    for fa in face_angles:
        rot_mat = Matrix.Rotation(fa, 4, 'Z')
        
        # Inner painted plate (color_mat)
        bm_p = bmesh.new()
        bmesh.ops.create_cube(bm_p, size=1.0)
        bmesh.ops.scale(bm_p, vec=(panel_size, panel_depth, panel_size), verts=bm_p.verts)
        bmesh.ops.bevel(bm_p, geom=list(bm_p.edges), offset=bevel_w * 0.4, segments=2, profile=0.5, affect='EDGES')
        panel_y = -(side_len / 2 - panel_depth / 2 + 0.0002)
        bmesh.ops.translate(bm_p, vec=(0, panel_y, 0), verts=bm_p.verts)
        bmesh.ops.transform(bm_p, matrix=rot_mat, verts=bm_p.verts)
        
        me_p = bpy.data.meshes.new("Panel")
        bm_p.to_mesh(me_p)
        bm_p.free()
        p_obj = bpy.data.objects.new("Panel", me_p)
        p_obj.data.materials.append(color_mat)
        bpy.context.scene.collection.objects.link(p_obj)
        sub_objs.append(p_obj)
        
        # Letter strokes (wood_mat relief)
        strokes = create_letter_curves(letter, panel_size * 0.85)
        for sx, sz, sw, sh in strokes:
            bm_s = bmesh.new()
            bmesh.ops.create_cube(bm_s, size=1.0)
            let_thick = relief_depth + 0.0005
            bmesh.ops.scale(bm_s, vec=(sw, let_thick, sh), verts=bm_s.verts)
            bmesh.ops.bevel(bm_s, geom=list(bm_s.edges), offset=min(sw, sh) * 0.12, segments=2, profile=0.5, affect='EDGES')
            stroke_y = -(side_len / 2 + let_thick / 2 - relief_depth)
            bmesh.ops.translate(bm_s, vec=(sx, stroke_y, sz), verts=bm_s.verts)
            bmesh.ops.transform(bm_s, matrix=rot_mat, verts=bm_s.verts)
            
            me_s = bpy.data.meshes.new("Stroke")
            bm_s.to_mesh(me_s)
            bm_s.free()
            s_obj = bpy.data.objects.new("Stroke", me_s)
            s_obj.data.materials.append(wood_mat)
            bpy.context.scene.collection.objects.link(s_obj)
            sub_objs.append(s_obj)
            
    # Join sub_objs into obj without transform warnings
    bpy.ops.object.select_all(action='DESELECT')
    for so in sub_objs:
        so.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.join()
    
    # Now set world transform (rotation around Z and center position)
    obj.rotation_euler = (0, 0, math.radians(rot_z_deg))
    obj.location = (0, 0, center_z)
    
    return obj
