"""Trunk — short fibrous palm trunk (part module; imported by src/model.py).

Cylindrical trunk (diameter 0.055 m at base tapering to 0.045 m at top, height 0.16 m) extending from z=0.18 to z=0.34 m, sculpted with horizontal ringed leaf-scar ridges and rough fibrous sheath texture.
Material: fibrous palm bark, textured wood brown with darker ring scars. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector

random.seed(0)

def make_material(name, rgb, roughness=0.8, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_trunk() -> bpy.types.Object:
    """Build palm trunk with sculpted horizontal ring ridges and fiber textures."""
    bm = bmesh.new()
    segments = 32
    n_rings = 18  # height steps
    z_min, z_max = 0.180, 0.332  # overlap 2mm with CrownShaft (which starts at 0.330)
    
    rings = []
    for step in range(n_rings):
        t = step / (n_rings - 1)
        z = z_min + t * (z_max - z_min)
        
        # Base diameter 0.055 (r=0.0275), top diameter 0.045 (r=0.0225)
        base_r = 0.0275 * (1.0 - t) + 0.0225 * t
        
        # Modulate radius with periodic horizontal ring leaf-scar ridges
        ridge_wave = math.sin(t * 6.0 * 2.0 * math.pi)
        ridge_offset = 0.0025 * max(0.0, ridge_wave) - 0.001 * min(0.0, ridge_wave)
        
        ring = []
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            noise = (random.random() - 0.5) * 0.001 + 0.001 * math.sin(angle * 4.0)
            r = base_r + ridge_offset + noise
            r = min(0.030, max(0.020, r))
            
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    # Cap bottom
    v_bot = bm.verts.new((0, 0, z_min))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((v_bot, rings[0][i_next], rings[0][i]))
        
    # Connect cylinder rings
    for r_idx in range(n_rings - 1):
        r1 = rings[r_idx]
        r2 = rings[r_idx + 1]
        for i in range(segments):
            i_next = (i + 1) % segments
            bm.faces.new((r1[i], r1[i_next], r2[i_next], r2[i]))
            
    # Cap top
    v_top = bm.verts.new((0, 0, z_max))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((v_top, rings[-1][i], rings[-1][i_next]))
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("Trunk")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Trunk", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Palm bark textured brown
    mat = make_material("TrunkMat", (0.42, 0.28, 0.16), roughness=0.85, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj
