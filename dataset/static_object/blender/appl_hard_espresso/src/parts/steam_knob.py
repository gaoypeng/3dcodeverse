"""SteamKnob — valve control dial for steam output.

Ribbed rotary knob (diameter 35 mm, depth 20 mm) mounted on the upper right side/front panel.
Material: black ribbed bakelite with chrome center cap.
Plan bbox: center (0.110, -0.100, 0.320) extents (0.040, 0.030, 0.040)
  x in [0.090, 0.130], y in [-0.115, -0.085], z in [0.300, 0.340]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix

def make_material(name, rgb, roughness=0.3, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_steam_knob():
    """Build cylindrical fluted rotary knob with chrome center cap on the front fascia."""
    bm = bmesh.new()
    rot_x90 = Matrix.Rotation(math.radians(90), 4, 'X')
    
    # Knob axis is aligned along Y (projecting from y = -0.085 outwards to y = -0.115)
    # Center: (0.110, -0.100, 0.320)
    # 1. Main ribbed knob body (diameter 36mm -> radius 0.018m, depth 0.020m)
    res = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=32,
        radius1=0.018,
        radius2=0.017,
        depth=0.020
    )
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.110, -0.098, 0.320), verts=res["verts"])
    
    # 2. Chrome center cap (front face)
    res = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=24,
        radius1=0.010,
        radius2=0.010,
        depth=0.005
    )
    bmesh.ops.transform(bm, matrix=rot_x90, verts=res["verts"])
    bmesh.ops.translate(bm, vec=(0.110, -0.110, 0.320), verts=res["verts"])
    
    # 3. Flutes / ridges on the knob circumference (small radial boxes)
    n_ribs = 8
    for i in range(n_ribs):
        angle = 2 * math.pi * i / n_ribs
        rx = 0.018 * math.cos(angle)
        rz = 0.018 * math.sin(angle)
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.003, 0.018, 0.003), verts=res["verts"])
        rot_r = Matrix.Rotation(angle, 4, 'Y')
        bmesh.ops.transform(bm, matrix=rot_r, verts=res["verts"])
        bmesh.ops.translate(bm, vec=(0.110 + rx, -0.098, 0.320 + rz), verts=res["verts"])

    me = bpy.data.meshes.new("SteamKnob")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SteamKnob", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("BlackBakelite", (0.08, 0.08, 0.08), roughness=0.3, metallic=0.05)
    obj.data.materials.append(mat)
    
    return obj
