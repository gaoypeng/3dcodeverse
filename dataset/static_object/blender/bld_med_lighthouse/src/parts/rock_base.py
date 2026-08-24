"""RockBase — natural stone foundation and shoreline outcropping.
Irregular sculpted rocky outcrop platform standing on z=0; rough fractured cliffs with stepped ledges,
crags, fissures, and faceted rocky outcropping.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector

# Plan numbers
ROCK_BASE_CENTER = (0.000, 0.000, 0.750)
ROCK_BASE_EXTENTS = (5.800, 5.800, 1.500)

def make_material(name, rgb, roughness=0.9, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_rock_base() -> bpy.types.Object:
    random.seed(1337)
    
    # We construct a multi-tiered, faceted rocky crag / cliff outcropping.
    # We can create a base cylinder/mesh with multiple height subdivisions and radial segments,
    # then apply layered 3D procedural noise/displacement, facet cuts, steps/ledges, and rocky crevices.
    
    bm = bmesh.new()
    
    n_radial = 48
    n_height = 16
    
    # Tower Plinth sits on top from z=1.498 to 2.200 with radius 1.700 (dia 3.4m).
    # So top of RockBase around radius <= 1.75 should stay flat enough around z=1.50 for solid seating/overlap.
    
    rings = []
    
    for h_idx in range(n_height + 1):
        v_frac = h_idx / n_height # 0.0 at bottom to 1.0 at top
        z_base = v_frac * 1.50
        
        # Profile radius from bottom (approx 2.85m) to top terrace (approx 1.95m) with stepped ledges
        # Introduce a couple of shelf steps in the profile
        step_mod = math.sin(v_frac * math.pi * 3.5) * 0.12
        r_profile = 2.85 * (1.0 - 0.32 * (v_frac ** 0.8)) + step_mod
        
        ring = []
        for r_idx in range(n_radial):
            theta = 2.0 * math.pi * r_idx / n_radial
            
            # Multi-octave angular and height noise for rocky crags, promontories and clefts
            n1 = math.sin(3 * theta) * 0.28 + math.cos(5 * theta) * 0.18
            n2 = math.sin(7 * theta + v_frac * 6.0) * 0.12 + math.cos(11 * theta - v_frac * 4.0) * 0.08
            n3 = (math.sin(2 * theta + 1.2) ** 2) * 0.22
            # Blocky / stepped crags: quantize some radial perturbations
            block_noise = (int((theta / (2 * math.pi)) * 12) % 3 - 1) * 0.08
            
            # Vertical displacement on the side walls (fissures and shelves)
            z_noise = 0.0
            if 0.0 < v_frac < 1.0:
                z_noise = (math.sin(5 * theta + v_frac * 8.0) * 0.06 + 
                           math.cos(9 * theta) * 0.04 + 
                           (random.random() - 0.5) * 0.05)
                # Ensure z stays strictly within [0.005, 1.495] before scaling
                z_val = max(0.01, min(1.49, z_base + z_noise))
            elif v_frac == 0.0:
                z_val = 0.0
            else:
                z_val = 1.50
                
            r_total = r_profile + (n1 + n2 + n3 + block_noise) * (1.0 - 0.2 * v_frac)
            # Add micro-irregularity
            r_total += (random.random() - 0.5) * 0.06
            
            x = r_total * math.cos(theta)
            y = r_total * math.sin(theta)
            
            v = bm.verts.new((x, y, z_val))
            ring.append(v)
            
        rings.append(ring)
        
    # Connect side quads
    for h_idx in range(n_height):
        r_curr = rings[h_idx]
        r_next = rings[h_idx + 1]
        for r_idx in range(n_radial):
            r_idx_next = (r_idx + 1) % n_radial
            bm.faces.new([r_curr[r_idx], r_curr[r_idx_next], r_next[r_idx_next], r_next[r_idx]])
            
    # Bottom face: cap flat at z=0
    bot_center = bm.verts.new((0.0, 0.0, 0.0))
    for r_idx in range(n_radial):
        r_idx_next = (r_idx + 1) % n_radial
        bm.faces.new([rings[0][r_idx_next], rings[0][r_idx], bot_center])
        
    # Top face: create concentric rings to make irregular rocky top shelf around the center seating
    top_ring = rings[-1]
    top_inner_rings = []
    
    for tier, r_ratio in enumerate([0.75, 0.50, 0.25]):
        inner_ring = []
        for r_idx in range(n_radial):
            theta = 2.0 * math.pi * r_idx / n_radial
            outer_v = top_ring[r_idx]
            # Interpolate towards center
            base_x = outer_v.co.x * r_ratio
            base_y = outer_v.co.y * r_ratio
            # Top shelf rocky relief
            z_top = 1.50
            # Keep flat where plinth sits (radius < 1.75), but outer terrace can have rocky bumps
            r_dist = math.hypot(base_x, base_y)
            if r_dist > 1.72:
                z_top += math.sin(6 * theta) * 0.03 - 0.02
            inner_ring.append(bm.verts.new((base_x, base_y, z_top)))
        top_inner_rings.append(inner_ring)
        
    # Bridge top outer ring to first inner ring
    prev_top = top_ring
    for inner_ring in top_inner_rings:
        for r_idx in range(n_radial):
            r_idx_next = (r_idx + 1) % n_radial
            bm.faces.new([prev_top[r_idx], prev_top[r_idx_next], inner_ring[r_idx_next], inner_ring[r_idx]])
        prev_top = inner_ring
        
    top_center = bm.verts.new((0.0, 0.0, 1.50))
    for r_idx in range(n_radial):
        r_idx_next = (r_idx + 1) % n_radial
        bm.faces.new([prev_top[r_idx], prev_top[r_idx_next], top_center])
        
    # Add random rock facets by subdividing and adding rocky displacement
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    # Normalize exact extents
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)
    
    scale_x = 5.800 / (max_x - min_x)
    scale_y = 5.800 / (max_y - min_y)
    scale_z = 1.500 / (max_z - min_z)
    
    mid_x = (min_x + max_x) / 2.0
    mid_y = (min_y + max_y) / 2.0
    
    for v in bm.verts:
        v.co.x = (v.co.x - mid_x) * scale_x
        v.co.y = (v.co.y - mid_y) * scale_y
        v.co.z = (v.co.z - min_z) * scale_z
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("RockBase")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("RockBase", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Apply a Displace modifier with procedural voronoi/clouds texture for rugged natural rock look
    # or keep procedural rocky geometry baked into mesh with smooth/faceted look
    # In Blender headless, we can add a subdivision and displace modifier or use geometry
    
    # Set flat shading for faceted rock outcropping look
    for f in obj.data.polygons:
        f.use_smooth = False
        
    # Material: dark weathered slate grey granite rock with wet shoreline base
    mat = make_material("RockBaseGranite", (0.16, 0.17, 0.19), roughness=0.92, metallic=0.02)
    obj.data.materials.append(mat)
    
    return obj
