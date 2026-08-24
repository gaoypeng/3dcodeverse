"""Bridge — string anchoring bridge plate and saddle (part module; imported by src/model.py).

Contoured rosewood bridge base (w=0.160m, h=0.038m) with slanted white bone saddle and 6 black bridge pins holding string ball-ends.
Material: dark oiled rosewood with polished bone saddle. Instances: 1. Attaches to: GuitarBody.

Plan bbox: center (0.000, -0.055, 0.155) extents (0.160, 0.012, 0.038)
x in [-0.080, 0.080], y in [-0.061, -0.049], z in [0.136, 0.174]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

BRIDGE_CENTER = (0.000, -0.055, 0.155)
BRIDGE_EXTENTS = (0.160, 0.012, 0.038)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_bridge():
    bm = bmesh.new()
    
    # 1. Bridge Base Plate: contoured wooden plate, wide in center, wings at sides
    # x in [-0.080, 0.080], z in [0.136, 0.174] (height 0.038), y in [-0.057, -0.049]
    # Center block (saddle area) + wings
    
    pts = [
        (-0.080, 0.155),
        (-0.060, 0.140),
        (-0.035, 0.136),
        (0.035, 0.136),
        (0.060, 0.140),
        (0.080, 0.155),
        (0.060, 0.170),
        (0.035, 0.174),
        (-0.035, 0.174),
        (-0.060, 0.170)
    ]
    
    y_front = -0.057
    y_back = -0.049
    
    f_verts = [bm.verts.new((x, y_front, z)) for x, z in pts]
    b_verts = [bm.verts.new((x, y_back, z)) for x, z in pts]
    
    bm.verts.ensure_lookup_table()
    
    # Side walls
    n = len(pts)
    for i in range(n):
        i_next = (i + 1) % n
        bm.faces.new([f_verts[i], f_verts[i_next], b_verts[i_next], b_verts[i]])
        
    # Front cap (facing -Y) & back cap (facing +Y)
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in f_verts and e.verts[1] in f_verts])
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in b_verts and e.verts[1] in b_verts])
    
    # Tag base faces as material index 0
    for f in bm.faces:
        f.material_index = 0
    
    # 2. Bone Saddle: white strip standing up at y = -0.061..-0.056, x in [-0.036, 0.036], z in [0.157, 0.163]
    saddle_ret = bmesh.ops.create_cube(bm, size=1.0)
    saddle_verts = saddle_ret["verts"]
    bmesh.ops.scale(bm, vec=(0.072, 0.005, 0.006), verts=saddle_verts)
    bmesh.ops.translate(bm, vec=(0.000, -0.0585, 0.160), verts=saddle_verts)
    for f in bm.faces:
        if any(v in saddle_verts for v in f.verts):
            f.material_index = 1
    
    # 3. Six Bridge Pins: small cylinders at z = 0.145, y = -0.058
    for i in range(6):
        x_pin = -0.025 + i * (0.050 / 5.0)
        pin_ret = bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.0035, radius2=0.0025, depth=0.006)
        pin_verts = pin_ret["verts"]
        bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi/2, 3, 'X'), verts=pin_verts)
        bmesh.ops.translate(bm, vec=(x_pin, -0.057, 0.146), verts=pin_verts)
        for f in bm.faces:
            if any(v in pin_verts for v in f.verts):
                f.material_index = 2
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Bridge")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Bridge", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_bridge = make_material("RosewoodBridge", (0.12, 0.07, 0.04), roughness=0.6, metallic=0.0)
    mat_bone = make_material("BoneSaddle", (0.92, 0.90, 0.84), roughness=0.3, metallic=0.0)
    mat_pin = make_material("BridgePin", (0.05, 0.05, 0.05), roughness=0.4, metallic=0.0)
    
    obj.data.materials.append(mat_bridge)
    obj.data.materials.append(mat_bone)
    obj.data.materials.append(mat_pin)
    
    return obj
    
    return obj
