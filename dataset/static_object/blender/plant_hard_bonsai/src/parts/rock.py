"""Rock — rugged mineral crag anchored in the soil (part module; imported by src/model.py).

Asymmetric, weathered dark rock with deep vertical crevasses and sharp angular facets, embedded ~15 mm into the soil bed.
Material: dark slate grey rough porous stone with subtle mineral veining.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

ROCK_CENTER = (-0.020, 0.010, 0.100)
ROCK_EXTENTS = (0.130, 0.110, 0.140)
# bounds: x in [-0.085, 0.045], y in [-0.045, 0.065], z in [0.030, 0.170]

def make_material(name, rgb, roughness=0.85, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_rock() -> bpy.types.Object:
    bm = bmesh.new()

    cx, cy = -0.020, 0.010
    
    levels = [
        # (z, scale_x, scale_y, offset_x, offset_y)
        (0.028, 0.056, 0.046, 0.000, 0.000), # base at 0.028 touching soil
        (0.050, 0.062, 0.052, -0.003, 0.002),
        (0.080, 0.063, 0.051, -0.005, 0.004),
        (0.110, 0.060, 0.046, -0.008, 0.006),
        (0.140, 0.050, 0.038, -0.010, 0.008),
        (0.160, 0.036, 0.026, -0.012, 0.009),
        (0.170, 0.014, 0.010, -0.014, 0.010),
    ]
    
    n_seg = 24
    ang_noise = [
        0.16 * math.sin(3 * i) + 0.10 * math.cos(5 * i + 1.2) - 0.06 * math.sin(7 * i)
        for i in range(n_seg)
    ]

    rings = []
    for l_idx, (z, sx, sy, ox, oy) in enumerate(levels):
        ring = []
        for i in range(n_seg):
            theta = 2.0 * math.pi * i / n_seg
            n_val = ang_noise[i]
            ridge = 0.06 if i in [2, 7, 13, 18] else 0.0
            
            r_mult = 1.0 + n_val + ridge
            # Indent root pathways slightly
            if l_idx in [1, 2, 3, 4]:
                if abs(theta - 0.8) < 0.35 or abs(theta - 2.4) < 0.35 or abs(theta - 4.0) < 0.35 or abs(theta - 5.5) < 0.35:
                    r_mult *= 0.85
            
            vx = (cx + ox) + sx * r_mult * math.cos(theta)
            vy = (cy + oy) + sy * r_mult * math.sin(theta)
            vz = z

            vx = max(-0.085, min(0.045, vx))
            vy = max(-0.045, min(0.065, vy))
            vz = max(0.028, min(0.170, vz))
            ring.append(bm.verts.new((vx, vy, vz)))
        rings.append(ring)

    for l_idx in range(len(levels) - 1):
        r1 = rings[l_idx]
        r2 = rings[l_idx + 1]
        for i in range(n_seg):
            i_next = (i + 1) % n_seg
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    v_bot = bm.verts.new((cx, cy, 0.028))
    r_bot = rings[0]
    for i in range(n_seg):
        i_next = (i + 1) % n_seg
        bm.faces.new([v_bot, r_bot[i_next], r_bot[i]])

    v_top = bm.verts.new((cx - 0.014, cy + 0.010, 0.170))
    r_top = rings[-1]
    for i in range(n_seg):
        i_next = (i + 1) % n_seg
        bm.faces.new([v_top, r_top[i], r_top[i_next]])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("Rock")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("Rock", me)
    bpy.context.scene.collection.objects.link(obj)

    mat = make_material("RockMat", (0.18, 0.19, 0.21), roughness=0.88, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
