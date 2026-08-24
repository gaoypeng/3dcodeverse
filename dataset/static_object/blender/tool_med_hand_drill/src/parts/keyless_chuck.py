"""KeylessChuck — Toolless 3-jaw drill chuck (part module; imported by src/model.py).

Stepped cylindrical metal and ribbed composite sleeve projecting forward from the torque collar with a three-jaw center aperture.
Material: Black oxide steel and knurled nylon collar.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -0.098, 0.190) extents (0.046, 0.046, 0.046)
# x in [-0.023, 0.023]
# y in [-0.121, -0.075] (length = 0.046)
# z in [0.167, 0.213]

def make_material(name, rgb, roughness=0.3, metallic=0.7):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_keyless_chuck():
    mat_steel = make_material("BlackOxideSteel", (0.10, 0.10, 0.11), roughness=0.3, metallic=0.85)

    bm = bmesh.new()

    # The keyless chuck is a stepped cylindrical sleeve with a tapered front cone and 3 jaw teeth.
    # Center axis along Y: (0, y, 0.190)
    # y ranges from -0.075 (rear attached to torque collar) to -0.121 (front tip)
    # Max radius = 0.023 (diameter 0.046)
    
    n_seg = 24
    # Profile along Y:
    # y = -0.075: r = 0.0225 (rear collar rim)
    # y = -0.080: r = 0.0230 (main gripping sleeve start)
    # y = -0.098: r = 0.0230 (main gripping sleeve mid)
    # y = -0.105: r = 0.0220 (front sleeve step)
    # y = -0.115: r = 0.0160 (front tapered cone)
    # y = -0.121: r = 0.0090 (front nose aperture around drill bit)

    y_slices = [
        (-0.075, 0.0225),
        (-0.080, 0.0230),
        (-0.098, 0.0230),
        (-0.105, 0.0220),
        (-0.115, 0.0160),
        (-0.121, 0.0090),
    ]

    rings = []
    for y, r in y_slices:
        ring = []
        for i in range(n_seg):
            theta = 2 * math.pi * i / n_seg
            # Add longitudinal grip ridges on the main sleeve (y between -0.080 and -0.105)
            r_act = r
            if -0.105 <= y <= -0.080:
                # 12 knurled grip ribs
                if (i % 2) == 1:
                    r_act -= 0.001
            px = r_act * math.cos(theta)
            pz = 0.190 + r_act * math.sin(theta)
            ring.append(bm.verts.new((px, y, pz)))
        rings.append(ring)

    for s in range(len(rings) - 1):
        r1 = rings[s]
        r2 = rings[s + 1]
        for i in range(n_seg):
            i_next = (i + 1) % n_seg
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    # Caps
    # Back cap (facing +Y)
    bm.faces.new([rings[0][i] for i in range(n_seg)])
    # Front cap (facing -Y) with central jaw aperture
    bm.faces.new([rings[-1][i] for i in reversed(range(n_seg))])

    # Add 3 steel jaws inside front cone
    for j in range(3):
        ang = j * (2 * math.pi / 3) + math.pi / 6
        bm_jaw = bmesh.new()
        bmesh.ops.create_cone(bm_jaw, cap_ends=True, segments=6, radius1=0.0035, radius2=0.002, depth=0.006)
        # Orient along Y
        for v in bm_jaw.verts:
            # Swap Z to Y
            vx, vy, vz = v.co.x, v.co.z, v.co.y
            v.co.x = 0.0055 * math.cos(ang) + vx
            v.co.y = -0.118 + vy
            v.co.z = 0.190 + 0.0055 * math.sin(ang) + vz
        offset = len(bm.verts)
        for v in bm_jaw.verts:
            bm.verts.new(v.co)
        bm.verts.ensure_lookup_table()
        for f in bm_jaw.faces:
            bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
        bm_jaw.free()

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("KeylessChuck")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("KeylessChuck", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat_steel)

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0006
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj
