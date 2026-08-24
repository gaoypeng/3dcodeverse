"""WaterTank — removable clear water reservoir at the rear.

Tall rectangular container with vertical fill-level markers and a top-opening hinged black plastic lid.
Material: frosted semi-transparent plastic with black lid.
Plan bbox: center (0.000, 0.141, 0.220) extents (0.240, 0.062, 0.320)
  x in [-0.120, 0.120], y in [0.110, 0.172], z in [0.060, 0.380]
"""
import bpy
import bmesh


def make_material(name, rgb, roughness=0.15, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_water_tank():
    """Water reservoir body + black top lid."""
    # Y range: 0.110 to 0.172 (sy = 0.062, cy = 0.141) -> max Y is 0.172 so total depth is exactly <= 0.400m
    bm = bmesh.new()

    # 1. Main frosted clear tank body (z: 0.060 to 0.365)
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.240, 0.062, 0.305), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.000, 0.141, 0.2125), verts=res["verts"])

    # 2. Black lid on top (z: 0.365 to 0.380)
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.240, 0.062, 0.015), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.000, 0.141, 0.3725), verts=res["verts"])

    # 3. Handle / lip on the lid
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.080, 0.020, 0.005), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.000, 0.141, 0.378), verts=res["verts"])

    me = bpy.data.meshes.new("WaterTank")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("WaterTank", me)
    bpy.context.scene.collection.objects.link(obj)

    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.004
    bev.segments = 3
    bev.limit_method = 'ANGLE'

    mat = make_material("FrostedPlastic", (0.88, 0.92, 0.96), roughness=0.15, metallic=0.05)
    obj.data.materials.append(mat)
    return obj
