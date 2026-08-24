"""Foot — Non-slip rubber support feet.

Short cylindrical rubber foot (diameter 0.022 m, height 0.010 m) with slightly tapered edge, standing firmly on the ground plane at z=0.
Material: black ribbed rubber.  Instances: 4 (mirror_x).  Attaches to: BaseChassis.
Plan bbox: center (0.105, -0.065, 0.005) extents (0.022, 0.022, 0.010)
  x in [0.094, 0.116]  y in [-0.076, -0.054]  z in [0.000, 0.010]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

FOOT_CENTER = (0.105, -0.065, 0.005)
FOOT_EXTENTS = (0.022, 0.022, 0.010)
FOOT_INSTANCES = 4

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_foot():
    mat = make_material("RubberFootMat", (0.04, 0.04, 0.04), roughness=0.85, metallic=0.0)
    
    positions = [
        (0.105, -0.065, 0.005),
        (-0.105, -0.065, 0.005),
        (-0.105, 0.065, 0.005),
        (0.105, 0.065, 0.005)
    ]
    
    radius = FOOT_EXTENTS[0] / 2.0  # 0.011
    depth = FOOT_EXTENTS[2]         # 0.010
    
    objs = []
    for i, pos in enumerate(positions):
        bm = bmesh.new()
        # Slightly tapered cylinder
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            segments=24,
            radius1=radius,        # bottom
            radius2=radius * 0.95, # top
            depth=depth
        )
        me = bpy.data.meshes.new(f"Foot_{i}")
        bm.to_mesh(me)
        bm.free()
        
        obj = bpy.data.objects.new(f"Foot_{i}", me)
        obj.location = pos
        bpy.context.scene.collection.objects.link(obj)
        
        bev = obj.modifiers.new("Bevel", "BEVEL")
        bev.width = 0.001
        bev.segments = 2
        bev.limit_method = "ANGLE"
        
        obj.data.materials.append(mat)
        objs.append(obj)
        
    return objs
