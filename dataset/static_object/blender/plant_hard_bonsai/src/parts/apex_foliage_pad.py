"""ApexFoliagePad — top crown foliage canopy dome (part module; imported by src/model.py).

Gently rounded triangular-dome foliage crown crowning the top of the trunk apex.
Material: dense vibrant pine needles with lighter fresh tips.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

APEX_FOLIAGE_PAD_CENTER = (0.020, -0.010, 0.440)
APEX_FOLIAGE_PAD_EXTENTS = (0.150, 0.140, 0.080)
# bounds: x in [-0.055, 0.095], y in [-0.080, 0.060], z in [0.400, 0.480]

def make_material(name, rgb, roughness=0.88, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_foliage_puff(bm, center, size, sub_seed=0):
    """Create a rounded crown dome cloud puff with scalloped surface."""
    cx, cy, cz = center
    sx, sy, sz = size
    
    n_lat = 10
    n_lon = 20
    
    rings = []
    for i in range(n_lat + 1):
        v_frac = i / n_lat
        lat_angle = -math.pi * 0.5 + math.pi * v_frac
        
        # Flattened bottom, domed top
        if v_frac < 0.4:
            z_scale = math.sin(lat_angle) * 0.8
        else:
            z_scale = math.sin(lat_angle)
            
        r_xy = math.cos(lat_angle)
        
        ring = []
        for j in range(n_lon):
            lon_angle = 2.0 * math.pi * j / n_lon
            lobe = (
                0.12 * math.sin(5 * lon_angle + sub_seed) +
                0.08 * math.cos(3 * lon_angle + 1.2 * sub_seed)
            )
            r_eff = max(0.01, r_xy * (1.0 + lobe))
            
            vx = cx + sx * r_eff * math.cos(lon_angle)
            vy = cy + sy * r_eff * math.sin(lon_angle)
            vz = cz + sz * z_scale * (1.0 + 0.05 * math.sin(4 * lon_angle))
            
            # clamp to part bounds: [-0.055, 0.095], [-0.080, 0.060], [0.400, 0.480]
            vx = max(-0.055, min(0.095, vx))
            vy = max(-0.080, min(0.060, vy))
            vz = max(0.400, min(0.480, vz))
            ring.append(bm.verts.new((vx, vy, vz)))
        rings.append(ring)
        
    for i in range(n_lat):
        r1 = rings[i]
        r2 = rings[i+1]
        for j in range(n_lon):
            j_next = (j + 1) % n_lon
            bm.faces.new([r1[j], r1[j_next], r2[j_next], r2[j]])
            
    v_bot = bm.verts.new((cx, cy, max(0.400, cz - sz * 0.8)))
    for j in range(n_lon):
        j_next = (j + 1) % n_lon
        bm.faces.new([v_bot, rings[0][j_next], rings[0][j]])
        
    v_top = bm.verts.new((cx, cy, min(0.480, cz + sz)))
    for j in range(n_lon):
        j_next = (j + 1) % n_lon
        bm.faces.new([v_top, rings[-1][j], rings[-1][j_next]])

def build_apex_foliage_pad() -> bpy.types.Object:
    bm = bmesh.new()

    # Center is (0.020, -0.010, 0.440), extents (0.150, 0.140, 0.080) -> z in [0.400, 0.480]
    # Trunk apex extends up to z = 0.402
    create_foliage_puff(bm, (0.020, -0.010, 0.440), (0.072, 0.068, 0.040), sub_seed=2.1)
    create_foliage_puff(bm, (0.052, -0.032, 0.435), (0.040, 0.038, 0.032), sub_seed=4.4)
    create_foliage_puff(bm, (-0.012, 0.015, 0.435), (0.040, 0.038, 0.032), sub_seed=6.7)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("ApexFoliagePad")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("ApexFoliagePad", me)
    bpy.context.scene.collection.objects.link(obj)

    mat = make_material("ApexFoliagePadMat", (0.10, 0.28, 0.09), roughness=0.88, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
