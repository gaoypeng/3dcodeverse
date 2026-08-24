"""StrikingFace — flat front impact face for nail driving.

Circular steel face (diameter 30 mm to 32 mm) positioned at the -Y front end, featuring a flat planar contact surface and a 2.5 mm perimeter bevel to resist chipping.
Material: hardened high-polish mirror steel.  Instances: 1.  Attaches to: StrikingNeck.
Plan bbox: center (0.000, -0.065, 0.305) extents (0.032, 0.012, 0.032)
  x in [-0.016, 0.016]  y in [-0.071, -0.059]  z in [0.289, 0.321]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

STRIKING_FACE_CENTER = (0.000, -0.065, 0.305)
STRIKING_FACE_EXTENTS = (0.032, 0.012, 0.032)

def make_material(name, rgb, roughness=0.15, metallic=0.98):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_striking_face():
    """Circular steel head face with a flat planar impact surface and chamfered rim."""
    bm = bmesh.new()
    
    # Range along Y: -0.071 to -0.059
    # -0.059: weld to striking neck, radius 0.0150
    # -0.063: max diameter rim, radius 0.0160
    # -0.0685: start of chamfer, radius 0.0160
    # -0.0710: flat striking face, radius 0.0135 (chamfered bevel edge)
    
    n_segments = 32
    y_profile = [
        (-0.0590, 0.0150),
        (-0.0630, 0.0160),
        (-0.0685, 0.0160),
        (-0.0710, 0.0135),
    ]
    
    rings = []
    for y, r in y_profile:
        ring_verts = []
        for i in range(n_segments):
            angle = 2.0 * math.pi * i / n_segments
            x = r * math.cos(angle)
            z = 0.305 + r * math.sin(angle)
            v = bm.verts.new((x, y, z))
            ring_verts.append(v)
        rings.append(ring_verts)
        
    bm.faces.new(reversed(rings[0]))
    for r in range(len(rings) - 1):
        r1 = rings[r]
        r2 = rings[r + 1]
        for i in range(n_segments):
            next_i = (i + 1) % n_segments
            bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            
    # Flat planar front impact face at y = -0.071
    bm.faces.new(rings[-1])
    
    bm.normal_update()
    me = bpy.data.meshes.new("StrikingFace")
    bm.to_mesh(me)
    bm.free()
    
    for poly in me.polygons:
        poly.use_smooth = True
        
    obj = bpy.data.objects.new("StrikingFace", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("HardenedMirrorSteel", (0.80, 0.82, 0.85), roughness=0.15, metallic=0.98)
    obj.data.materials.append(mat)
    
    return obj
