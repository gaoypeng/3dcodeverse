"""BrowningDial — Rotary timer/browning control knob.

Fluted round dial knob (diameter 0.034 m, depth 0.016 m) with numbered dial bezel ring mounted prominently on the front (-Y) lower face.
Material: gloss black plastic with chrome center cap and white indicator notches.
Plan bbox: center (0.000, -0.088, 0.055) extents (0.034, 0.016, 0.034)
  x in [-0.017, 0.017]  y in [-0.096, -0.080]  z in [0.038, 0.072]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

BROWNING_DIAL_CENTER = (0.000, -0.088, 0.055)
BROWNING_DIAL_EXTENTS = (0.034, 0.016, 0.034)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_browning_dial():
    # Cylinder rotated so its axis is aligned with Y (front face)
    radius = BROWNING_DIAL_EXTENTS[0] / 2.0  # 0.017
    depth = BROWNING_DIAL_EXTENTS[1]         # 0.016
    
    bm = bmesh.new()
    # Cylinder along Z first
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=36,
        radius1=radius,
        radius2=radius * 0.92, # slightly tapered knob
        depth=depth
    )
    # Rotate 90 deg around X so axis points along Y
    bmesh.ops.rotate(
        bm,
        cent=(0, 0, 0),
        matrix=Matrix.Rotation(math.radians(90), 4, 'X'),
        verts=bm.verts
    )
    
    me = bpy.data.meshes.new("BrowningDial")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("BrowningDial", me)
    obj.location = BROWNING_DIAL_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0015
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    mat = make_material("DialBakelite", (0.1, 0.1, 0.1), roughness=0.25, metallic=0.1)
    obj.data.materials.append(mat)
    return obj
