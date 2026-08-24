"""HorseMane — decorative mane along the neck crest (part module; imported by src/model.py).

Segmented stepped/grooved wooden crest profile 24 mm wide following the curve of the neck from poll to withers.
Material: stained walnut wood, dark brown. Instances: 1.
Plan bbox: center (0.000, -0.160, 0.540) extents (0.024, 0.160, 0.200)
  x in [-0.012, 0.012]  y in [-0.240, -0.080]  z in [0.440, 0.640]
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

def build_horse_mane():
    bm = bmesh.new()
    
    # Neck crest profile in HorseNeckHead:
    # (-0.220, 0.605) -> (-0.200, 0.590) -> (-0.160, 0.540) -> (-0.120, 0.470) -> (-0.100, 0.435)
    # HorseNeckHead width is 90 mm (x from -0.045 to +0.045).
    # HorseMane width is 24 mm (x from -0.012 to +0.012).
    # Since HorseMane is narrower than HorseNeckHead, any part of HorseMane that is inside HorseNeckHead's 2D profile
    # is considered inside the volume. Thus base_points must match the neck crest exactly (with minimal overlap).
    
    base_points = [
        (-0.098, 0.444),
        (-0.120, 0.470),
        (-0.160, 0.540),
        (-0.200, 0.590),
        (-0.220, 0.605),
    ]
    
    # Softened, stylized carved crest scallops with flowing curves
    outer_points = [
        (-0.236, 0.615),
        (-0.230, 0.638),
        (-0.218, 0.640),
        (-0.208, 0.628),
        (-0.200, 0.618),
        (-0.190, 0.615),
        (-0.178, 0.598),
        (-0.170, 0.585),
        (-0.160, 0.582),
        (-0.148, 0.562),
        (-0.140, 0.545),
        (-0.130, 0.540),
        (-0.118, 0.515),
        (-0.110, 0.495),
        (-0.100, 0.485),
        (-0.088, 0.458),
        (-0.080, 0.446),
    ]
    
    profile_pts = base_points + outer_points
    
    half_w = 0.012  # 24 mm total width
    
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
    
    me = bpy.data.meshes.new("HorseMane")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("HorseMane", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Subtle bevel modifier to soften the outer edges
    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.002
    bev.segments = 2
    
    mat = make_material("WalnutMane", (0.24, 0.14, 0.08), roughness=0.5, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj
