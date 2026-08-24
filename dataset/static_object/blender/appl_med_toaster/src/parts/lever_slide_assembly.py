"""LeverSlideAssembly — Side carriage plunge lever and slider track.

Vertical slider slot track on the right (+X) side wall housing a horizontal steel arm capped with an ergonomic oval bakelite handle knob.
Material: polished chrome slider arm with gloss black plastic grip knob.
Plan bbox: center (0.148, 0.000, 0.130) extents (0.036, 0.030, 0.080)
  x in [0.130, 0.166]  y in [-0.015, 0.015]  z in [0.090, 0.170]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

LEVER_SLIDE_ASSEMBLY_CENTER = (0.148, 0.000, 0.130)
LEVER_SLIDE_ASSEMBLY_EXTENTS = (0.036, 0.030, 0.080)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_lever_slide_assembly():
    bm = bmesh.new()
    
    # 1. Slider plate / track on toaster wall: thin plate at x_min
    # Plan bounds relative to LEVER_SLIDE_ASSEMBLY_CENTER (0.148, 0.0, 0.130):
    # dx in [-0.018, 0.018], dy in [-0.015, 0.015], dz in [-0.040, 0.040]
    
    # Vertical track plate: x in [-0.018, -0.012], y in [-0.012, 0.012], z in [-0.040, 0.040]
    ret_plate = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.006, 0.024, 0.080), verts=ret_plate["verts"])
    bmesh.ops.translate(bm, vec=(-0.015, 0, 0), verts=ret_plate["verts"])
    
    # 2. Lever stem / arm (chrome shaft): x in [-0.012, 0.008], y in [-0.004, 0.004], z in [0.010, 0.020]
    ret_stem = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.020, 0.008, 0.010), verts=ret_stem["verts"])
    bmesh.ops.translate(bm, vec=(-0.002, 0, 0.015), verts=ret_stem["verts"])
    
    # 3. Bakelite knob handle: x in [0.002, 0.018], y in [-0.015, 0.015], z in [0.005, 0.025]
    # Rounded handle block
    ret_knob = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.016, 0.030, 0.020), verts=ret_knob["verts"])
    bmesh.ops.translate(bm, vec=(0.010, 0, 0.015), verts=ret_knob["verts"])
    
    me = bpy.data.meshes.new("LeverSlideAssembly")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("LeverSlideAssembly", me)
    obj.location = LEVER_SLIDE_ASSEMBLY_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    mat = make_material("LeverKnobMat", (0.1, 0.1, 0.1), roughness=0.3, metallic=0.2)
    obj.data.materials.append(mat)
    return obj
