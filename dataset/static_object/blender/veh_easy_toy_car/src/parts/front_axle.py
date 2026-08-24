import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

FRONT_AXLE_CENTER = (0.000, -0.065, 0.035)
FRONT_AXLE_EXTENTS = (0.114, 0.008, 0.008)

def make_material(name, rgb, roughness=0.35, metallic=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_front_axle():
    """FrontAxle — transverse front wheel axle
    Solid horizontal cylinder rod (diameter 8 mm, length 0.114 m) passing through the lower front chassis.
    Material: semi-gloss steel rod
    """
    mat = make_material("FrontAxleMat", (0.75, 0.75, 0.78), roughness=0.35, metallic=1.0)
    
    # Cylinder of length 0.114, radius 0.004 m
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.004, radius2=0.004, depth=0.114)
    # Rotate to align with X axis
    bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'), verts=bm.verts)
    
    me = bpy.data.meshes.new("FrontAxle")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("FrontAxle", me)
    obj.location = FRONT_AXLE_CENTER
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    
    # Soft end bevel
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0006
    bev.segments = 2
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
