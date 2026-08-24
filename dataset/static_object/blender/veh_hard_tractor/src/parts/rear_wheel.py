"""RearWheel — primary rear drive wheels with heavy lug treads.

Massive 1.4m diameter tractor tire with deep angled V-shaped (chevron / herringbone) agricultural lug treads and an inset 6-bolt deep-dish stamped steel rim.
Material: matte dark grey rubber with cream steel rims. Instances: 2 (mirror_x).
Bbox: center (0.780, 0.700, 0.700) extents (0.340, 1.400, 1.400)
      x in [0.610, 0.950]  y in [0.000, 1.400]  z in [0.000, 1.400]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

REAR_WHEEL_CENTER = (0.780, 0.700, 0.700)
REAR_WHEEL_EXTENTS = (0.340, 1.400, 1.400)

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

def create_rear_wheel_mesh(name_prefix, x_center, sign_x):
    """
    Tire center: (x_center, 0.700, 0.700). Outer radius 0.70m, diameter 1.40m.
    Width: 0.34m (x from x_center - 0.17 to x_center + 0.17).
    """
    bm = bmesh.new()
    y_c, z_c = 0.700, 0.700
    outer_r = 0.700
    base_tire_r = 0.655
    rim_r = 0.420
    hub_r = 0.160
    axle_r = 0.070
    w = 0.340
    half_w = w / 2.0  # 0.170

    # Materials
    mat_rubber = make_material("RubberTireMat", (0.08, 0.08, 0.08), roughness=0.85, metallic=0.0)
    mat_rim = make_material("CreamRimMat", (0.92, 0.88, 0.76), roughness=0.35, metallic=0.1)
    mat_hub = make_material("HubIronMat", (0.15, 0.15, 0.15), roughness=0.4, metallic=0.7)

    # 1. Base tire casing
    n_theta = 36
    thetas = [2.0 * math.pi * i / n_theta for i in range(n_theta)]

    profile = [
        (-half_w + 0.02, rim_r),
        (-half_w, rim_r + 0.08),
        (-half_w, base_tire_r - 0.04),
        (-half_w + 0.03, base_tire_r),
        (half_w - 0.03, base_tire_r),
        (half_w, base_tire_r - 0.04),
        (half_w, rim_r + 0.08),
        (half_w - 0.02, rim_r),
    ]

    ring_verts = []
    for th in thetas:
        cos_t = math.cos(th)
        sin_t = math.sin(th)
        row = []
        for x_rel, r in profile:
            vx = x_center + x_rel
            vy = y_c + r * cos_t
            vz = z_c + r * sin_t
            row.append(bm.verts.new((vx, vy, vz)))
        ring_verts.append(row)

    for i in range(n_theta):
        next_i = (i + 1) % n_theta
        for j in range(len(profile) - 1):
            v1 = ring_verts[i][j]
            v2 = ring_verts[i][j+1]
            v3 = ring_verts[next_i][j+1]
            v4 = ring_verts[next_i][j]
            f = bm.faces.new((v1, v2, v3, v4))
            f.material_index = 0

    # 2. Chevron Lug Treads
    n_lugs = 18
    lug_thickness_ang = (2.0 * math.pi / n_lugs) * 0.28
    
    for i in range(n_lugs):
        base_ang = 2.0 * math.pi * i / n_lugs
        sweep = 0.22
        ang1 = base_ang
        ang2 = base_ang + sweep
        
        x_in = x_center - half_w + 0.02
        x_mid = x_center + 0.02
        
        v_l_b1 = bm.verts.new((x_in, y_c + base_tire_r * math.cos(ang1), z_c + base_tire_r * math.sin(ang1)))
        v_l_b2 = bm.verts.new((x_in, y_c + base_tire_r * math.cos(ang1 + lug_thickness_ang), z_c + base_tire_r * math.sin(ang1 + lug_thickness_ang)))
        v_l_b3 = bm.verts.new((x_mid, y_c + base_tire_r * math.cos(ang2 + lug_thickness_ang), z_c + base_tire_r * math.sin(ang2 + lug_thickness_ang)))
        v_l_b4 = bm.verts.new((x_mid, y_c + base_tire_r * math.cos(ang2), z_c + base_tire_r * math.sin(ang2)))

        v_l_t1 = bm.verts.new((x_in, y_c + outer_r * math.cos(ang1), z_c + outer_r * math.sin(ang1)))
        v_l_t2 = bm.verts.new((x_in, y_c + outer_r * math.cos(ang1 + lug_thickness_ang), z_c + outer_r * math.sin(ang1 + lug_thickness_ang)))
        v_l_t3 = bm.verts.new((x_mid, y_c + outer_r * math.cos(ang2 + lug_thickness_ang), z_c + outer_r * math.sin(ang2 + lug_thickness_ang)))
        v_l_t4 = bm.verts.new((x_mid, y_c + outer_r * math.cos(ang2), z_c + outer_r * math.sin(ang2)))

        faces_lug = [
            (v_l_t1, v_l_t2, v_l_t3, v_l_t4),
            (v_l_b1, v_l_t1, v_l_t4, v_l_b4),
            (v_l_b2, v_l_b3, v_l_t3, v_l_t2),
            (v_l_b1, v_l_b2, v_l_t2, v_l_t1),
            (v_l_b4, v_l_t4, v_l_t3, v_l_b3),
        ]
        for fv in faces_lug:
            f = bm.faces.new(fv)
            f.material_index = 0

        ang_r_base = base_ang + math.pi / n_lugs
        ang_r1 = ang_r_base
        ang_r2 = ang_r_base + sweep
        
        x_out = x_center + half_w - 0.02
        x_mid_r = x_center - 0.02
        
        v_r_b1 = bm.verts.new((x_out, y_c + base_tire_r * math.cos(ang_r1), z_c + base_tire_r * math.sin(ang_r1)))
        v_r_b2 = bm.verts.new((x_out, y_c + base_tire_r * math.cos(ang_r1 + lug_thickness_ang), z_c + base_tire_r * math.sin(ang_r1 + lug_thickness_ang)))
        v_r_b3 = bm.verts.new((x_mid_r, y_c + base_tire_r * math.cos(ang_r2 + lug_thickness_ang), z_c + base_tire_r * math.sin(ang_r2 + lug_thickness_ang)))
        v_r_b4 = bm.verts.new((x_mid_r, y_c + base_tire_r * math.cos(ang_r2), z_c + base_tire_r * math.sin(ang_r2)))

        v_r_t1 = bm.verts.new((x_out, y_c + outer_r * math.cos(ang_r1), z_c + outer_r * math.sin(ang_r1)))
        v_r_t2 = bm.verts.new((x_out, y_c + outer_r * math.cos(ang_r1 + lug_thickness_ang), z_c + outer_r * math.sin(ang_r1 + lug_thickness_ang)))
        v_r_t3 = bm.verts.new((x_mid_r, y_c + outer_r * math.cos(ang_r2 + lug_thickness_ang), z_c + outer_r * math.sin(ang_r2 + lug_thickness_ang)))
        v_r_t4 = bm.verts.new((x_mid_r, y_c + outer_r * math.cos(ang_r2), z_c + outer_r * math.sin(ang_r2)))

        faces_lug_r = [
            (v_r_t1, v_r_t4, v_r_t3, v_r_t2),
            (v_r_b1, v_r_b4, v_r_t4, v_r_t1),
            (v_r_b2, v_r_t2, v_r_t3, v_r_b3),
            (v_r_b1, v_r_t1, v_r_t2, v_r_b2),
            (v_r_b4, v_r_b3, v_r_t3, v_r_t4),
        ]
        for fv in faces_lug_r:
            f = bm.faces.new(fv)
            f.material_index = 0

    # 3. Steel Wheel Rim (Cream)
    rim_dish_x = x_center + (0.04 * sign_x)
    rim_outer_x = x_center + (half_w - 0.04) * sign_x
    rim_inner_x = x_center - (half_w - 0.04) * sign_x
    
    rim_outer_ring = []
    rim_dish_ring = []
    hub_ring = []
    axle_ring = []
    
    for th in thetas:
        cos_t = math.cos(th)
        sin_t = math.sin(th)
        rim_outer_ring.append(bm.verts.new((rim_outer_x, y_c + rim_r * cos_t, z_c + rim_r * sin_t)))
        rim_dish_ring.append(bm.verts.new((rim_dish_x, y_c + (rim_r - 0.06) * cos_t, z_c + (rim_r - 0.06) * sin_t)))
        hub_ring.append(bm.verts.new((rim_dish_x, y_c + hub_r * cos_t, z_c + hub_r * sin_t)))
        axle_ring.append(bm.verts.new((rim_dish_x + 0.05 * sign_x, y_c + axle_r * cos_t, z_c + axle_r * sin_t)))

    rim_inner_ring = []
    for th in thetas:
        cos_t = math.cos(th)
        sin_t = math.sin(th)
        rim_inner_ring.append(bm.verts.new((rim_inner_x, y_c + rim_r * cos_t, z_c + rim_r * sin_t)))

    for i in range(n_theta):
        next_i = (i + 1) % n_theta
        f = bm.faces.new((rim_inner_ring[i], rim_inner_ring[next_i], rim_outer_ring[next_i], rim_outer_ring[i]))
        f.material_index = 1
        f = bm.faces.new((rim_outer_ring[i], rim_outer_ring[next_i], rim_dish_ring[next_i], rim_dish_ring[i]))
        f.material_index = 1
        f = bm.faces.new((rim_dish_ring[i], rim_dish_ring[next_i], hub_ring[next_i], hub_ring[i]))
        f.material_index = 1
        f = bm.faces.new((hub_ring[i], hub_ring[next_i], axle_ring[next_i], axle_ring[i]))
        f.material_index = 2

    # Axle cap center
    center_v = bm.verts.new((rim_dish_x + 0.06 * sign_x, y_c, z_c))
    for i in range(n_theta):
        next_i = (i + 1) % n_theta
        f = bm.faces.new((axle_ring[i], axle_ring[next_i], center_v))
        f.material_index = 2

    # 6 Lug Nuts
    n_bolts = 6
    bolt_r = 0.115
    for b in range(n_bolts):
        b_ang = 2.0 * math.pi * b / n_bolts
        bx = rim_dish_x + 0.015 * sign_x
        by = y_c + bolt_r * math.cos(b_ang)
        bz = z_c + bolt_r * math.sin(b_ang)
        b_verts = []
        for h in range(6):
            h_ang = 2.0 * math.pi * h / 6
            b_verts.append(bm.verts.new((bx, by + 0.018 * math.cos(h_ang), bz + 0.018 * math.sin(h_ang))))
        f = bm.faces.new(b_verts)
        f.material_index = 2

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name_prefix)
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new(name_prefix, me)
    bpy.context.scene.collection.objects.link(obj)
    
    obj.data.materials.append(mat_rubber)
    obj.data.materials.append(mat_rim)
    obj.data.materials.append(mat_hub)
    
    return obj

def build_rear_wheel():
    objs = []
    objs.append(create_rear_wheel_mesh("RearWheel_0", 0.780, sign_x=1.0))
    objs.append(create_rear_wheel_mesh("RearWheel_1", -0.780, sign_x=-1.0))
    return objs
