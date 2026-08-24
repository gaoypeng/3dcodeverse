"""PressureGauge — barometric pressure display dial.

Circular analogue gauge (diameter 48 mm) with chrome bezel, white dial face, tick marks, espresso zone, needle, and glass pin.
Material: chrome bezel, printed white dial face, black ticks, red needle.
Plan bbox: center (-0.050, -0.101, 0.320) extents (0.050, 0.015, 0.050)
  x in [-0.075, -0.025], y in [-0.109, -0.093], z in [0.295, 0.345]
"""
import math
import bpy
import bmesh
from mathutils import Matrix


def make_material(name, rgb, roughness=0.1, metallic=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_pressure_gauge():
    """Build circular pressure gauge dial with chrome bezel, white face, needle, and tick marks."""
    bm = bmesh.new()
    rot_x90 = Matrix.Rotation(math.radians(90), 4, 'X')

    mat_chrome = make_material("GaugeChrome", (0.95, 0.95, 0.95), roughness=0.1, metallic=1.0)
    mat_white = make_material("GaugeWhite", (0.95, 0.95, 0.93), roughness=0.3, metallic=0.0)
    mat_dark = make_material("GaugeDark", (0.08, 0.08, 0.08), roughness=0.5, metallic=0.1)
    mat_red = make_material("GaugeRed", (0.85, 0.12, 0.12), roughness=0.4, metallic=0.0)

    # 1. Outer chrome bezel ring (radius 0.024, depth 0.012) - mat 0 (chrome)
    res = bmesh.ops.create_cone(
        bm, cap_ends=True, segments=36, radius1=0.024, radius2=0.024, depth=0.012
    )
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(-0.050, -0.101, 0.320), verts=res["verts"])
    for f in bm.faces:
        f.material_index = 0

    # 2. White dial face disk - mat 1 (white)
    f_start = len(bm.faces)
    res = bmesh.ops.create_cone(
        bm, cap_ends=True, segments=32, radius1=0.021, radius2=0.021, depth=0.002
    )
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(-0.050, -0.106, 0.320), verts=res["verts"])
    for f in bm.faces[f_start:]:
        f.material_index = 1

    # 3. Inner colored espresso zone arc
    f_start = len(bm.faces)
    for a_idx in range(6):
        a_mid = math.radians(160 - a_idx * 16)
        ax = -0.050 + 0.015 * math.cos(a_mid)
        az = 0.320 + 0.015 * math.sin(a_mid)
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.003, 0.001, 0.002), verts=res["verts"])
        rot_a = Matrix.Rotation(-a_mid + math.pi / 2, 4, 'Y')
        bmesh.ops.transform(bm, matrix=rot_a, verts=res["verts"])
        bmesh.ops.translate(bm, vec=(ax, -0.1065, az), verts=res["verts"])
    for f in bm.faces[f_start:]:
        f.material_index = 3

    # 4. Black needle pointer - mat 2 (dark)
    f_start = len(bm.faces)
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.0015, 0.002, 0.015), verts=res["verts"])
    rot_needle = Matrix.Rotation(math.radians(-35), 4, 'Y')
    bmesh.ops.transform(bm, matrix=rot_needle, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(-0.050 + 0.004, -0.107, 0.320 + 0.004), verts=res["verts"])

    # Center needle pin / hub - mat 2 (dark)
    res = bmesh.ops.create_cone(
        bm, cap_ends=True, segments=16, radius1=0.0035, radius2=0.0035, depth=0.003
    )
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(-0.050, -0.1072, 0.320), verts=res["verts"])

    # Dial tick marks around perimeter (10 black tick marks) - mat 2 (dark)
    for t in range(10):
        t_ang = math.radians(225 - t * 30)
        tx = -0.050 + 0.0175 * math.cos(t_ang)
        tz = 0.320 + 0.0175 * math.sin(t_ang)
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.0012, 0.001, 0.0035), verts=res["verts"])
        rot_tick = Matrix.Rotation(-t_ang + math.pi / 2, 4, 'Y')
        bmesh.ops.transform(bm, matrix=rot_tick, verts=res["verts"])
        bmesh.ops.translate(bm, vec=(tx, -0.1065, tz), verts=res["verts"])

    for f in bm.faces[f_start:]:
        f.material_index = 2

    me = bpy.data.meshes.new("PressureGauge")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("PressureGauge", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_chrome)
    obj.data.materials.append(mat_white)
    obj.data.materials.append(mat_dark)
    obj.data.materials.append(mat_red)
    return obj
