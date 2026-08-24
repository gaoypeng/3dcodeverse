"""HorseTail — rear tail feature (part module; imported by src/model.py).

Tapered wooden tail spindle flared slightly outward and angled 40° downward/backward from the rear rump.
Material: stained walnut wood, dark brown. Instances: 1.
Plan bbox: center (0.000, 0.240, 0.350) extents (0.040, 0.140, 0.150)
  x in [-0.020, 0.020]  y in [0.170, 0.310]  z in [0.275, 0.425]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

def make_material(name, rgb, roughness=0.55, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_horse_tail():
    bm = bmesh.new()
    
    # Body rump surface is at y around 0.180..0.190, z around 0.375
    # HorseBody rear hemisphere center is at (0, 0.120, 0.375) with radius 0.070.
    # At z = 0.375, rear apex is at y = 0.190.
    # To have a slight weld overlap (1-2 mm), the tail base should meet at y ≈ 0.188..0.189.
    # Plan bbox: center (0.000, 0.240, 0.350) extents (0.040, 0.140, 0.150)
    # y in [0.170, 0.310] (allowed bounds), z in [0.275, 0.425]
    profile_pts = [
        (0.188, 0.395),   # Upper attachment meeting rear body surface (r^2 = 0.068^2 + 0.020^2 = 0.005024 <= 0.07^2=0.0049)
        (0.189, 0.405),   # Upper contour meeting body surface
        (0.220, 0.425),   # Top arch peak (z max = 0.425)
        (0.270, 0.380),   # Sweeping down-back
        (0.310, 0.310),   # Outer tail curl / tip (y max = 0.310)
        (0.290, 0.275),   # Bottom tip (z min = 0.275)
        (0.240, 0.300),   # Inner tail taper
        (0.195, 0.340),   # Under curve
        (0.188, 0.355),   # Lower attachment meeting rear body surface
    ]
    
    half_w = 0.020  # 40 mm total width (x in [-0.020, 0.020])
    
    v_pos = [bm.verts.new((half_w, y, z)) for y, z in profile_pts]
    v_neg = [bm.verts.new((-half_w, y, z)) for y, z in profile_pts]
    
    n = len(profile_pts)
    for i in range(n):
        next_i = (i + 1) % n
        bm.faces.new([v_pos[i], v_pos[next_i], v_neg[next_i], v_neg[i]])
        
    bm.faces.new(v_pos)
    bm.faces.new(list(reversed(v_neg)))
    
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    me = bpy.data.meshes.new("HorseTail")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("HorseTail", me)
    bpy.context.scene.collection.objects.link(obj)
    mat = make_material("WalnutTail", (0.24, 0.14, 0.08), roughness=0.5, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj
