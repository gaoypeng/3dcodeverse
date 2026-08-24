"""GuitarNeck — structural neck beam (part module; imported by src/model.py).

Rounded D-profile mahogany neck running from the body heel joint at z=0.46 up to the headstock base at z=0.86, tapering slightly from 54 mm wide at heel to 43 mm at nut.
Material: satin finished mahogany. Instances: 1. Attaches to: GuitarBody.

Plan bbox: center (0.000, 0.012, 0.660) extents (0.054, 0.040, 0.400)
x in [-0.027, 0.027], y in [-0.008, 0.032], z in [0.460, 0.860]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

GUITAR_NECK_CENTER = (0.000, 0.012, 0.660)
GUITAR_NECK_EXTENTS = (0.054, 0.040, 0.400)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_guitar_neck():
    bm = bmesh.new()
    
    # Neck spans z in [0.498, 0.860] (length ~0.362, overlaps GuitarBody by 2 mm at z=0.500)
    # Heel junction at z = 0.498 sits flush against the body top shoulder (z = 0.500) with 2 mm weld overlap.
    
    num_z_steps = 16
    segments_d = 12
    rings = []
    
    for i in range(num_z_steps):
        t = i / (num_z_steps - 1)
        z = 0.498 + t * (0.860 - 0.498)
        
        # Width taper
        half_w = 0.027 * (1.0 - t) + 0.0215 * t
        
        y_front = -0.008
        if t < 0.12:
            u = t / 0.12
            y_back_max = 0.032 * (1.0 - u) + 0.020 * u
        else:
            u = (t - 0.12) / 0.88
            y_back_max = 0.020 * (1.0 - u) + 0.016 * u
            
        ring_pts = []
        ring_pts.append((half_w, y_front, z))
        
        depth = y_back_max - y_front
        for j in range(segments_d + 1):
            theta = math.pi * j / segments_d
            px = half_w * math.cos(theta)
            py = y_front + depth * math.sin(theta)
            ring_pts.append((px, py, z))
            
        ring_pts.append((-half_w, y_front, z))
        
        ring_verts = [bm.verts.new(p) for p in ring_pts]
        rings.append(ring_verts)
        
    bm.verts.ensure_lookup_table()
    
    for i in range(num_z_steps - 1):
        r0 = rings[i]
        r1 = rings[i+1]
        for j in range(len(r0) - 1):
            bm.faces.new([r0[j], r0[j+1], r1[j+1], r1[j]])
        bm.faces.new([r0[-1], r0[0], r1[0], r1[-1]])
        
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in rings[0] and e.verts[1] in rings[0]])
    bmesh.ops.triangle_fill(bm, use_beauty=True, edges=[e for e in bm.edges if e.verts[0] in rings[-1] and e.verts[1] in rings[-1]])
    
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("GuitarNeck")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("GuitarNeck", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_neck = make_material("MahoganyNeck", (0.38, 0.20, 0.12), roughness=0.4, metallic=0.0)
    obj.data.materials.append(mat_neck)
    
    return obj
