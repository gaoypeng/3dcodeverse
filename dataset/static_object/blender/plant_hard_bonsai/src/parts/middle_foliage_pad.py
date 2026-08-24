"""MiddleFoliagePad — middle counter-balancing foliage cloud (part module; imported by src/model.py).

Horizontal tiered foliage cushion positioned on the opposite side (-X, +Y direction) to balance the lower branch pad.
Material: dense deep emerald green pine needle clusters.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

MIDDLE_FOLIAGE_PAD_CENTER = (-0.110, 0.050, 0.350)
MIDDLE_FOLIAGE_PAD_EXTENTS = (0.130, 0.110, 0.060)
# bounds: x in [-0.175, -0.045], y in [-0.005, 0.105], z in [0.320, 0.380]

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
    sx, sy, sz = size
    
    n_lat = 8
    n_lon = 16
    
    rings = []
    for i in range(n_lat + 1):
        v_frac = i / n_lat
        lat_angle = -math.pi * 0.5 + math.pi * v_frac
        
        if v_frac < 0.3:
            z_scale = math.sin(lat_angle) * 0.5
        else:
            z_scale = math.sin(lat_angle)
            
        r_xy = math.cos(lat_angle)
        
        ring = []
        for j in range(n_lon):
            lon_angle = 2.0 * math.pi * j / n_lon
            lobe = (
                0.12 * math.sin(4 * lon_angle + sub_seed) +
                0.08 * math.cos(6 * lon_angle + 1.5 * sub_seed)
            )
            r_eff = max(0.01, r_xy * (1.0 + lobe))
            
            vx = cx + sx * r_eff * math.cos(lon_angle)
            vy = cy + sy * r_eff * math.sin(lon_angle)
            vz = cz + sz * z_scale * (1.0 + 0.1 * math.sin(3 * lon_angle))
            
            # clamp to part bounds: [-0.175, -0.045], [-0.005, 0.105], [0.320, 0.380]
            vx = max(-0.175, min(-0.045, vx))
            vy = max(-0.005, min(0.105, vy))
            vz = max(0.320, min(0.380, vz))
            ring.append(bm.verts.new((vx, vy, vz)))
        rings.append(ring)
        
    for i in range(n_lat):
        r1 = rings[i]
        r2 = rings[i+1]
        for j in range(n_lon):
            j_next = (j + 1) % n_lon
            bm.faces.new([r1[j], r1[j_next], r2[j_next], r2[j]])
            
    v_bot = bm.verts.new((cx, cy, cz - sz * 0.5))
    for j in range(n_lon):
        j_next = (j + 1) % n_lon
        bm.faces.new([v_bot, rings[0][j_next], rings[0][j]])
        
    v_top = bm.verts.new((cx, cy, cz + sz))
    for j in range(n_lon):
        j_next = (j + 1) % n_lon
        bm.faces.new([v_top, rings[-1][j], rings[-1][j_next]])

def build_middle_foliage_pad() -> bpy.types.Object:
    bm = bmesh.new()

    # Compose overlapping cloud puffs
    # Center: (-0.110, 0.050, 0.350), extents: (0.130, 0.110, 0.060)
    # Main central cushion
    create_foliage_puff(bm, (-0.110, 0.050, 0.350), (0.060, 0.050, 0.028), sub_seed=3.2)
    # Left-outer sub-cluster
    create_foliage_puff(bm, (-0.135, 0.060, 0.348), (0.036, 0.034, 0.022), sub_seed=5.1)
    # Inward sub-cluster
    create_foliage_puff(bm, (-0.080, 0.038, 0.352), (0.034, 0.032, 0.020), sub_seed=1.8)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("MiddleFoliagePad")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("MiddleFoliagePad", me)
    bpy.context.scene.collection.objects.link(obj)

    # Deep emerald green pine needle foliage material
    mat = make_material("MiddleFoliagePadMat", (0.08, 0.24, 0.08), roughness=0.9, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
