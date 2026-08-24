"""StrikingNeck — tapered neck connecting eye to striking face.

Solid steel cylinder (diameter 28 mm tapering to 30 mm) projecting forward along -Y from the eye block.
Material: polished forged steel.  Instances: 1.  Attaches to: HammerEye.
Plan bbox: center (0.000, -0.040, 0.305) extents (0.030, 0.045, 0.030)
  x in [-0.015, 0.015]  y in [-0.0625, -0.0175]  z in [0.290, 0.320]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

STRIKING_NECK_CENTER = (0.000, -0.040, 0.305)
STRIKING_NECK_EXTENTS = (0.030, 0.045, 0.030)

def make_material(name, rgb, roughness=0.3, metallic=0.95):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_striking_neck():
    """Tapered neck projecting along -Y."""
    bm = bmesh.new()
    
    # Neck runs from y = -0.0175 (attached to HammerEye) to y = -0.0625 (attached to StrikingFace)
    # Extents: X span 0.030 (radius 0.015), Z span 0.030 (radius 0.015)
    # Tapers slightly from eye (y=-0.0175, r=0.014) to face (y=-0.0625, r=0.015)
    
    n_segments = 32
    y_levels = [
        (-0.0175, 0.0142, 0.0142), # weld to eye
        (-0.0300, 0.0135, 0.0135), # gentle waist
        (-0.0500, 0.0145, 0.0145), # expanding towards face
        (-0.0625, 0.0150, 0.0150), # front joint with striking face
    ]
    
    rings = []
    for y, rx, rz in y_levels:
        ring_verts = []
        for i in range(n_segments):
            angle = 2.0 * math.pi * i / n_segments
            x = rx * math.cos(angle)
            z = 0.305 + rz * math.sin(angle)
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
    bm.faces.new(rings[-1])
    
    bm.normal_update()
    me = bpy.data.meshes.new("StrikingNeck")
    bm.to_mesh(me)
    bm.free()
    
    for poly in me.polygons:
        poly.use_smooth = True
        
    obj = bpy.data.objects.new("StrikingNeck", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("ForgedSteelNeck", (0.65, 0.67, 0.70), roughness=0.25, metallic=0.95)
    obj.data.materials.append(mat)
    
    return obj
