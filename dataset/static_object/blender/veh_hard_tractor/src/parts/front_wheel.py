"""FrontWheel — front steering wheels with directional tread.

Compact 0.7m diameter front guide tire featuring three continuous circumferential ribs/grooves mounted on an offset steel hub.
Material: matte dark grey rubber with cream steel rims. Instances: 2 (mirror_x).
Bbox: center (0.620, -1.050, 0.350) extents (0.220, 0.700, 0.700)
      x in [0.510, 0.730]  y in [-1.400, -0.700]  z in [0.000, 0.700]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

FRONT_WHEEL_CENTER = (0.620, -1.050, 0.350)
FRONT_WHEEL_EXTENTS = (0.220, 0.700, 0.700)

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

def create_front_wheel_mesh(name_prefix, x_center, sign_x):
    """
    Tire center: (x_center, -1.050, 0.350). Outer radius 0.35m, diameter 0.70m.
    Width: 0.22m (x from x_center - 0.11 to x_center + 0.11).
    Three-rib agricultural front guide tire tread.
    """
    bm = bmesh.new()
    y_c, z_c = -1.050, 0.350
    outer_r = 0.350
    groove_r = 0.335
    sidewall_r = 0.320
    rim_r = 0.220
    hub_r = 0.090
    axle_r = 0.045
    w = 0.220
    half_w = w / 2.0  # 0.110

    # Materials
    mat_rubber = make_material("RubberTireMat", (0.08, 0.08, 0.08), roughness=0.85, metallic=0.0)
    mat_rim = make_material("CreamRimMat", (0.92, 0.88, 0.76), roughness=0.35, metallic=0.1)
    mat_hub = make_material("HubIronMat", (0.15, 0.15, 0.15), roughness=0.4, metallic=0.7)

    # 3 continuous circumferential ribs profile across X:
    # 3 ribs with 2 grooves in between and rounded shoulders
    # Profile points (x_rel, r) from inside to outside
    profile = [
        (-half_w + 0.015, rim_r),
        (-half_w, rim_r + 0.04),
        (-half_w, sidewall_r),
        (-half_w + 0.02, outer_r - 0.01),
        # Rib 1 (inner)
        (-0.065, outer_r),
        (-0.045, outer_r),
        # Groove 1
        (-0.035, groove_r),
        (-0.025, groove_r),
        # Rib 2 (center)
        (-0.015, outer_r),
        (0.015, outer_r),
        # Groove 2
        (0.025, groove_r),
        (0.035, groove_r),
        # Rib 3 (outer)
        (0.045, outer_r),
        (0.065, outer_r),
        # Shoulder
        (half_w - 0.02, outer_r - 0.01),
        (half_w, sidewall_r),
        (half_w, rim_r + 0.04),
        (half_w - 0.015, rim_r),
    ]

    n_theta = 32
    thetas = [2.0 * math.pi * i / n_theta for i in range(n_theta)]

    # Build rings of vertices for tire
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

    # Connect casing faces
    for i in range(n_theta):
        next_i = (i + 1) % n_theta
        for j in range(len(profile) - 1):
            v1 = ring_verts[i][j]
            v2 = ring_verts[i][j+1]
            v3 = ring_verts[next_i][j+1]
            v4 = ring_verts[next_i][j]
            f = bm.faces.new((v1, v2, v3, v4))
            f.material_index = 0

    # Stamped steel rim for front wheel
    rim_dish_x = x_center + (0.02 * sign_x)
    rim_outer_x = x_center + (half_w - 0.02) * sign_x
    rim_inner_x = x_center - (half_w - 0.02) * sign_x

    rim_outer_ring = []
    rim_dish_ring = []
    hub_ring = []
    axle_ring = []

    for th in thetas:
        cos_t = math.cos(th)
        sin_t = math.sin(th)
        rim_outer_ring.append(bm.verts.new((rim_outer_x, y_c + rim_r * cos_t, z_c + rim_r * sin_t)))
        rim_dish_ring.append(bm.verts.new((rim_dish_x, y_c + (rim_r - 0.04) * cos_t, z_c + (rim_r - 0.04) * sin_t)))
        hub_ring.append(bm.verts.new((rim_dish_x, y_c + hub_r * cos_t, z_c + hub_r * sin_t)))
        axle_ring.append(bm.verts.new((rim_dish_x + 0.035 * sign_x, y_c + axle_r * cos_t, z_c + axle_r * sin_t)))

    rim_inner_ring = []
    for th in thetas:
        cos_t = math.cos(th)
        sin_t = math.sin(th)
        rim_inner_ring.append(bm.verts.new((rim_inner_x, y_c + rim_r * cos_t, z_c + rim_r * sin_t)))

    for i in range(n_theta):
        next_i = (i + 1) % n_theta
        # Inner barrel
        f = bm.faces.new((rim_inner_ring[i], rim_inner_ring[next_i], rim_outer_ring[next_i], rim_outer_ring[i]))
        f.material_index = 1
        # Dish slope
        f = bm.faces.new((rim_outer_ring[i], rim_outer_ring[next_i], rim_dish_ring[next_i], rim_dish_ring[i]))
        f.material_index = 1
        # Dish flat to hub
        f = bm.faces.new((rim_dish_ring[i], rim_dish_ring[next_i], hub_ring[next_i], hub_ring[i]))
        f.material_index = 1
        # Hub step
        f = bm.faces.new((hub_ring[i], hub_ring[next_i], axle_ring[next_i], axle_ring[i]))
        f.material_index = 2

    # Axle cap center
    center_v = bm.verts.new((rim_dish_x + 0.045 * sign_x, y_c, z_c))
    for i in range(n_theta):
        next_i = (i + 1) % n_theta
        f = bm.faces.new((axle_ring[i], axle_ring[next_i], center_v))
        f.material_index = 2

    # 4 Lug bolts on front hub
    n_bolts = 4
    bolt_r = 0.065
    for b in range(n_bolts):
        b_ang = 2.0 * math.pi * b / n_bolts
        bx = rim_dish_x + 0.012 * sign_x
        by = y_c + bolt_r * math.cos(b_ang)
        bz = z_c + bolt_r * math.sin(b_ang)
        b_verts = []
        for h in range(6):
            h_ang = 2.0 * math.pi * h / 6
            b_verts.append(bm.verts.new((bx, by + 0.012 * math.cos(h_ang), bz + 0.012 * math.sin(h_ang))))
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

def build_front_wheel():
    """Builds FrontWheel_0 and FrontWheel_1."""
    objs = []
    # FrontWheel_0: right side (+X = 0.620), FrontWheel_1: left side (-X = -0.620)
    objs.append(create_front_wheel_mesh("FrontWheel_0", 0.620, sign_x=1.0))
    objs.append(create_front_wheel_mesh("FrontWheel_1", -0.620, sign_x=-1.0))
    return objs
