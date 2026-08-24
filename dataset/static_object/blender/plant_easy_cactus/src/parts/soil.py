"""Soil — potting soil substrate (part module; imported by src/model.py).

Slightly mounded circular disk (d=0.178 m, thickness 0.02 m) seated inside the pot opening 0.015 m below the top rim, with fine granular surface texture.
Material: dark organic potting soil, gritty earthy charcoal brown.
Plan bbox: center (0.000, 0.000, 0.175) extents (0.178, 0.178, 0.020)
  z in [0.165, 0.185]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(42)

SOIL_CENTER = (0.000, 0.000, 0.175)
SOIL_EXTENTS = (0.178, 0.178, 0.020)

def make_material(name, rgb, roughness=0.9, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_soil() -> bpy.types.Object:
    """Builds the soil disk with slight mound and organic granular displacements."""
    bm = bmesh.new()
    segments = 36
    rings_count = 8
    max_r = 0.178 / 2.0  # 0.089
    
    # Bottom center vertex at z=0.163 (extends slightly into pot body for weld)
    v_bottom_center = bm.verts.new((0, 0, 0.163))
    
    # Bottom perimeter ring
    bottom_ring = []
    for s in range(segments):
        ang = 2 * math.pi * s / segments
        x = max_r * math.cos(ang)
        y = max_r * math.sin(ang)
        v = bm.verts.new((x, y, 0.163))
        bottom_ring.append(v)
        
    for s in range(segments):
        bm.faces.new([v_bottom_center, bottom_ring[(s + 1) % segments], bottom_ring[s]])
        
    # Top concentric rings creating a gentle mound up to z=0.185
    top_rings = []
    for r_idx in range(1, rings_count + 1):
        frac = r_idx / rings_count
        r = max_r * frac
        # Mound profile: highest at center (z=0.185), drops to z=0.175 at edge
        base_z = 0.185 - 0.010 * (frac ** 1.5)
        ring = []
        for s in range(segments):
            ang = 2 * math.pi * s / segments
            noise = (random.random() - 0.5) * 0.0015
            z_val = max(0.165, min(0.185, base_z + noise))
            x = r * math.cos(ang)
            y = r * math.sin(ang)
            v = bm.verts.new((x, y, z_val))
            ring.append(v)
        top_rings.append(ring)
        
    # Top center vertex at z=0.185
    v_top_center = bm.verts.new((0, 0, 0.185))
    
    # Connect top center to first top ring
    for s in range(segments):
        bm.faces.new([v_top_center, top_rings[0][s], top_rings[0][(s + 1) % segments]])
        
    # Connect top rings
    for r_idx in range(rings_count - 1):
        r1 = top_rings[r_idx]
        r2 = top_rings[r_idx + 1]
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([r1[s], r2[s], r2[s_next], r1[s_next]])
            
    # Connect outermost top ring to bottom ring (side wall)
    outer_top_ring = top_rings[-1]
    for s in range(segments):
        s_next = (s + 1) % segments
        bm.faces.new([outer_top_ring[s], bottom_ring[s], bottom_ring[s_next], outer_top_ring[s_next]])
        
    bm.normal_update()
    me = bpy.data.meshes.new("Soil")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Soil", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("SoilMat", (0.18, 0.13, 0.09), roughness=0.95, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
