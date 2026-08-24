"""Saucer — drainage saucer tray (part module; imported by src/model.py).

Shallow circular terracotta tray with a slightly raised outer lip (d=0.18 m, base rim h=0.025 m, wall thickness 0.008 m) resting on the ground plane.
Material: terracotta clay, unglazed matte warm orange-red.
Plan bbox: center (0.000, 0.000, 0.013) extents (0.180, 0.180, 0.025)
  z in [0.000, 0.025]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

SAUCER_CENTER = (0.000, 0.000, 0.0125)
SAUCER_EXTENTS = (0.180, 0.180, 0.025)

def make_material(name, rgb, roughness=0.62, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_saucer() -> bpy.types.Object:
    """Builds the circular saucer with a rim lip and recessed interior."""
    bm = bmesh.new()
    segments = 48
    
    profile = [
        (0.000, 0.000),      # center bottom
        (0.080, 0.000),      # outer base
        (0.088, 0.004),      # outer lower chamfer
        (0.090, 0.025),      # outer top lip (max r=0.090 -> d=0.180, max z=0.025)
        (0.083, 0.025),      # top rim inner
        (0.077, 0.008),      # inner wall bottom
        (0.000, 0.008),      # center inner floor
    ]
    
    rings = []
    for r, z in profile:
        if r == 0.0:
            v = bm.verts.new((0, 0, z))
            rings.append([v])
        else:
            ring = []
            for s in range(segments):
                ang = 2 * math.pi * s / segments
                x = r * math.cos(ang)
                y = r * math.sin(ang)
                v = bm.verts.new((x, y, z))
                ring.append(v)
            rings.append(ring)
            
    for i in range(len(rings) - 1):
        r1 = rings[i]
        r2 = rings[i + 1]
        
        if len(r1) == 1 and len(r2) > 1:
            center_v = r1[0]
            for s in range(segments):
                bm.faces.new([center_v, r2[(s + 1) % segments], r2[s]])
        elif len(r1) > 1 and len(r2) > 1:
            for s in range(segments):
                s_next = (s + 1) % segments
                bm.faces.new([r1[s], r1[s_next], r2[s_next], r2[s]])
        elif len(r1) > 1 and len(r2) == 1:
            center_v = r2[0]
            for s in range(segments):
                bm.faces.new([r1[s], r1[(s + 1) % segments], center_v])
                
    bm.normal_update()
    me = bpy.data.meshes.new("Saucer")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Saucer", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Rich warm earthy terracotta orange-red
    mat = make_material("TerracottaSaucer", (0.64, 0.22, 0.11), roughness=0.62, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
