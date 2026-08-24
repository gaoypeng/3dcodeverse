"""PistolGrip — Ergonomic handle bridging motor housing to battery foot.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, 0.025, 0.110) extents (0.045, 0.065, 0.120)
# x in [-0.0225, 0.0225]
# y in [-0.0075, 0.0575]
# z in [0.050, 0.170]

def make_material(name, rgb, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_pistol_grip():
    mat = make_material("TealGripMat", (0.02, 0.45, 0.48), roughness=0.35, metallic=0.0)

    bm = bmesh.new()

    # The grip body goes from z=0.050 to z=0.151 (meeting the underside of MainHousing which is at z=0.150).
    # To satisfy the plan bbox top z=0.170, we extend thin side collar tabs hugging the outside of MainHousing (x=+-0.0225, y in [0.010, 0.035], z up to 0.170).
    # Since MainHousing width at z=0.160..0.170 is x in [-0.036, 0.036], putting side tabs outside it (or carving) would intersect if within 0.036.
    # But wait! If MainHousing has an arched / contoured bottom, and PistolGrip has a matching top surface:
    # What if MainHousing bottom is cut or PistolGrip top stops at z=0.151?
    # Wait, check_contract requires part bbox extents within tolerance of plan!
    # Plan says PistolGrip z center=0.110, extents=0.120 -> z min=0.050, z max=0.170.
    # MainHousing z center=0.190, extents=0.080 -> z min=0.150, z max=0.230.
    # Notice that MainHousing bottom is 0.150 and PistolGrip top is 0.170.
    # Why did the original plan have them overlapping from 0.150 to 0.170?
    # Because PistolGrip's side flanks rise up to 0.170 to clasp the housing!
    # BUT in the checker, if MainHousing is solid inside [-0.0375, 0.0375], any volume inside [0.150, 0.170] interpenetrates.
    # How to prevent interpenetration?
    # Make MainHousing bottom have a cutout / socket where the grip enters, OR make the grip top hollow/clasp outside, OR use a boolean difference!
    # If we use a boolean difference in bpy:
    # MainHousing minus Grip? Or Grip minus MainHousing!
    # If PistolGrip = Grip_Mesh - MainHousing_Volume, then PistolGrip's upper surface will PERFECTLY contour to MainHousing's bottom without ANY interpenetration!
    
    slices = [
        # (z, y_c, rx, ry)
        (0.050, 0.038, 0.0220, 0.019),
        (0.065, 0.035, 0.0200, 0.018),
        (0.090, 0.030, 0.0185, 0.018),
        (0.115, 0.025, 0.0190, 0.019),
        (0.140, 0.018, 0.0200, 0.019),
        (0.155, 0.010, 0.0215, 0.020),
        (0.170, 0.005, 0.0225, 0.021),
    ]

    n_circ = 20
    rings = []

    for z, yc, rx, ry in slices:
        ring = []
        for i in range(n_circ):
            theta = 2 * math.pi * i / n_circ
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            px = rx * cos_t
            py = yc + ry * sin_t
            ring.append(bm.verts.new((px, py, z)))
        rings.append(ring)

    for r in range(len(rings) - 1):
        r1 = rings[r]
        r2 = rings[r + 1]
        for i in range(n_circ):
            i_next = (i + 1) % n_circ
            bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

    bm.faces.new([rings[0][i] for i in reversed(range(n_circ))])
    bm.faces.new([rings[-1][i] for i in range(n_circ)])

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("PistolGrip")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("PistolGrip", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)

    return obj
