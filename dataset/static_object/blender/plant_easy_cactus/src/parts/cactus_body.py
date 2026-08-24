"""CactusBody — fleshy ribbed cactus stem (part module; imported by src/model.py).

Globular spherical barrel cactus stem (d=0.17 m, h=0.17 m) embedded 0.01 m into the soil, featuring 20 sharp vertical undulating radial ribs and a depressed apical top.
Material: cactus skin, deep sage green with subtle waxy sheen.
Plan bbox: center (0.000, 0.000, 0.260) extents (0.170, 0.170, 0.170)
  z in [0.175, 0.345], x in [-0.085, 0.085], y in [-0.085, 0.085]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

CACTUS_BODY_CENTER = (0.000, 0.000, 0.260)
CACTUS_BODY_EXTENTS = (0.170, 0.170, 0.170)

def make_material(name, rgb, roughness=0.32, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_cactus_body() -> bpy.types.Object:
    """Builds a 20-ribbed globose barrel cactus body."""
    bm = bmesh.new()
    
    num_ribs = 20
    samples_per_rib = 6
    num_phi = num_ribs * samples_per_rib  # 120
    num_theta = 48
    
    z_min = 0.175
    z_max = 0.345
    z_center = 0.260
    h_half = 0.085
    r_max = 0.085
    
    grid = []
    
    for t_idx in range(num_theta + 1):
        v_frac = t_idx / num_theta
        lat = -math.pi / 2.0 + math.pi * v_frac
        base_r_frac = math.cos(lat)
        
        if v_frac > 0.85:
            top_frac = (v_frac - 0.85) / 0.15
            apex_depression = 0.008 * (top_frac ** 2)
        else:
            apex_depression = 0.0
            
        z = z_center + h_half * math.sin(lat) - apex_depression
        rib_amp_factor = (base_r_frac ** 0.8) * 0.16
        
        row = []
        for p_idx in range(num_phi):
            phi = 2 * math.pi * p_idx / num_phi
            rib_wave = math.cos(num_ribs * phi)
            rib_factor = 1.0 + rib_amp_factor * (math.copysign(abs(rib_wave)**0.7, rib_wave))
            
            r = (base_r_frac * r_max) * rib_factor / (1.0 + 0.16)
            undulation = 0.001 * math.sin(6.0 * lat + 3.0 * phi)
            r = max(0.001, r + undulation)
            
            x = r * math.cos(phi)
            y = r * math.sin(phi)
            z_clamped = max(z_min, min(z_max, z))
            
            v = bm.verts.new((x, y, z_clamped))
            row.append(v)
        grid.append(row)
        
    for t_idx in range(num_theta):
        row1 = grid[t_idx]
        row2 = grid[t_idx + 1]
        for p_idx in range(num_phi):
            p_next = (p_idx + 1) % num_phi
            v1 = row1[p_idx]
            v2 = row1[p_next]
            v3 = row2[p_next]
            v4 = row2[p_idx]
            bm.faces.new([v1, v2, v3, v4])
            
    # Bottom pole
    v_bot_pole = bm.verts.new((0, 0, z_min))
    for p_idx in range(num_phi):
        p_next = (p_idx + 1) % num_phi
        bm.faces.new([v_bot_pole, grid[0][p_next], grid[0][p_idx]])
        
    # Top pole
    v_top_pole = bm.verts.new((0, 0, min(grid[-1][0].co.z, z_max - 0.005)))
    for p_idx in range(num_phi):
        p_next = (p_idx + 1) % num_phi
        bm.faces.new([v_top_pole, grid[-1][p_idx], grid[-1][p_next]])
        
    bm.normal_update()
    
    max_x = max(abs(v.co.x) for v in bm.verts)
    max_y = max(abs(v.co.y) for v in bm.verts)
    if max_x > 0 and max_y > 0:
        for v in bm.verts:
            v.co.x *= (0.085 / max_x)
            v.co.y *= (0.085 / max_y)
            
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)
    z_span = max_z - min_z
    if z_span > 0:
        for v in bm.verts:
            nz = (v.co.z - min_z) / z_span
            v.co.z = z_min + nz * 0.170
            
    bm.normal_update()
    me = bpy.data.meshes.new("CactusBody")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("CactusBody", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Deep rich botanical cactus green
    mat = make_material("CactusGreen", (0.08, 0.32, 0.14), roughness=0.32, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
