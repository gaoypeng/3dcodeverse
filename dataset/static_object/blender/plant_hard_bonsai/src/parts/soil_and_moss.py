"""SoilAndMoss — potting soil bed and live green moss carpet (part module; imported by src/model.py).

Slightly domed oval mound filling the interior of the tray up to 10 mm above the pot rim with organic surface variation and velvety moss patches.
Material: vibrant green velvety moss with dark rich soil undertones.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

SOIL_AND_MOSS_CENTER = (0.000, 0.000, 0.038)
SOIL_AND_MOSS_EXTENTS = (0.320, 0.220, 0.030)

def make_material(name, rgb, roughness=0.9, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_soil_and_moss() -> bpy.types.Object:
    bm = bmesh.new()

    # Domed oval mound with an open collar matching the rock:
    n_rings = 8
    n_seg = 48
    rx_outer = 0.160
    ry_outer = 0.110
    rx_inner = 0.068
    ry_inner = 0.057
    cx_rock, cy_rock = -0.020, 0.010
    z_outer = 0.023
    z_peak = 0.048
    z_inner = 0.029

    rings_top = []
    for r in range(n_rings):
        frac = r / (n_rings - 1)  # 0 at outer edge, 1 at rock inner collar
        z = z_outer + (z_peak - z_outer) * math.sin(frac * math.pi) ** 0.8
        if frac > 0.8:
            z = z * (1.0 - (frac - 0.8)/0.2) + z_inner * ((frac - 0.8)/0.2)

        ring = []
        for i in range(n_seg):
            theta = 2.0 * math.pi * i / n_seg
            rx = rx_outer * (1.0 - frac) + rx_inner * frac
            ry = ry_outer * (1.0 - frac) + ry_inner * frac
            cx = 0.0 * (1.0 - frac) + cx_rock * frac
            cy = 0.0 * (1.0 - frac) + cy_rock * frac

            noise = 0.0015 * math.sin(4 * theta + frac * 4.0) * math.cos(3 * theta)
            vx = cx + rx * math.cos(theta)
            vy = cy + ry * math.sin(theta)
            vz = z + noise

            vx = max(-0.160, min(0.160, vx))
            vy = max(-0.110, min(0.110, vy))
            vz = max(0.023, min(0.053, vz))
            ring.append(bm.verts.new((vx, vy, vz)))
        rings_top.append(ring)

    # Bridge top surface
    for r in range(n_rings - 1):
        r1 = rings_top[r]
        r2 = rings_top[r+1]
        for i in range(n_seg):
            i_next = (i + 1) % n_seg
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    # Bottom surface (same rings projected to z = 0.023)
    rings_bot = []
    for r in range(n_rings):
        ring = []
        for i in range(n_seg):
            v_t = rings_top[r][i]
            ring.append(bm.verts.new((v_t.co.x, v_t.co.y, 0.023)))
        rings_bot.append(ring)

    # Bridge bottom surface (reversed winding)
    for r in range(n_rings - 1):
        r1 = rings_bot[r]
        r2 = rings_bot[r+1]
        for i in range(n_seg):
            i_next = (i + 1) % n_seg
            bm.faces.new([r1[i], r2[i], r2[i_next], r1[i_next]])

    # Outer vertical rim
    r_top_o = rings_top[0]
    r_bot_o = rings_bot[0]
    for i in range(n_seg):
        i_next = (i + 1) % n_seg
        bm.faces.new([r_top_o[i], r_bot_o[i], r_bot_o[i_next], r_top_o[i_next]])

    # Inner vertical rim (at rock collar)
    r_top_i = rings_top[-1]
    r_bot_i = rings_bot[-1]
    for i in range(n_seg):
        i_next = (i + 1) % n_seg
        bm.faces.new([r_top_i[i], r_top_i[i_next], r_bot_i[i_next], r_bot_i[i]])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("SoilAndMoss")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("SoilAndMoss", me)
    bpy.context.scene.collection.objects.link(obj)

    mat = make_material("SoilAndMossMat", (0.16, 0.38, 0.08), roughness=0.95, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
