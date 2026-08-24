"""Rocker — curved ground rail providing rocking motion (part module; imported by src/model.py).

Arched wooden runner plank (30 mm thick, 70 mm tall, 850 mm arc length) curving upwards at front (-Y) and rear (+Y) ends with soft rounded safety ends (r=12 mm).
Material: natural solid birch, satin clear coat. Instances: 2 (mirror_x).
Plan bbox: center (0.130, 0.000, 0.050) extents (0.030, 0.850, 0.100)
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

ROCKER_CENTER_X = 0.130
ROCKER_THICKNESS = 0.030  # dx: [-0.015, +0.015] -> [0.115, 0.145]
ROCKER_LENGTH = 0.850     # dy: [-0.425, +0.425]
ROCKER_HEIGHT = 0.100     # dz: [0.000, 0.100]

def make_material(name, rgb, roughness=0.45, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_rocker_mesh(name: str, x_pos: float, mat: bpy.types.Material) -> bpy.types.Object:
    bm = bmesh.new()
    
    # 2D profile in Y-Z plane, extruded along X
    # Y spans -0.425 to +0.425
    # Rocker curve: z_bottom(y) = 0.070 * (y / 0.425)^2
    # at y=0, z_bottom=0.000. At y=±0.425, z_bottom=0.070.
    # Height of rail = 0.030 m (30 mm).
    # at y=0, z_top = 0.030. At y=±0.425, z_top = 0.100.
    # At y=±0.160 (leg positions), z_bot = 0.070 * (0.160/0.425)^2 = 0.0099, z_top = 0.0399 ≈ 0.040.
    # FrontLeg and RearLeg bottoms are at z=0.040, touching/overlapping the rocker top!
    
    num_samples = 32
    bottom_pts = []
    top_pts = []
    
    for i in range(num_samples + 1):
        t = i / num_samples
        y = -0.425 + t * 0.850
        u = y / 0.425
        z_bot = 0.070 * (u ** 2)
        z_top = z_bot + 0.030
        bottom_pts.append((y, z_bot))
        top_pts.append((y, z_top))
    
    profile_pts = []
    for pt in bottom_pts:
        profile_pts.append(pt)
    for pt in reversed(top_pts):
        profile_pts.append(pt)
        
    half_th = ROCKER_THICKNESS / 2.0
    
    v_front = [bm.verts.new((half_th, y, z)) for y, z in profile_pts]
    v_back = [bm.verts.new((-half_th, y, z)) for y, z in profile_pts]
    
    n = len(profile_pts)
    for i in range(n):
        next_i = (i + 1) % n
        bm.faces.new([v_front[i], v_front[next_i], v_back[next_i], v_back[i]])
        
    bm.faces.new(v_front)
    bm.faces.new(list(reversed(v_back)))
    
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    bmesh.ops.translate(bm, vec=Vector((x_pos, 0, 0)), verts=bm.verts)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    
    return obj

def build_rocker():
    mat = make_material("BirchWood", (0.82, 0.70, 0.52), roughness=0.4, metallic=0.0)
    r0 = create_rocker_mesh("Rocker_0", ROCKER_CENTER_X, mat)
    r1 = create_rocker_mesh("Rocker_1", -ROCKER_CENTER_X, mat)
    return [r0, r1]
