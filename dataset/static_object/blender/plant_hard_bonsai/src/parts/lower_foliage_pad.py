"""LowerFoliagePad — lowest horizontal branch foliage cloud (part module; imported by src/model.py).

Flattened horizontal convex cluster (pad) of fine pine needle bunches extending towards the right (+X, -Y direction).
Material: dense deep emerald green pine needle clusters.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

LOWER_FOLIAGE_PAD_CENTER = (0.120, -0.060, 0.280)
LOWER_FOLIAGE_PAD_EXTENTS = (0.140, 0.120, 0.060)
# bounds: x in [0.050, 0.190], y in [-0.120, 0.000], z in [0.250, 0.310]

def make_material(name, rgb, roughness=0.88, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_foliage_puff(bm, center, size, sub_seed=0):
    """Create a flattened organic cloud puff with scalloped surface."""
    cx, cy, cz = center
    sx, sy, sz = size # semi-axes
    
    n_lat = 8
    n_lon = 16
    
    rings = []
    for i in range(n_lat + 1):
        v_frac = i / n_lat # 0 at bottom, 1 at top
        lat_angle = -math.pi * 0.5 + math.pi * v_frac
        
        # Flattened bottom (traditional bonsai pad is flat underneath, convex domed on top)
        if v_frac < 0.3:
            z_scale = math.sin(lat_angle) * 0.5
        else:
            z_scale = math.sin(lat_angle)
            
        r_xy = math.cos(lat_angle)
        
        ring = []
        for j in range(n_lon):
            lon_angle = 2.0 * math.pi * j / n_lon
            # Multi-frequency lobes for needle bunch silhouettes
            lobe = (
                0.12 * math.sin(4 * lon_angle + sub_seed) +
                0.08 * math.cos(6 * lon_angle + 1.5 * sub_seed)
            )
            r_eff = max(0.01, r_xy * (1.0 + lobe))
            
            vx = cx + sx * r_eff * math.cos(lon_angle)
            vy = cy + sy * r_eff * math.sin(lon_angle)
            vz = cz + sz * z_scale * (1.0 + 0.1 * math.sin(3 * lon_angle))
            
            # clamp to part bounds: [0.050, 0.190], [-0.120, 0.000], [0.250, 0.310]
            vx = max(0.050, min(0.190, vx))
            vy = max(-0.120, min(0.000, vy))
            vz = max(0.250, min(0.310, vz))
            ring.append(bm.verts.new((vx, vy, vz)))
        rings.append(ring)
        
    for i in range(n_lat):
        r1 = rings[i]
        r2 = rings[i+1]
        for j in range(n_lon):
            j_next = (j + 1) % n_lon
            bm.faces.new([r1[j], r1[j_next], r2[j_next], r2[j]])
            
    # Bottom pole
    v_bot = bm.verts.new((cx, cy, cz - sz * 0.5))
    for j in range(n_lon):
        j_next = (j + 1) % n_lon
        bm.faces.new([v_bot, rings[0][j_next], rings[0][j]])
        
    # Top pole
    v_top = bm.verts.new((cx, cy, cz + sz))
    for j in range(n_lon):
        j_next = (j + 1) % n_lon
        bm.faces.new([v_top, rings[-1][j], rings[-1][j_next]])

def build_lower_foliage_pad() -> bpy.types.Object:
    bm = bmesh.new()

    # Compose 3 overlapping rounded cloud puffs into a lush tiered foliage pad
    # Center: (0.120, -0.060, 0.280), extents: (0.140, 0.120, 0.060)
    # Main central cushion
    create_foliage_puff(bm, (0.120, -0.060, 0.280), (0.065, 0.055, 0.028), sub_seed=1.0)
    # Right-extended sub-cluster
    create_foliage_puff(bm, (0.145, -0.075, 0.278), (0.040, 0.038, 0.024), sub_seed=2.5)
    # Inward sub-cluster
    create_foliage_puff(bm, (0.090, -0.045, 0.282), (0.038, 0.035, 0.022), sub_seed=4.0)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("LowerFoliagePad")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("LowerFoliagePad", me)
    bpy.context.scene.collection.objects.link(obj)

    # Deep emerald green pine needle foliage material
    mat = make_material("LowerFoliagePadMat", (0.08, 0.24, 0.08), roughness=0.9, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
