"""PotBody — main conical pot chamber (part module; imported by src/model.py).

Tapered round terracotta pot body rising from base diameter 0.13 m to upper diameter 0.185 m, hollowed top with 0.01 m wall thickness, nested 3 mm into the saucer.
Material: terracotta clay, unglazed matte warm orange-red.
Plan bbox: center (0.000, 0.000, 0.090) extents (0.185, 0.185, 0.150)
  z in [0.015, 0.165]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

POT_BODY_CENTER = (0.000, 0.000, 0.090)
POT_BODY_EXTENTS = (0.185, 0.185, 0.150)

def make_material(name, rgb, roughness=0.62, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_pot_body() -> bpy.types.Object:
    """Builds the tapered pot body from z=0.006 up to z=0.165."""
    bm = bmesh.new()
    segments = 48
    
    profile = [
        (0.000, 0.006),      # center base bottom
        (0.063, 0.006),      # bottom edge
        (0.065, 0.015),      # nominal base edge (d=0.130)
        (0.0925, 0.165),     # outer top edge (d=0.185)
        (0.0825, 0.165),     # inner top edge
        (0.055, 0.020),      # inner bottom edge
        (0.000, 0.020),      # inner floor center
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
    me = bpy.data.meshes.new("PotBody")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("PotBody", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("TerracottaPotBody", (0.64, 0.22, 0.11), roughness=0.62, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
