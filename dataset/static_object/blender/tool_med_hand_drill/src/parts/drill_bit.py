"""DrillBit — Twist drill bit accessory (part module; imported by src/model.py).

Standard helical fluted high-speed steel twist drill bit clamped inside the chuck and protruding along the negative Y forward axis.
Material: Polished high-speed steel with titanium nitride golden flutes.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -0.150, 0.190) extents (0.008, 0.060, 0.008)
# x in [-0.004, 0.004] (diameter 0.008)
# y in [-0.180, -0.120] (length 0.060)
# z in [0.186, 0.194]

def make_material(name, rgb, roughness=0.15, metallic=0.98):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_drill_bit():
    mat_steel = make_material("PolishedSteelBit", (0.85, 0.85, 0.88), roughness=0.15, metallic=0.98)


    bm = bmesh.new()

    # Twist drill bit along Y: y from -0.120 (base clamped in chuck) to -0.180 (drill tip)
    # Diameter = 0.008 (radius = 0.004)
    # A helical twist bit: two flutes twisting along Y
    # Number of slices along Y:
    n_slices = 32
    y_start = -0.120
    y_end = -0.176  # start of 118-degree cutting point cone
    y_tip = -0.180  # very tip point

    r_shank = 0.0040
    flute_depth = 0.0018  # flute indent

    n_pts = 16  # around circle

    rings = []
    for s in range(n_slices + 1):
        frac = s / n_slices
        y = y_start + (y_end - y_start) * frac
        # Twist angle along drill length
        twist = frac * 4 * math.pi  # 2 full turns
        ring = []
        for i in range(n_pts):
            theta = 2 * math.pi * i / n_pts
            rel_angle = (theta - twist) % (2 * math.pi)
            # Flutes at 0 and pi
            flute_factor = abs(math.sin(rel_angle))
            # Smoothly shape the two helical grooves
            r = r_shank - flute_depth * (1.0 - flute_factor**2)
            if r < 0.0020:
                r = 0.0020
            px = r * math.cos(theta)
            pz = 0.190 + r * math.sin(theta)
            ring.append(bm.verts.new((px, y, pz)))
        rings.append(ring)

    for s in range(n_slices):
        r1 = rings[s]
        r2 = rings[s + 1]
        for i in range(n_pts):
            i_next = (i + 1) % n_pts
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    # Base cap (at y = -0.120)
    bm.faces.new([rings[0][i] for i in range(n_pts)])

    # Conical drill tip from y_end (-0.176) to y_tip (-0.180)
    tip_vert = bm.verts.new((0.0, y_tip, 0.190))
    last_ring = rings[-1]
    for i in range(n_pts):
        i_next = (i + 1) % n_pts
        bm.faces.new([last_ring[i], last_ring[i_next], tip_vert])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("DrillBit")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("DrillBit", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat_steel)

    return obj
