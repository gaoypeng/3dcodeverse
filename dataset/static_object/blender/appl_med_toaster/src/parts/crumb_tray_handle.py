"""CrumbTrayHandle — Pull-out crumb tray lip/handle.

Slim horizontal pull tab handle located along the bottom edge of the side/rear base for the sliding crumb collection tray.
Material: polished chrome handle tab.  Instances: 1.  Attaches to: BaseChassis.
Plan bbox: center (-0.138, 0.000, 0.018) extents (0.012, 0.065, 0.010)
  x in [-0.144, -0.132]  y in [-0.033, 0.033]  z in [0.013, 0.023]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

CRUMB_TRAY_HANDLE_CENTER = (-0.138, 0.000, 0.018)
CRUMB_TRAY_HANDLE_EXTENTS = (0.012, 0.065, 0.010)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_crumb_tray_handle():
    mat = make_material("ChromeCrumbHandle", (0.92, 0.92, 0.94), roughness=0.15, metallic=1.0)
    
    bm = bmesh.new()
    sx, sy, sz = CRUMB_TRAY_HANDLE_EXTENTS
    # Make a beveled tab handle
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    
    me = bpy.data.meshes.new("CrumbTrayHandle")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("CrumbTrayHandle", me)
    obj.location = CRUMB_TRAY_HANDLE_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 3
    bev.limit_method = "ANGLE"
    
    obj.data.materials.append(mat)
    return obj
