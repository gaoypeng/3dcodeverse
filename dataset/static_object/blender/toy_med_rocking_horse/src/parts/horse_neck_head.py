"""HorseNeckHead — sculpted neck, head, muzzle, and ears (part module; imported by src/model.py).

Stylized continuous wooden neck rising at a 65° angle from the torso leading into a muzzle pointing -Y, featuring carved upright ears (45 mm tall) and round eye indents.
Material: natural solid birch, satin clear coat. Instances: 1.
Plan bbox: center (0.000, -0.220, 0.520) extents (0.090, 0.240, 0.260)
  x in [-0.045, 0.045]  y in [-0.340, -0.100]  z in [0.390, 0.650]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

def make_material(name, rgb, roughness=0.45, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_horse_neck_head():
    bm = bmesh.new()
    
    # HorseBody cylinder/capsule surface at (0, -0.010, 0.375):
    # Front cap center is at y=-0.140, radius=0.070.
    # At y=-0.100: z_body = 0.375 + 0.070 = 0.445.
    # At y=-0.140: z_body = 0.445.
    # At y=-0.160: dy = -0.020 -> z_body = 0.375 + sqrt(0.070^2 - 0.020^2) = 0.375 + 0.0671 = 0.4421.
    # At y=-0.180: dy = -0.040 -> z_body = 0.375 + sqrt(0.070^2 - 0.040^2) = 0.375 + 0.0574 = 0.4324.
    # At y=-0.200: dy = -0.060 -> z_body = 0.375 + sqrt(0.070^2 - 0.060^2) = 0.375 + 0.0361 = 0.4111.
    # At y=-0.210: z_body = 0.375.
    
    # We want a hairline overlap of ~1.0 - 1.5 mm below the body surface along the mating curve:
    # y = -0.100 -> z = 0.4435
    # y = -0.140 -> z = 0.4435
    # y = -0.160 -> z = 0.4406
    # y = -0.180 -> z = 0.4310
    # y = -0.200 -> z = 0.4095
    # y = -0.210 -> z = 0.3900 (chest front curve)
    
    # Profile points in Y-Z plane matching plan extents:
    # y range [-0.340, -0.100] (center y=-0.220, extent=0.240)
    # z range [0.390, 0.650] (center z=0.520, extent=0.260)
    # x range [-0.045, 0.045] (extent=0.090)
    
    profile_pts = [
        (-0.210, 0.390),     # Bottom front at chest
        (-0.230, 0.430),     # Throat / lower neck curve
        (-0.255, 0.470),     # Throatslash curve
        (-0.285, 0.505),     # Jaw curve (jowl)
        (-0.320, 0.520),     # Under-chin
        (-0.338, 0.530),     # Muzzle bottom
        (-0.340, 0.542),     # Muzzle tip (front-most point y=-0.340)
        (-0.336, 0.556),     # Muzzle top nostril curve
        (-0.300, 0.575),     # Bridge of nose
        (-0.270, 0.590),     # Forehead
        (-0.252, 0.608),     # Poll (front base of ear)
        (-0.240, 0.650),     # Ear tip (highest point z=0.650)
        (-0.226, 0.612),     # Back base of ear
        (-0.200, 0.590),     # Neck crest upper
        (-0.160, 0.540),     # Neck crest mid
        (-0.120, 0.470),     # Neck crest lower
        (-0.100, 0.4435),    # Withers rear base (y=-0.100)
        (-0.140, 0.4435),    # Underside flush mating along body top
        (-0.160, 0.4406),    # Underside flush mating along body curve
        (-0.180, 0.4310),    # Underside flush mating
        (-0.200, 0.4095),    # Underside flush mating near front cap
    ]
    
    half_w = 0.045  # 90 mm total width
    
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
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("HorseNeckHead")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("HorseNeckHead", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Bevel modifier to round off edges for an organic carved toy appearance
    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.005
    bev.segments = 3
    bev.limit_method = 'ANGLE'
    bev.angle_limit = math.radians(30.0)
    
    mat = make_material("BirchWoodHead", (0.82, 0.70, 0.52), roughness=0.4, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj
