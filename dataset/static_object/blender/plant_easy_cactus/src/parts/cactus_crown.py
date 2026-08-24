"""CactusCrown — woolly apical crown (part module; imported by src/model.py).

Dense flattened dome of pale yellow-cream woolly fibers and nascent central spine buds at the apex depression of the cactus (d=0.06 m, h=0.025 m).
Material: fuzzy cactus wool, pale cream to warm straw yellow.
Plan bbox: center (0.000, 0.000, 0.350) extents (0.060, 0.060, 0.025)
  z in [0.3375, 0.3625], x in [-0.030, 0.030], y in [-0.030, 0.030]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(101)

CACTUS_CROWN_CENTER = (0.000, 0.000, 0.350)
CACTUS_CROWN_EXTENTS = (0.060, 0.060, 0.025)

def make_material(name, rgb, roughness=0.85, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_cactus_crown() -> bpy.types.Object:
    """Builds a single watertight tufted woolly dome at the apex of the cactus."""
    bm = bmesh.new()
    
    rings_count = 12
    segments = 36
    max_r = 0.030
    z_base = 0.3375
    z_peak = 0.3625
    
    # Bottom center vertex (embeds slightly into cactus body top)
    v_bottom = bm.verts.new((0, 0, z_base))
    
    # Bottom perimeter ring
    bot_ring = []
    for s in range(segments):
        ang = 2 * math.pi * s / segments
        x = max_r * math.cos(ang)
        y = max_r * math.sin(ang)
        v = bm.verts.new((x, y, z_base))
        bot_ring.append(v)
        
    for s in range(segments):
        bm.faces.new([v_bottom, bot_ring[(s + 1) % segments], bot_ring[s]])
        
    # Upper dome concentric rings with tufted woolly hills and valleys
    dome_rings = []
    for r_idx in range(1, rings_count + 1):
        frac = r_idx / rings_count # 1/12 to 1 (outermost)
        r = max_r * frac
        h_frac = math.sqrt(max(0.0, 1.0 - frac**1.6))
        base_z = z_base + (z_peak - z_base) * h_frac
        
        ring = []
        for s in range(segments):
            ang = 2 * math.pi * s / segments
            # Bumpy woolly tuft lobes
            lobe_noise = 0.0020 * math.sin(10 * ang) * math.sin(math.pi * frac)
            micro_noise = 0.0008 * math.cos(20 * ang)
            z_val = max(z_base, min(z_peak, base_z + lobe_noise + micro_noise))
            
            r_noisy = r * (1.0 + 0.04 * math.sin(10 * ang) * math.sin(math.pi * frac))
            r_noisy = min(max_r, r_noisy)
            
            x = r_noisy * math.cos(ang)
            y = r_noisy * math.sin(ang)
            v = bm.verts.new((x, y, z_val))
            ring.append(v)
        dome_rings.append(ring)
        
    # Top apex vertex
    v_top = bm.verts.new((0, 0, z_peak))
    for s in range(segments):
        bm.faces.new([v_top, dome_rings[0][s], dome_rings[0][(s + 1) % segments]])
        
    # Connect dome rings
    for r_idx in range(rings_count - 1):
        r1 = dome_rings[r_idx]
        r2 = dome_rings[r_idx + 1]
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([r1[s], r2[s], r2[s_next], r1[s_next]])
            
    # Connect outermost dome ring to bottom ring
    outer_dome = dome_rings[-1]
    for s in range(segments):
        s_next = (s + 1) % segments
        bm.faces.new([outer_dome[s], bot_ring[s], bot_ring[s_next], outer_dome[s_next]])
        
    bm.normal_update()
    
    # Scale to ensure exact bbox: x/y in [-0.030, 0.030], z in [0.3375, 0.3625]
    max_x = max(abs(v.co.x) for v in bm.verts)
    max_y = max(abs(v.co.y) for v in bm.verts)
    if max_x > 0 and max_y > 0:
        for v in bm.verts:
            v.co.x *= (0.030 / max_x)
            v.co.y *= (0.030 / max_y)
            
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)
    z_span = max_z - min_z
    if z_span > 0:
        for v in bm.verts:
            nz = (v.co.z - min_z) / z_span
            v.co.z = 0.3375 + nz * 0.025
            
    bm.normal_update()
    me = bpy.data.meshes.new("CactusCrown")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("CactusCrown", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("CactusWool", (0.92, 0.88, 0.65), roughness=0.9, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
