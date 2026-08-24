"""EngineHood — engine enclosure and front nose.

Streamlined agricultural hood box with rounded front nose taper, front intake grille with horizontal slats, side service access seams, and front circular headlight recesses.
Material: glossy agricultural red painted sheet metal. Instances: 1.
Bbox: center (0.000, -0.750, 1.050) extents (0.600, 1.300, 0.650)
      x in [-0.300, 0.300]  y in [-1.400, -0.100]  z in [0.725, 1.375]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

ENGINE_HOOD_CENTER = (0.000, -0.750, 1.050)
ENGINE_HOOD_EXTENTS = (0.600, 1.300, 0.650)

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
        (verts[0], verts[2], verts[3], verts[1]),
        (verts[4], verts[5], verts[7], verts[6]),
        (verts[0], verts[1], verts[5], verts[4]),
        (verts[2], verts[6], verts[7], verts[3]),
        (verts[0], verts[4], verts[6], verts[2]),
        (verts[1], verts[3], verts[7], verts[5]),
    ]
    for f in faces:
        face = bm.faces.new(f)
        face.material_index = mat_index

def build_engine_hood():
    bm = bmesh.new()

    mat_red = make_material("AgriRedMat", (0.80, 0.05, 0.05), roughness=0.25, metallic=0.1)
    mat_grille = make_material("GrilleBlackMat", (0.12, 0.12, 0.12), roughness=0.6, metallic=0.3)
    mat_light = make_material("HeadlightGlassMat", (0.95, 0.95, 0.90), roughness=0.1, metallic=0.1)
    mat_chrome = make_material("ChromeBezelMat", (0.90, 0.90, 0.92), roughness=0.15, metallic=0.95)

    stations = [
        # y, half_w, z_bot, z_top, chamf, crown
        (-0.100, 0.300, 0.725, 1.365, 0.060, 0.010),
        (-0.750, 0.290, 0.725, 1.365, 0.060, 0.010),
        (-1.280, 0.270, 0.725, 1.355, 0.060, 0.010),
        (-1.400, 0.250, 0.725, 1.325, 0.050, 0.005),
    ]

    ring_verts = []
    for (y_val, hw, z_b, z_t, ch, cr) in stations:
        row = [
            bm.verts.new((-hw, y_val, z_b)),
            bm.verts.new((-hw, y_val, z_t - ch)),
            bm.verts.new((-hw + ch, y_val, z_t)),
            bm.verts.new((0.0, y_val, z_t + cr)),
            bm.verts.new((hw - ch, y_val, z_t)),
            bm.verts.new((hw, y_val, z_t - ch)),
            bm.verts.new((hw, y_val, z_b)),
            bm.verts.new((0.0, y_val, z_b)),
        ]
        ring_verts.append(row)

    # Loft faces along the stations
    for s in range(len(stations) - 1):
        s_next = s + 1
        for p in range(len(ring_verts[0])):
            p_next = (p + 1) % len(ring_verts[0])
            v1 = ring_verts[s][p]
            v2 = ring_verts[s][p_next]
            v3 = ring_verts[s_next][p_next]
            v4 = ring_verts[s_next][p]
            f = bm.faces.new((v1, v2, v3, v4))
            f.material_index = 0

    # Rear cap at y = -0.100
    r0 = ring_verts[0]
    bm.faces.new((r0[0], r0[7], r0[3], r0[2], r0[1])).material_index = 0
    bm.faces.new((r0[7], r0[6], r0[5], r0[4], r0[3])).material_index = 0

    # Front nose cap at y = -1.400
    rf = ring_verts[-1]
    f_grille1 = bm.faces.new((rf[1], rf[2], rf[3], rf[7], rf[0]))
    f_grille1.material_index = 1
    f_grille2 = bm.faces.new((rf[3], rf[4], rf[5], rf[6], rf[7]))
    f_grille2.material_index = 1

    # Horizontal Grille Slats
    n_slats = 6
    slat_z_start = 0.820
    slat_z_end = 1.220
    for sl in range(n_slats):
        z_slat = slat_z_start + (slat_z_end - slat_z_start) * sl / (n_slats - 1)
        w_slat = 0.380 - (sl * 0.02)
        add_box(bm, (0.0, -1.402, z_slat), (w_slat, 0.025, 0.018), mat_index=1)

    # Dual Front Headlights
    for sign_x in [-1.0, 1.0]:
        x_hl = 0.160 * sign_x
        y_hl = -1.400
        z_hl = 1.200
        
        # Chrome bezel ring
        n_seg = 16
        b_bot, b_top = [], []
        for i in range(n_seg):
            ang = 2.0 * math.pi * i / n_seg
            b_bot.append(bm.verts.new((x_hl + 0.052 * math.cos(ang), y_hl - 0.025, z_hl + 0.052 * math.sin(ang))))
            b_top.append(bm.verts.new((x_hl + 0.052 * math.cos(ang), y_hl + 0.005, z_hl + 0.052 * math.sin(ang))))
        for i in range(n_seg):
            next_i = (i + 1) % n_seg
            bm.faces.new((b_bot[i], b_bot[next_i], b_top[next_i], b_top[i])).material_index = 3
        bm.faces.new(b_bot).material_index = 3

        # Glass lens dome
        l_ring = []
        for i in range(n_seg):
            ang = 2.0 * math.pi * i / n_seg
            l_ring.append(bm.verts.new((x_hl + 0.045 * math.cos(ang), y_hl - 0.020, z_hl + 0.045 * math.sin(ang))))
        l_center = bm.verts.new((x_hl, y_hl - 0.035, z_hl))
        for i in range(n_seg):
            next_i = (i + 1) % n_seg
            bm.faces.new((l_ring[i], l_ring[next_i], l_center)).material_index = 2

    # Side badging trim
    for sign_x in [-1.0, 1.0]:
        x_badge = 0.292 * sign_x
        add_box(bm, (x_badge, -0.700, 1.240), (0.015, 0.700, 0.040), mat_index=3)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("EngineHood")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("EngineHood", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_red)
    obj.data.materials.append(mat_grille)
    obj.data.materials.append(mat_light)
    obj.data.materials.append(mat_chrome)

    return obj
