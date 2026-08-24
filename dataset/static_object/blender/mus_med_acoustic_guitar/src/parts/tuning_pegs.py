"""TuningPegs — six mechanical tuning machines with knobs and posts (part module; imported by src/model.py).

3 left and 3 right chrome tuning machines, each consisting of a vertical string post through the headstock face and a lateral rotating button knob (d=18mm) protruding from the side.
Material: polished chrome plated steel. Instances: 6 (mirror_x). Attaches to: Headstock.

Plan bbox per instance:
center (0.046, 0.035, 0.935) extents (0.032, 0.024, 0.120)
x in [0.030, 0.062], y in [0.023, 0.047], z in [0.875, 0.995]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

TUNING_PEGS_CENTER = (0.046, 0.035, 0.935)
TUNING_PEGS_EXTENTS = (0.032, 0.024, 0.120)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_single_peg(index, is_right, z_pos):
    bm = bmesh.new()
    
    # Target per instance:
    # x in [0.030, 0.062] (center 0.046, extents 0.032)
    # y in [0.023, 0.047] (center 0.035, extents 0.024)
    # z in [0.875, 0.995] (center 0.935, extents 0.120)
    
    sign = 1.0 if is_right else -1.0
    x_post = sign * 0.031
    x_button = sign * 0.0605
    
    t = (z_pos - 0.855) / (1.015 - 0.855)
    y_front = 0.010 + t * (0.038 - 0.010)
    y_back = y_front + 0.014
    
    # 1. Post through headstock (passes through headstock front to back)
    curr_v = set(bm.verts)
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.0028, radius2=0.0028, depth=0.018)
    post_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2 - 0.18, 3, 'X'), verts=post_verts)
    bmesh.ops.translate(bm, vec=(x_post, y_front - 0.002, z_pos), verts=post_verts)
    
    # 2. Shaft connecting post to button on the side of headstock
    curr_v = set(bm.verts)
    shaft_len = abs(x_button - x_post)
    bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=0.0018, radius2=0.0018, depth=shaft_len)
    shaft_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'Y'), verts=shaft_verts)
    bmesh.ops.translate(bm, vec=(0.5 * (x_post + x_button), y_back - 0.004, z_pos), verts=shaft_verts)
    
    # 3. Knob button (d=18mm)
    curr_v = set(bm.verts)
    bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.0085, radius2=0.0085, depth=0.0035)
    knob_verts = [v for v in bm.verts if v not in curr_v]
    bmesh.ops.scale(bm, vec=(1.0, 0.6, 1.0), verts=knob_verts)
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'Y'), verts=knob_verts)
    bmesh.ops.translate(bm, vec=(x_button, y_back - 0.004, z_pos), verts=knob_verts)
    
    # 4. Thin chrome base strip embedded flush on headstock back:
    # Touches headstock back (y ~ y_back)
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
