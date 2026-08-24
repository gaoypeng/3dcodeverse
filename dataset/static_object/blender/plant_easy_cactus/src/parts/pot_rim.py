"""PotRim — projecting top collar of pot (part module; imported by src/model.py).

Thick circular cylindrical lip projecting 0.012 m outward past the pot body (d=0.21 m, h=0.04 m) with beveled top and bottom edges.
Material: terracotta clay, unglazed matte warm orange-red.
Plan bbox: center (0.000, 0.000, 0.180) extents (0.210, 0.210, 0.040)
  z in [0.160, 0.200]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

POT_RIM_CENTER = (0.000, 0.000, 0.180)
POT_RIM_EXTENTS = (0.210, 0.210, 0.040)

def make_material(name, rgb, roughness=0.62, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_pot_rim() -> bpy.types.Object:
    """Builds the thick projecting pot rim collar."""
    bm = bmesh.new()
    segments = 48
    
    profile = [
        (0.0910, 0.160),     # Bottom inner-outer junction matching pot body outer
        (0.1050, 0.165),     # Outer bottom bevel
        (0.1050, 0.195),     # Outer top start bevel
        (0.0980, 0.200),     # Top outer lip
        (0.0890, 0.200),     # Top inner lip
        (0.0890, 0.160),     # Inner wall
        (0.0810, 0.160),     # Connect back towards interior
    ]
    
    rings = []
    for r, z in profile:
        ring = []
        for s in range(segments):
            ang = 2 * math.pi * s / segments
            x = r * math.cos(ang)
            y = r * math.sin(ang)
            v = bm.verts.new((x, y, z))
            ring.append(v)
        rings.append(ring)
        
    for i in range(len(rings)):
        r1 = rings[i]
        r2 = rings[(i + 1) % len(rings)]
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([r1[s], r1[s_next], r2[s_next], r2[s]])
            
    bm.normal_update()
    me = bpy.data.meshes.new("PotRim")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("PotRim", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("TerracottaPotRim", (0.64, 0.22, 0.11), roughness=0.62, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
