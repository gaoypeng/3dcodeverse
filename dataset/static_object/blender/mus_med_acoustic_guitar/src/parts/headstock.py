"""Headstock — headstock holding tuning machines (part module; imported by src/model.py).

Classic paddle-shaped headstock angled backward by 12 degrees from the nut (z=0.86 to 1.01m), flared profile with bevelled edges and front veneer.
Material: varnished mahogany with ebony front veneer. Instances: 1. Attaches to: GuitarNeck.

Plan bbox: center (0.000, 0.035, 0.935) extents (0.076, 0.035, 0.160)
x in [-0.038, 0.038], y in [0.018, 0.053], z in [0.855, 1.015]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

HEADSTOCK_CENTER = (0.000, 0.035, 0.935)
HEADSTOCK_EXTENTS = (0.076, 0.035, 0.160)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_headstock():
    bm = bmesh.new()
    
    # Headstock spans z from 0.855 to 1.015 (length 0.160m)
    # Starts at neck joint z=0.855 (touches neck at z=0.860 with ~5mm overlap for weld)
    # At z=0.855, neck top was at y in [-0.008, 0.016], x in [-0.0215, 0.0215]
    # Headstock base at z=0.855: y in [0.006, 0.020]
    
    num_steps = 12
    rings = []
    
    for i in range(num_steps):
        t = i / (num_steps - 1)
        z = 0.855 + t * (1.015 - 0.855)
        
        # Width flare profile:
        if t < 0.7:
            u = t / 0.7
            half_w = 0.0215 * (1.0 - u) + 0.038 * u
        else:
            u = (t - 0.7) / 0.3
            half_w = 0.038 * (1.0 - 0.1 * u)
            
        # Slope in Y:
        # At base (z=0.855), front is at y=0.010, back at 0.024
        # At top (z=1.015), front is at y=0.038, back at 0.052
        y_front = 0.010 + t * (0.038 - 0.010)
        y_back = y_front + 0.014
        
        v_fl = bm.verts.new((-half_w, y_front, z))
        v_fr = bm.verts.new(( half_w, y_front, z))
        v_br = bm.verts.new(( half_w, y_back, z))
        v_bl = bm.verts.new((-half_w, y_back, z))
        
        rings.append([v_fl, v_fr, v_br, v_bl])
        
    bm.verts.ensure_lookup_table()
    
    for i in range(num_steps - 1):
        r0 = rings[i]
        r1 = rings[i+1]
        bm.faces.new([r0[0], r1[0], r1[1], r0[1]])
        bm.faces.new([r0[1], r1[1], r1[2], r0[2]])
        bm.faces.new([r0[2], r1[2], r1[3], r0[3]])
        bm.faces.new([r0[3], r1[3], r1[0], r0[0]])
        
    bm.faces.new([rings[0][0], rings[0][1], rings[0][2], rings[0][3]])
    bm.faces.new([rings[-1][3], rings[-1][2], rings[-1][1], rings[-1][0]])
    
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Headstock")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Headstock", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_head = make_material("VarnishedMahogany", (0.35, 0.18, 0.10), roughness=0.35, metallic=0.0)
    obj.data.materials.append(mat_head)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0025
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    return obj
