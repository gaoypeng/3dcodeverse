"""EntranceDoor — Ground floor entrance door with surround (part module; imported by src/model.py).

Heavy vertical plank wooden arched door, width 0.90 m, height 1.70 m, recessed into the front (-Y) face of the octagonal base, complete with raised stone arch surround and iron handle.
Material: dark rustic wood planks with forged iron hardware. Instances: 1. Attaches to: BaseStructure (must touch, no gap).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -2.100, 0.900) extents (0.950, 0.220, 1.750)
# x in [-0.475, 0.475], y in [-2.210, -1.990], z in [0.025, 1.775]
ENTRANCE_DOOR_CENTER = (0.000, -2.100, 0.900)
ENTRANCE_DOOR_EXTENTS = (0.950, 0.220, 1.750)

def make_material(name, rgb, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_entrance_door() -> bpy.types.Object:
    """Arched wooden plank door with stone arch surround and forged iron hardware.
    World center: (0.0, -2.100, 0.900).
    Local Z: -0.875 to +0.875 (world z: 0.025 to 1.775).
    Local X: -0.475 to +0.475 (width 0.950).
    Local Y: -0.110 to +0.110 (depth 0.220).
    """
    bm = bmesh.new()
    
    # 1. Stone arch frame / surround (outer width 0.950, total height 1.750, depth 0.220)
    # Left stone jamb (x: -0.475 to -0.375, z: -0.875 to 0.400)
    res_jamb_l = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.100, 0.220, 1.275), verts=res_jamb_l['verts'])
    bmesh.ops.translate(bm, vec=(-0.425, 0.000, -0.2375), verts=res_jamb_l['verts'])
    
    # Right stone jamb (x: 0.375 to 0.475, z: -0.875 to 0.400)
    res_jamb_r = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.100, 0.220, 1.275), verts=res_jamb_r['verts'])
    bmesh.ops.translate(bm, vec=(0.425, 0.000, -0.2375), verts=res_jamb_r['verts'])
    
    # Top arch header / surround (z: 0.400 to 0.875)
    # Arched surround built as solid box segments
    n_arch = 8
    r_arch_out = 0.475
    r_arch_in = 0.375
    z_spring = 0.400
    for a in range(n_arch):
        th1 = math.pi * a / n_arch
        th2 = math.pi * (a + 1) / n_arch
        c1, s1 = math.cos(th1), math.sin(th1)
        c2, s2 = math.cos(th2), math.sin(th2)
        
        p1_in = Vector((-r_arch_in * c1, -0.110, z_spring + r_arch_in * s1))
        p2_in = Vector((-r_arch_in * c2, -0.110, z_spring + r_arch_in * s2))
        p1_out = Vector((-r_arch_out * c1, -0.110, min(0.875, z_spring + r_arch_out * s1)))
        p2_out = Vector((-r_arch_out * c2, -0.110, min(0.875, z_spring + r_arch_out * s2)))
        
        q1_in = Vector((p1_in.x, 0.110, p1_in.z))
        q2_in = Vector((p2_in.x, 0.110, p2_in.z))
        q1_out = Vector((p1_out.x, 0.110, p1_out.z))
        q2_out = Vector((p2_out.x, 0.110, p2_out.z))
        
        v_f1 = bm.verts.new(p1_in); v_f2 = bm.verts.new(p2_in)
        v_f3 = bm.verts.new(p2_out); v_f4 = bm.verts.new(p1_out)
        v_b1 = bm.verts.new(q1_in); v_b2 = bm.verts.new(q2_in)
        v_b3 = bm.verts.new(q2_out); v_b4 = bm.verts.new(q1_out)
        
        bm.faces.new([v_f1, v_f2, v_f3, v_f4])
        bm.faces.new([v_b4, v_b3, v_b2, v_b1])
        bm.faces.new([v_f1, v_b1, v_b2, v_f2])
        bm.faces.new([v_f3, v_b3, v_b4, v_f4])
        bm.faces.new([v_f4, v_b4, v_b1, v_f1])
        bm.faces.new([v_f2, v_b2, v_b3, v_f3])

    # 2. Main wooden plank door panel (width 0.750, height 1.600, depth 0.060, recessed at local y = 0.020)
    # Vertical planks (5 planks across width 0.750)
    n_planks = 5
    plank_w = 0.740 / n_planks
    for p in range(n_planks):
        px = -0.370 + (p + 0.5) * plank_w
        # Main rectangular lower portion (z: -0.875 to 0.400)
        res_p = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(plank_w - 0.005, 0.060, 1.275), verts=res_p['verts'])
        bmesh.ops.translate(bm, vec=(px, 0.010, -0.2375), verts=res_p['verts'])
        
    # Top arched door fill
    res_arch_door = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=16,
        radius1=0.370,
        radius2=0.370,
        depth=0.060
    )
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi / 2.0, 4, 'X'), verts=res_arch_door['verts'])
    bmesh.ops.translate(bm, vec=(0.000, 0.010, 0.400), verts=res_arch_door['verts'])

    # 3. Forged iron strap hinges (2 horizontal black iron bars) and ring handle
    for hz in [-0.400, 0.200]:
        res_hinge = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.600, 0.015, 0.040), verts=res_hinge['verts'])
        bmesh.ops.translate(bm, vec=(-0.050, -0.025, hz), verts=res_hinge['verts'])
        
    # Iron latch handle plate (attached to the plank)
    res_handle = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.060, 0.040, 0.120), verts=res_handle['verts'])
    bmesh.ops.translate(bm, vec=(0.220, -0.025, -0.150), verts=res_handle['verts'])

    me = bpy.data.meshes.new("EntranceDoor")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("EntranceDoor", me)
    obj.location = (0.000, -2.100, 0.900)
    bpy.context.scene.collection.objects.link(obj)
    
    # Material: rustic dark wood & iron
    mat_door = make_material("RusticDoorMat", (0.25, 0.17, 0.11), roughness=0.7)
    obj.data.materials.append(mat_door)
    
    return obj
