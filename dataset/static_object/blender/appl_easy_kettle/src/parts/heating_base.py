"""HeatingBase — circular power base station resting on the counter (part module; imported by src/model.py).

Cylindrical power base disc (diameter 0.160 m, height 0.022 m) with beveled top edge (r=3 mm) and a slightly raised central concentric connector ring.
Material: matte black heat-resistant plastic.  Instances: 1.

Exports `build_heating_base() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh

random.seed(0)

HEATING_BASE_CENTER = (0.000, 0.000, 0.011)
HEATING_BASE_EXTENTS = (0.160, 0.160, 0.022)
R_BASE = 0.080  # 0.160 / 2
H_BASE = 0.022


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_heating_base() -> bpy.types.Object:
    bm = bmesh.new()

    # Main cylindrical disc: base from z=0 to z=0.020
    h_main = 0.020
    res_main = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=48,
        radius1=R_BASE,
        radius2=R_BASE * 0.96,
        depth=h_main
    )
    bmesh.ops.translate(bm, vec=(0, 0, h_main / 2), verts=res_main['verts'])

    # Raised central concentric connector ring
    h_ring = 0.004
    res_ring = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=32,
        radius1=0.028,
        radius2=0.025,
        depth=h_ring
    )
    bmesh.ops.translate(bm, vec=(0, 0, h_main + h_ring / 2 - 0.002), verts=res_ring['verts'])

    me = bpy.data.meshes.new("HeatingBase")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("HeatingBase", me)
    bpy.context.scene.collection.objects.link(obj)

    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 3
    bev.limit_method = "ANGLE"

    mat = make_material("MatteBlackPlastic", (0.05, 0.05, 0.05), roughness=0.5, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
