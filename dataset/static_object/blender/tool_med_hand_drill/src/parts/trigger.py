"""Trigger — Variable-speed power trigger switch (part module; imported by src/model.py).

Curved finger-conforming red trigger switch nestled inside the upper front corner crook of the pistol grip.
Material: Bright red smooth plastic.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -0.018, 0.150) extents (0.020, 0.028, 0.035)
# x in [-0.010, 0.010]
# y in [-0.032, -0.004]
# z in [0.1325, 0.1675]

def make_material(name, rgb, roughness=0.3, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_trigger():
    mat = make_material("TriggerRed", (0.85, 0.05, 0.05), roughness=0.25, metallic=0.0)

    bm = bmesh.new()

    # Trigger paddle sits in the crook under the motor housing (z < 0.152) and forward/reverse bar above it (z in [0.158, 0.167]).
    # Main housing at y in [-0.025, -0.010] has bottom at z = 0.190 - 0.036 = 0.154.
    # To avoid interpenetration with MainHousing:
    # Trigger paddle stays in z: [0.133, 0.153].
    # Forward/reverse switch button sits at y = -0.010, z in [0.158, 0.167], but only on the sides of the housing (or in the recess).
    
    slices = [
        # (z, y_front, y_back, half_w)
        (0.133, -0.024, -0.005, 0.008),
        (0.140, -0.031, -0.005, 0.009),
        (0.146, -0.032, -0.005, 0.0095),
        (0.150, -0.027, -0.005, 0.009),
        (0.153, -0.020, -0.005, 0.0085),
    ]

    rings = []
    for z, yf, yb, hw in slices:
        v0 = bm.verts.new((-hw, yb, z))
        v1 = bm.verts.new((hw, yb, z))
        v2 = bm.verts.new((hw, yf, z))
        v3 = bm.verts.new((-hw, yf, z))
        rings.append([v0, v1, v2, v3])

    for r in range(len(rings) - 1):
        r1 = rings[r]
        r2 = rings[r + 1]
        for i in range(4):
            i_next = (i + 1) % 4
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    bm.faces.new([rings[0][i] for i in reversed(range(4))])
    bm.faces.new([rings[-1][i] for i in range(4)])

    # Forward / reverse slider button extending to bbox top z=0.167
    bm_fr = bmesh.new()
    bmesh.ops.create_cube(bm_fr, size=1.0)
    for v in bm_fr.verts:
        v.co.x = v.co.x * 0.020
        v.co.y = -0.012 + v.co.y * 0.008
        v.co.z = 0.1625 + v.co.z * 0.008
    offset = len(bm.verts)
    for v in bm_fr.verts:
        bm.verts.new(v.co)
    bm.verts.ensure_lookup_table()
    for f in bm_fr.faces:
        bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
    bm_fr.free()

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("Trigger")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("Trigger", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)

    return obj
