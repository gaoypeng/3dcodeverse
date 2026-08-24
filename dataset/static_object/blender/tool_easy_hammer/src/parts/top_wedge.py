"""TopWedge — fixation wedge locking handle tenon into eye.

Steel wedge driven into the top end grain of the wooden handle flush with the top face of the hammer eye.
Material: dark blackened steel.  Instances: 1.  Attaches to: WoodenHandle.
Plan bbox: center (0.000, 0.000, 0.328) extents (0.016, 0.008, 0.004)
  x in [-0.008, 0.008]  y in [-0.004, 0.004]  z in [0.326, 0.330]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

TOP_WEDGE_CENTER = (0.000, 0.000, 0.328)
TOP_WEDGE_EXTENTS = (0.016, 0.008, 0.004)

def make_material(name, rgb, roughness=0.6, metallic=0.9):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_top_wedge():
    """Build wedge-shaped steel fastener driven into wooden tenon at z=[0.326, 0.330]."""
    bm = bmesh.new()
    
    # WoodenHandle tenon reaches z = 0.327 (or 0.328).
    # Wedge sits at z in [0.326, 0.330].
    # Overlaps WoodenHandle top by 1-2 mm, so it contacts WoodenHandle.
    
    v0 = bm.verts.new((-0.008, -0.004, 0.330))
    v1 = bm.verts.new(( 0.008, -0.004, 0.330))
    v2 = bm.verts.new(( 0.008,  0.004, 0.330))
    v3 = bm.verts.new((-0.008,  0.004, 0.330))
    
    v4 = bm.verts.new((-0.0075, -0.002, 0.326))
    v5 = bm.verts.new(( 0.0075, -0.002, 0.326))
    v6 = bm.verts.new(( 0.0075,  0.002, 0.326))
    v7 = bm.verts.new((-0.0075,  0.002, 0.326))
    
    # Faces
    bm.faces.new([v3, v2, v1, v0]) # Top
    bm.faces.new([v4, v5, v6, v7]) # Bottom
    bm.faces.new([v0, v1, v5, v4]) # -Y
    bm.faces.new([v1, v2, v6, v5]) # +X
    bm.faces.new([v2, v3, v7, v6]) # +Y
    bm.faces.new([v3, v0, v4, v7]) # -X
    
    bm.normal_update()
    me = bpy.data.meshes.new("TopWedge")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("TopWedge", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("BlackenedSteelWedge", (0.15, 0.15, 0.16), roughness=0.6, metallic=0.9)
    obj.data.materials.append(mat)
    
    return obj
