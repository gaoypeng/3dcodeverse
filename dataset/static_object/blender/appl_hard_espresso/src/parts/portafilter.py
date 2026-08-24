"""Portafilter — coffee filter holder with dual pouring spouts and handle.

58 mm chrome basket head locked into group head, featuring twin downward spouts and an ergonomic black handle extending forward-left.
Material: polished chrome with matte black ergonomic handle.
Plan bbox: center (-0.040, -0.160, 0.190) extents (0.120, 0.200, 0.060)
  x in [-0.100, 0.020], y in [-0.260, -0.060], z in [0.160, 0.220]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix


def make_material(name, rgb, roughness=0.25, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_portafilter():
    """Build portafilter basket head, dual spouts, neck, and angled ergonomic handle."""
    bm = bmesh.new()
    mat_chrome = make_material("PortafilterChrome", (0.95, 0.95, 0.95), roughness=0.1, metallic=1.0)
    mat_handle = make_material("PortafilterBlackHandle", (0.1, 0.1, 0.1), roughness=0.4, metallic=0.0)

    # 1. Basket head (58mm diameter -> radius 0.034 with outer rim, center at (0.000, -0.090, 0.190))
    res = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=32,
        radius1=0.033,
        radius2=0.035,
        depth=0.024
    )
    bmesh.ops.translate(bm, vec=(0.000, -0.090, 0.190), verts=res["verts"])

    # Locking collar rim at top of basket
    res_rim = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=32,
        radius1=0.037,
        radius2=0.037,
        depth=0.004
    )
    bmesh.ops.translate(bm, vec=(0.000, -0.090, 0.200), verts=res_rim["verts"])

    # 2. Dual pouring spouts under basket head (z: 0.160 to 0.178)
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.004, radius2=0.007, depth=0.020)
    rot_y = Matrix.Rotation(math.radians(-15), 4, 'Y')
    bmesh.ops.transform(bm, matrix=rot_y, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(-0.012, -0.090, 0.170), verts=res["verts"])

    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.004, radius2=0.007, depth=0.020)
    rot_y_pos = Matrix.Rotation(math.radians(15), 4, 'Y')
    bmesh.ops.transform(bm, matrix=rot_y_pos, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.012, -0.090, 0.170), verts=res["verts"])

    # 3. Handle geometry:
    # Handle starts at basket collar (0.0, -0.090, 0.190) and extends forward-left towards (-0.080, -0.228, 0.190)
    # Total y span of handle is up to -0.228 so total machine depth is <= 0.400 m
    p_start = Vector((0.0, -0.090, 0.190))
    p_end = Vector((-0.080, -0.218, 0.190))
    v_handle = p_end - p_start
    len_handle = v_handle.length
    q_handle = Vector((0, 0, 1)).rotation_difference(v_handle)

    # Chrome neck
    p_neck_mid = p_start + v_handle * (0.0175 / len_handle)
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=0.007, radius2=0.007, depth=0.035)
    bmesh.ops.transform(bm, matrix=q_handle.to_matrix().to_4x4(), verts=res["verts"])
    bmesh.ops.translate(bm, vec=p_neck_mid, verts=res["verts"])

    for f in bm.faces:
        f.material_index = 0

    # Ergonomic grip
    f_grip_start = len(bm.faces)
    grip_len = 0.105
    p_grip_mid = p_start + v_handle * ((0.030 + 0.135) / 2.0 / len_handle)
    res = bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.014, radius2=0.011, depth=grip_len)
    bmesh.ops.transform(bm, matrix=q_handle.to_matrix().to_4x4(), verts=res["verts"])
    bmesh.ops.translate(bm, vec=p_grip_mid, verts=res["verts"])

    # End cap
    p_cap = p_start + v_handle * (0.135 / len_handle)
    res = bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=12, radius=0.012)
    bmesh.ops.translate(bm, vec=p_cap, verts=res["verts"])

    for f in bm.faces[f_grip_start:]:
        f.material_index = 1

    me = bpy.data.meshes.new("Portafilter")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("Portafilter", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_chrome)
    obj.data.materials.append(mat_handle)
    return obj
