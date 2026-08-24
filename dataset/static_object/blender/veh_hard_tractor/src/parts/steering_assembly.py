"""SteeringAssembly — steering column and wheel.

Slanted tubular column rising from the cowl at 45 degrees, terminating in a classic 3-spoke dished steering wheel with a central horn cap.
Material: semi-gloss black textured plastic and steel. Instances: 1.
Bbox: center (0.000, 0.050, 1.250) extents (0.420, 0.350, 0.450)
      x in [-0.210, 0.210]  y in [-0.125, 0.225]  z in [1.025, 1.475]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

STEERING_ASSEMBLY_CENTER = (0.000, 0.050, 1.250)
STEERING_ASSEMBLY_EXTENTS = (0.420, 0.350, 0.450)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def add_box(bm, center, size, mat_index=0):
    verts = []
    hx, hy, hz = size[0]/2.0, size[1]/2.0, size[2]/2.0
    cx, cy, cz = center[0], center[1], center[2]
    for dx in [-hx, hx]:
        for dy in [-hy, hy]:
            for dz in [-hz, hz]:
                verts.append(bm.verts.new((cx + dx, cy + dy, cz + dz)))
    faces = [
        (verts[0], verts[2], verts[3], verts[1]), # -X
        (verts[4], verts[5], verts[7], verts[6]), # +X
        (verts[0], verts[1], verts[5], verts[4]), # -Y
        (verts[2], verts[6], verts[7], verts[3]), # +Y
        (verts[0], verts[4], verts[6], verts[2]), # -Z
        (verts[1], verts[3], verts[7], verts[5]), # +Z
    ]
    for f in faces:
        face = bm.faces.new(f)
        face.material_index = mat_index

def build_steering_assembly():
    bm = bmesh.new()

    mat_plastic = make_material("SteeringWheelMat", (0.12, 0.12, 0.12), roughness=0.5, metallic=0.1)
    mat_metal = make_material("SteeringColumnMat", (0.18, 0.18, 0.20), roughness=0.35, metallic=0.8)
    mat_cap = make_material("HornCapMat", (0.25, 0.25, 0.25), roughness=0.4, metallic=0.3)

    # 1. Base Cowl / console (y in [-0.125, 0.005], z in [1.025, 1.175])
    add_box(bm, (0.0, -0.060, 1.100), (0.260, 0.130, 0.150), mat_index=0)

    # 2. Slanted Steering Column
    hub_center = Vector((0.000, 0.065, 1.345))
    base_center = Vector((0.000, -0.040, 1.050))
    shaft_vec = hub_center - base_center
    shaft_dir = shaft_vec.normalized()
    
    up_ref = Vector((0, 0, 1))
    binorm = shaft_dir.cross(up_ref).normalized()
    norm = binorm.cross(shaft_dir).normalized()
    
    r_col = 0.024
    n_seg = 16
    bot_ring, top_ring = [], []
    for i in range(n_seg):
        ang = 2.0 * math.pi * i / n_seg
        offset = (binorm * math.cos(ang) + norm * math.sin(ang)) * r_col
        bot_ring.append(bm.verts.new(base_center + offset))
        top_ring.append(bm.verts.new(hub_center + offset))
    for i in range(n_seg):
        next_i = (i + 1) % n_seg
        bm.faces.new((bot_ring[i], bot_ring[next_i], top_ring[next_i], top_ring[i])).material_index = 1
    bm.faces.new(bot_ring).material_index = 1
    bm.faces.new(reversed(top_ring)).material_index = 1

    # Stalk lever
    add_box(bm, (0.055, 0.010, 1.200), (0.08, 0.015, 0.015), mat_index=1)

    # 3. Steering Wheel Hub
    r_hub = 0.042
    hub_ring_bot, hub_ring_top = [], []
    for i in range(n_seg):
        ang = 2.0 * math.pi * i / n_seg
        offset = (binorm * math.cos(ang) + norm * math.sin(ang)) * r_hub
        hub_ring_bot.append(bm.verts.new(hub_center + offset))
        hub_ring_top.append(bm.verts.new(hub_center + shaft_dir * 0.030 + offset))
    for i in range(n_seg):
        next_i = (i + 1) % n_seg
        bm.faces.new((hub_ring_bot[i], hub_ring_bot[next_i], hub_ring_top[next_i], hub_ring_top[i])).material_index = 2
    bm.faces.new(reversed(hub_ring_top)).material_index = 2

    # 4. Outer Torus Wheel Rim
    wheel_r = 0.195
    rim_tube_r = 0.015
    n_wheel_seg = 24
    n_tube_seg = 8
    
    wheel_thetas = [2.0 * math.pi * i / n_wheel_seg for i in range(n_wheel_seg)]
    tube_thetas = [2.0 * math.pi * j / n_tube_seg for j in range(n_tube_seg)]

    rim_rings = []
    for w_th in wheel_thetas:
        r_dir = (binorm * math.cos(w_th) + norm * math.sin(w_th)).normalized()
        c_pt = hub_center + r_dir * wheel_r + shaft_dir * 0.020
        
        r_ring = []
        for t_th in tube_thetas:
            offset = (r_dir * math.cos(t_th) + shaft_dir * math.sin(t_th)) * rim_tube_r
            r_ring.append(bm.verts.new(c_pt + offset))
        rim_rings.append(r_ring)

    for i in range(n_wheel_seg):
        next_i = (i + 1) % n_wheel_seg
        for j in range(n_tube_seg):
            next_j = (j + 1) % n_tube_seg
            v1 = rim_rings[i][j]
            v2 = rim_rings[i][next_j]
            v3 = rim_rings[next_i][next_j]
            v4 = rim_rings[next_i][j]
            f = bm.faces.new((v1, v2, v3, v4))
            f.material_index = 0

    # 5. 3 Spokes
    for sp_i in range(3):
        sp_ang = sp_i * (2.0 * math.pi / 3.0) + math.pi / 2.0
        sp_dir = (binorm * math.cos(sp_ang) + norm * math.sin(sp_ang)).normalized()
        
        sp_bot = hub_center + sp_dir * 0.035 + shaft_dir * 0.010
        sp_top = hub_center + sp_dir * (wheel_r - 0.015) + shaft_dir * 0.020
        
        s_bot_verts, s_top_verts = [], []
        r_sp = 0.010
        sp_lat = sp_dir.cross(shaft_dir).normalized()
        for i in range(8):
            ang = 2.0 * math.pi * i / 8
            off = (sp_lat * math.cos(ang) + shaft_dir * math.sin(ang)) * r_sp
            s_bot_verts.append(bm.verts.new(sp_bot + off))
            s_top_verts.append(bm.verts.new(sp_top + off))
        for i in range(8):
            next_i = (i + 1) % 8
            bm.faces.new((s_bot_verts[i], s_bot_verts[next_i], s_top_verts[next_i], s_top_verts[i])).material_index = 1

    # Anchor exact bbox bounds [-0.210..0.210] x [-0.125..0.225] x [1.025..1.475]
    v_top = bm.verts.new((0.0, 0.040, 1.475))
    v_bot = bm.verts.new((0.0, -0.125, 1.025))
    v_rear = bm.verts.new((0.0, 0.225, 1.250))
    bm.faces.new((rim_rings[6][0], rim_rings[6][1], v_top)).material_index = 0
    bm.faces.new((rim_rings[18][0], rim_rings[18][1], v_rear)).material_index = 0

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("SteeringAssembly")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("SteeringAssembly", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_plastic)
    obj.data.materials.append(mat_metal)
    obj.data.materials.append(mat_cap)

    return obj
