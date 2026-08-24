"""MainHousing — Upper motor enclosure and internal gear casing.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, 0.020, 0.190) extents (0.075, 0.150, 0.080)
# y range: [-0.055, 0.095]
# x range: [-0.0375, 0.0375]
# z range: [0.150, 0.230]

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_main_housing():
    mat = make_material("TealComposite", (0.02, 0.45, 0.48), roughness=0.3, metallic=0.05)

    bm = bmesh.new()

    # Smooth profile cross-sections along Y
    # (y, rx, rz_top, rz_bot, cz, power)
    y_slices = [
        (-0.055, 0.0270, 0.0270, 0.0270, 0.190, 0.95),
        (-0.045, 0.0315, 0.0320, 0.0320, 0.190, 0.90),
        (-0.035, 0.0345, 0.0355, 0.0340, 0.190, 0.88),
        (-0.020, 0.0365, 0.0380, 0.0260, 0.190, 0.85),
        (0.000,  0.0375, 0.0398, 0.0220, 0.190, 0.85),
        (0.020,  0.0375, 0.0400, 0.0220, 0.190, 0.85),
        (0.040,  0.0375, 0.0400, 0.0240, 0.190, 0.85),
        (0.055,  0.0372, 0.0390, 0.0320, 0.190, 0.85),
        (0.070,  0.0360, 0.0375, 0.0365, 0.190, 0.88),
        (0.082,  0.0340, 0.0355, 0.0355, 0.190, 0.90),
        (0.090,  0.0310, 0.0330, 0.0330, 0.190, 0.92),
        (0.095,  0.0260, 0.0280, 0.0280, 0.190, 0.95),
    ]

    n_circ = 32
    rings_verts = []

    for y, rx, rz_t, rz_b, cz, pwr in y_slices:
        ring = []
        for i in range(n_circ):
            theta = 2 * math.pi * i / n_circ
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            px = rx * math.copysign(abs(cos_t) ** pwr, cos_t)
            rz = rz_t if sin_t >= 0 else rz_b
            pz = cz + rz * math.copysign(abs(sin_t) ** pwr, sin_t)
            v = bm.verts.new((px, y, pz))
            ring.append(v)
        rings_verts.append(ring)

    for r in range(len(rings_verts) - 1):
        r1 = rings_verts[r]
        r2 = rings_verts[r + 1]
        for i in range(n_circ):
            i_next = (i + 1) % n_circ
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    # Caps with fan or grid
    bm.faces.new([rings_verts[0][i] for i in reversed(range(n_circ))])
    bm.faces.new([rings_verts[-1][i] for i in range(n_circ)])

    # Smooth shading for faces
    for f in bm.faces:
        f.smooth = True

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("MainHousing")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("MainHousing", me)
    bpy.context.scene.collection.objects.link(obj)

    # Add subtle bevel modifier for ergonomic edges
    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = 'ANGLE'
    bev.angle_limit = math.radians(40)

    obj.data.materials.append(mat)

    return obj
