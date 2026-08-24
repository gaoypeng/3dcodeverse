"""TorqueCollar — Adjustable clutch selector ring (part module; imported by src/model.py).

Fluted circular dial mounted at the front nose of the main housing with printed/embossed torque numerical step indicators.
Material: Dark gray knurled plastic with white index markings.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -0.065, 0.190) extents (0.058, 0.030, 0.058)
# x in [-0.029, 0.029]
# y in [-0.080, -0.050] (length = 0.030)
# z in [0.161, 0.219]

def make_material(name, rgb, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_torque_collar():
    mat = make_material("TorqueCollarDark", (0.15, 0.15, 0.17), roughness=0.4, metallic=0.2)

    bm = bmesh.new()

    # Cylindrical ring with ribbed/fluted ridges along circumference
    # Center axis along Y: (0, y, 0.190)
    # y ranges from -0.080 (front) to -0.050 (back, attached to MainHousing)
    # Radius = 0.029 (diameter 0.058)
    
    n_teeth = 18
    n_pts = n_teeth * 2
    r_base = 0.0265
    r_ridge = 0.0290

    # Profiles along Y:
    # y = -0.050 (rear base ring)
    # y = -0.054 (start of knurled ridges)
    # y = -0.076 (end of knurled ridges)
    # y = -0.080 (front beveled face)

    y_slices = [
        (-0.050, 0.0270, 0.0270),
        (-0.054, r_base, r_ridge),
        (-0.076, r_base, r_ridge),
        (-0.080, 0.0255, 0.0255),
    ]

    rings = []
    for y, rb, rr in y_slices:
        ring = []
        for i in range(n_pts):
            theta = 2 * math.pi * i / n_pts
            # Alternate between base and ridge
            r = rr if (i % 2 == 1 and rr > rb) else rb
            px = r * math.cos(theta)
            pz = 0.190 + r * math.sin(theta)
            ring.append(bm.verts.new((px, y, pz)))
        rings.append(ring)

    for s in range(len(rings) - 1):
        r1 = rings[s]
        r2 = rings[s + 1]
        for i in range(n_pts):
            i_next = (i + 1) % n_pts
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    # Caps
    # Back cap (facing +Y)
    bm.faces.new([rings[0][i] for i in range(n_pts)])
    # Front cap (facing -Y)
    bm.faces.new([rings[-1][i] for i in reversed(range(n_pts))])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("TorqueCollar")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("TorqueCollar", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0008
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj
