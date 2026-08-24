"""Pot — main ceramic planter holding the plant (part module; imported by src/model.py).

Tapered cylinder pot (base diameter 0.16 m, top rim diameter 0.22 m, height 0.20 m) with a rolled upper lip rim (0.02 m thick, 0.025 m high) and slight bevel on base edge.
Material: matte terracotta ceramic, warm reddish-clay. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_pot() -> bpy.types.Object:
    """Build ceramic pot with rolled rim, hollow interior, and terracotta material."""
    bm = bmesh.new()
    segments = 36
    
    # Profile for pot (r, z):
    # Base: z=0, r=0.074 to 0.080 (diameter 0.16)
    # Wall: z=0.01 to z=0.170, r goes from 0.080 to 0.102
    # Rolled rim outer: z=0.175..0.200, r goes to 0.110 (diameter 0.22)
    # Rim top: z=0.200, r=0.098..0.108
    # Inner wall: at z=0.180..0.195 r_inner = 0.098 (matching soil radius 0.0975)
    
    profile = [
        (0.000, 0.000),      # Center bottom outer
        (0.074, 0.000),      # Base bottom
        (0.080, 0.006),      # Base bevel
        (0.080, 0.020),      # Lower wall
        (0.102, 0.170),      # Below rim
        (0.108, 0.175),      # Rim bulge bottom
        (0.110, 0.1875),     # Rim outer peak (r=0.110 -> diam=0.220)
        (0.108, 0.200),      # Rim top outer
        (0.098, 0.200),      # Rim top inner
        (0.098, 0.174),      # Inner shelf for soil (soil base at 0.175)
        (0.072, 0.020),      # Inner bottom
        (0.000, 0.020)       # Center bottom inner
    ]
    
    rings = []
    for r, z in profile:
        ring = []
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            v = bm.verts.new((x, y, z))
            ring.append(v)
        rings.append(ring)
    
    bm.verts.ensure_lookup_table()
    
    # Bottom face (outer)
    bottom_center = bm.verts.new((0, 0, 0))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((bottom_center, rings[1][i], rings[1][i_next]))
        
    # Inner bottom face
    inner_bottom_center = bm.verts.new((0, 0, 0.020))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((inner_bottom_center, rings[-2][i_next], rings[-2][i]))
        
    # Side quads connecting adjacent rings
    for r_idx in range(len(rings) - 1):
        if r_idx == 0 or r_idx == len(rings) - 2:
            continue
        r1 = rings[r_idx]
        r2 = rings[r_idx + 1]
        for i in range(segments):
            i_next = (i + 1) % segments
            bm.faces.new((r1[i], r1[i_next], r2[i_next], r2[i]))
            
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Pot")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Pot", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Warm terracotta ceramic
    mat = make_material("PotMat", (0.72, 0.32, 0.18), roughness=0.75, metallic=0.02)
    obj.data.materials.append(mat)
    
    return obj
