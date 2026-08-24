"""Soil — potting soil surface (part module; imported by src/model.py).

Recessed circular disc (diameter 0.195 m, thickness 0.02 m) sitting 0.015 m below the pot rim, with rough displaced surface texture.
Material: dark moist potting soil, granular deep brown. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector

random.seed(0)

def make_material(name, rgb, roughness=0.9, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_soil() -> bpy.types.Object:
    """Build potting soil disc with a central collar matching trunk base for seamless contact."""
    bm = bmesh.new()
    segments = 32
    r_outer = 0.195 / 2.0  # 0.0975 m radius
    r_trunk = 0.0275       # trunk base radius
    z_bottom = 0.175
    z_top = 0.190
    
    # Soil surface rings: from inner ring (at trunk base) to outer ring
    n_rings = 4
    top_rings = []
    
    for r_i in range(n_rings):
        frac = r_i / (n_rings - 1)
        r = r_trunk + frac * (r_outer - r_trunk)
        ring = []
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            if r_i == 0:
                # Inner collar at z=0.183 (Trunk base is z=0.180, so 3mm overlap)
                z = 0.182
            elif r_i == n_rings - 1:
                z = z_top - 0.003
            else:
                z = z_top + (random.random() - 0.5) * 0.002
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            ring.append(bm.verts.new((x, y, z)))
        top_rings.append(ring)
        
    # Annular top faces
    for r_i in range(n_rings - 1):
        r1 = top_rings[r_i]
        r2 = top_rings[r_i + 1]
        for i in range(segments):
            i_next = (i + 1) % segments
            bm.faces.new((r1[i], r2[i], r2[i_next], r1[i_next]))
            
    # Inner cylinder / bottom ring to close mesh
    bot_ring_outer = []
    bot_ring_inner = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        x_out = r_outer * math.cos(angle)
        y_out = r_outer * math.sin(angle)
        bot_ring_outer.append(bm.verts.new((x_out, y_out, z_bottom)))
        
        x_in = r_trunk * math.cos(angle)
        y_in = r_trunk * math.sin(angle)
        bot_ring_inner.append(bm.verts.new((x_in, y_in, z_bottom)))
        
    # Connect bottom ring
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((bot_ring_inner[i], bot_ring_outer[i], bot_ring_outer[i_next], bot_ring_inner[i_next]))
        
    # Outer side wall
    outer_top = top_rings[-1]
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((outer_top[i], bot_ring_outer[i], bot_ring_outer[i_next], outer_top[i_next]))
        
    # Inner side wall
    inner_top = top_rings[0]
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((bot_ring_inner[i], inner_top[i], inner_top[i_next], bot_ring_inner[i_next]))
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Soil")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Soil", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("SoilMat", (0.16, 0.10, 0.06), roughness=0.95, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj
