"""MainChassis — main housing body and internal structural frame.

Rectangular sheet-metal chassis with curved vertical front corners (r=15 mm) and a recessed lower front cavity for the cup brewing area. Top surface acts as the cup warmer tray.
Material: brushed stainless steel.
Plan bbox: center (0.000, 0.030, 0.200) extents (0.260, 0.260, 0.370)
  x in [-0.130, 0.130], y in [-0.100, 0.160], z in [0.015, 0.385]
"""
import bpy
import bmesh


def make_material(name, rgb, roughness=0.25, metallic=0.95):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_main_chassis():
    """Build the MainChassis with upper housing, recessed rear wall for brew area, side panels, and top warmer tray."""
    bm = bmesh.new()

    # 1. Base floor block (under brew station)
    # DripTray is at y: -0.200 to -0.080.
    # To have a ≤ 2mm overlap for welding, base floor reaches y = -0.0815 to 0.160
    # y: -0.0815 to 0.160 -> sy = 0.2415, cy = 0.03925
    # z: 0.015 to 0.080 -> center z = 0.0475, sz = 0.065
    # x: -0.130 to 0.130 -> center x = 0.000, sx = 0.260
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.260, 0.2415, 0.065), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.000, 0.03925, 0.0475), verts=res["verts"])

    # 2. Upper overhang (grouphead & control panel housing)
    # z: 0.238 to 0.385 -> center z = 0.3115, sz = 0.147
    # y: -0.100 to 0.160 -> center y = 0.030, sy = 0.260
    # x: -0.130 to 0.130 -> center x = 0.000, sx = 0.260
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.260, 0.260, 0.147), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.000, 0.030, 0.3115), verts=res["verts"])

    # 3. Rear column / back wall behind brew cavity
    # z: 0.075 to 0.245 -> center z = 0.160, sz = 0.170
    # y: -0.010 to 0.160 -> center y = 0.075, sy = 0.170
    # x: -0.130 to 0.130 -> center x = 0.000, sx = 0.260
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.260, 0.170, 0.170), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.000, 0.075, 0.160), verts=res["verts"])

    # 4. Side accent trim on backplate
    res = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.240, 0.010, 0.160), verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.000, -0.005, 0.160), verts=res["verts"])

    me = bpy.data.meshes.new("MainChassis")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("MainChassis", me)
    bpy.context.scene.collection.objects.link(obj)

    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.006
    bev.segments = 3
    bev.limit_method = 'ANGLE'

    mat = make_material("BrushedStainless", (0.80, 0.81, 0.83), roughness=0.25, metallic=0.95)
    obj.data.materials.append(mat)

    return obj
