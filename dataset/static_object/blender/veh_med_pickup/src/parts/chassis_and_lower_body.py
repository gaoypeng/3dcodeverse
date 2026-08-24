"""ChassisAndLowerBody — main chassis frame, front hood, and integrated flared wheel arches.
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

# Plan:
# center (0.000, -0.050, 0.650) extents (1.880, 4.650, 0.720)
# x in [-0.940, 0.940], y in [-2.375, 2.275], z in [0.290, 1.010]
# Wheels at y = -1.45 and y = 1.45, x = +-0.86, z = 0.38, r = 0.38 (inner edge x = +-0.730, outer edge x = +-0.990)
# Front bumper at y = -2.34 (touches at -2.26)
# Rear bumper at y = 2.34 (touches at 2.26)
# Bed sits on top at z in [0.67, 1.25], Cab sits on top at z in [0.95, 1.73]

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_chassis_and_lower_body():
    body_mat = make_material("TruckPaint_Green", (0.08, 0.22, 0.12), roughness=0.25, metallic=0.1)
    under_mat = make_material("TruckUnderbody_Black", (0.05, 0.05, 0.05), roughness=0.8, metallic=0.2)

    bm = bmesh.new()

    # 1. Main frame rails / floor (central frame under body)
    # y from -2.26 to +2.243 (length 4.503, center -0.0085), x in [-0.70, 0.70], z in [0.30, 0.56]
    # Frame ends at y = 2.243 to touch rear bumper extending to 2.245 with 2mm overlap
    frame = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.40, 4.503, 0.26), verts=frame["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -0.0085, 0.43), verts=frame["verts"])

    # 2. Front Hood & Engine Bay Upper Section
    # y from -2.26 to -1.00 (length 1.26), center y = -1.63
    # Front wheel is at y in [-1.83, -1.07]. Front wheel top is at z = 0.76, inner face |x|=0.73
    hood_upper = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.74, 1.26, 0.24), verts=hood_upper["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -1.63, 0.89), verts=hood_upper["verts"])

    # Lower hood nose (in front of wheels, y in [-2.26, -1.84]) full width
    hood_nose = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.74, 0.42, 0.22), verts=hood_nose["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -2.05, 0.66), verts=hood_nose["verts"])

    # Lower hood between wheels (y in [-1.84, -1.00]) narrower width x in [-0.70, 0.70]
    hood_engine = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.40, 0.84, 0.22), verts=hood_engine["verts"])
    bmesh.ops.translate(bm, vec=(0.0, -1.42, 0.66), verts=hood_engine["verts"])

    # 3. Flared Wheel Arches above wheels (z in [0.77, 0.95], well above wheel top at z=0.76)
    # Front wheel arches at y = -1.45, x = +-0.88, z = 0.86
    for ax, ay, az in [(0.88, -1.45, 0.86), (-0.88, -1.45, 0.86)]:
        arch = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.12, 0.88, 0.18), verts=arch["verts"])
        bmesh.ops.translate(bm, vec=(ax, ay, az), verts=arch["verts"])

    # Rear wheel arches outer trim: thin strip attached to chassis or flush against bed outer wall (|x|=0.92)
    # Bed side wall is at x in [0.84, 0.92], z in [0.67, 1.25].
    # Flared arch on rear at x = +-0.93 (thickness 0.02, x in [0.92, 0.94]), z in [0.70, 0.86], y = 1.45
    for ax, ay, az in [(0.93, 1.45, 0.78), (-0.93, 1.45, 0.78)]:
        arch = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.02, 0.88, 0.16), verts=arch["verts"])
        bmesh.ops.translate(bm, vec=(ax, ay, az), verts=arch["verts"])

    # 4. Axle rods connecting frame (|x|=0.70) to wheel inner face (|x|=0.73)
    # Overlap wheel inner face by exactly 2 mm (depth 0.034 from 0.70 to 0.732, center x = +-0.716)
    for wx, wy, wz in [(0.716, -1.45, 0.38), (-0.716, -1.45, 0.38), (0.716, 1.45, 0.38), (-0.716, 1.45, 0.38)]:
        axle = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.04, radius2=0.04, depth=0.034)
        rot_ax = Matrix.Rotation(math.pi / 2, 4, 'Y')
        for v in axle["verts"]:
            v.co = rot_ax @ v.co + Vector((wx, wy, wz))

    # 5. Rocker panels / Cab lower side body under cab (y in [-1.00, 0.40])
    # x in [-0.88, 0.88], z in [0.55, 0.952] (reaches 2 mm into Cab bottom at z=0.950)
    for side_x in [-0.82, 0.82]:
        sill = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.12, 1.40, 0.40), verts=sill["verts"])
        bmesh.ops.translate(bm, vec=(side_x, -0.30, 0.75), verts=sill["verts"])

    # 6. Bed support pads under bed floor (bed floor is at z in [0.670, 0.730])
    # Bed support top is at z = 0.672 (overlaps bed floor by 2 mm)
    # y in [0.40, 2.24], x in [-0.55, 0.55] (well inside bed inner tub at x=0.56..0.84)
    bed_support = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.10, 1.80, 0.112), verts=bed_support["verts"])
    bmesh.ops.translate(bm, vec=(0.0, 1.32, 0.616), verts=bed_support["verts"])

    me = bpy.data.meshes.new("ChassisAndLowerBody")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("ChassisAndLowerBody", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(body_mat)
    obj.data.materials.append(under_mat)

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.012
    bev.segments = 2
    bev.limit_method = "ANGLE"

    return obj
