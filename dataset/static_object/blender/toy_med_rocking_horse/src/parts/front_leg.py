"""FrontLeg — forward supporting leg (part module; imported by src/model.py).

Solid wooden leg angled forward (-Y) and outward (+X), mortised into the rocker below and the body above with beveled edges.
Material: natural solid birch, satin clear coat. Instances: 2 (mirror_x).
Plan bbox: center (0.090, -0.160, 0.220) extents (0.035, 0.070, 0.280)
  x in [0.0725, 0.1075]  y in [-0.195, -0.125]  z in [0.080, 0.360]
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

def create_leg(name: str, x_sign: float, mat: bpy.types.Material) -> bpy.types.Object:
    bm = bmesh.new()
    
    # Body lower surface at y=-0.160 has radius 0.070, so at x=0.060, z_bot = 0.375 - sqrt(0.070^2 - 0.060^2) = 0.375 - 0.036 = 0.339
    # Setting top of leg to z=0.341 gives exactly 2 mm overlap into the body underside!
    
    b0 = (0.080, -0.195, 0.040)
    b1 = (0.120, -0.195, 0.040)
    b2 = (0.120, -0.125, 0.040)
    b3 = (0.080, -0.125, 0.040)
    
    t0 = (0.055, -0.180, 0.341)
    t1 = (0.095, -0.180, 0.341)
    t2 = (0.095, -0.125, 0.341)
    t3 = (0.055, -0.125, 0.341)
    
    verts_coords = [b0, b1, b2, b3, t0, t1, t2, t3]
    if x_sign < 0:
        verts_coords = [(-x, y, z) for x, y, z in verts_coords]
        
    bm_verts = [bm.verts.new(v) for v in verts_coords]
    
    bm.faces.new([bm_verts[3], bm_verts[2], bm_verts[1], bm_verts[0]] if x_sign > 0 else [bm_verts[0], bm_verts[1], bm_verts[2], bm_verts[3]])
    bm.faces.new([bm_verts[4], bm_verts[5], bm_verts[6], bm_verts[7]] if x_sign > 0 else [bm_verts[7], bm_verts[6], bm_verts[5], bm_verts[4]])
    bm.faces.new([bm_verts[0], bm_verts[1], bm_verts[5], bm_verts[4]] if x_sign > 0 else [bm_verts[4], bm_verts[5], bm_verts[1], bm_verts[0]])
    bm.faces.new([bm_verts[1], bm_verts[2], bm_verts[6], bm_verts[5]] if x_sign > 0 else [bm_verts[5], bm_verts[6], bm_verts[2], bm_verts[1]])
    bm.faces.new([bm_verts[2], bm_verts[3], bm_verts[7], bm_verts[6]] if x_sign > 0 else [bm_verts[6], bm_verts[7], bm_verts[3], bm_verts[2]])
    bm.faces.new([bm_verts[3], bm_verts[0], bm_verts[4], bm_verts[7]] if x_sign > 0 else [bm_verts[7], bm_verts[4], bm_verts[0], bm_verts[3]])
    
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    
    return obj

def build_front_leg():
    mat = make_material("BirchWoodLeg", (0.82, 0.70, 0.52), roughness=0.4, metallic=0.0)
    l0 = create_leg("FrontLeg_0", 1.0, mat)
    l1 = create_leg("FrontLeg_1", -1.0, mat)
    return [l0, l1]
