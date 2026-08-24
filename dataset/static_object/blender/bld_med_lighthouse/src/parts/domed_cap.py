"""DomedCap — weatherproof dome roof and lightning finial.
Hemispherical copper dome roof curving upward from diameter 1.90 m at z=11.90 m,
topped with a spherical ventilation cowl and vertical spire/lightning rod extending to z=13.70 m.
BBox: center (0.000, 0.000, 12.800) extents (1.900, 1.900, 1.800)
z in [11.90, 13.70], radius = 0.95.
"""
import math
import bpy
import bmesh
from mathutils import Vector

DOMED_CAP_CENTER = (0.000, 0.000, 12.800)
DOMED_CAP_EXTENTS = (1.900, 1.900, 1.800)

def make_material(name, rgb, roughness=0.5, metallic=0.7):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_domed_cap() -> bpy.types.Object:
    bm = bmesh.new()
    segments = 48
    
    # 1. Copper Eaves / Lip at z = 11.948 to 11.98 (radius = 0.95)
    # 2. Dome from z = 11.98 to 12.80 (curving inward to r = 0.15)
    # 3. Ventilation Cowl / Ball at z = 12.80 to 13.00 (sphere r = 0.18)
    # 4. Spire / Lightning Rod from z = 13.00 to 13.70 (thin cone/rod)
    
    # Profile for eaves + dome + cowl + spire:
    n_dome_steps = 16
    dome_profile = []
    
    # Eaves rim (dia 1.90m -> r=0.95m)
    dome_profile.append((0.95, 11.948))
    dome_profile.append((0.95, 11.98))
    
    # Dome hemisphere arc
    # arc from angle theta=0 to theta=pi/2
    for i in range(1, n_dome_steps):
        theta = (math.pi / 2) * (i / n_dome_steps)
        # r = 0.95 * cos(theta), z = 11.98 + 0.80 * sin(theta)
        r = 0.95 * math.cos(theta)
        z = 11.98 + 0.82 * math.sin(theta)
        dome_profile.append((r, z))
        
    # Cowl base collar
    dome_profile.append((0.20, 12.80))
    # Cowl ball
    n_ball = 8
    for i in range(n_ball + 1):
        ang = -math.pi/2 + math.pi * (i / n_ball)
        r_ball = 0.16 * math.cos(ang) + 0.05
        z_ball = 12.92 + 0.12 * math.sin(ang)
        dome_profile.append((r_ball, z_ball))
        
    # Spire rod base
    dome_profile.append((0.04, 13.04))
    dome_profile.append((0.025, 13.50))
    # Lightning tip
    dome_profile.append((0.002, 13.70))
    
    rings = []
    for r, z in dome_profile:
        ring = []
        for i in range(segments):
            ang = 2 * math.pi * i / segments
            ring.append(bm.verts.new((r * math.cos(ang), r * math.sin(ang), z)))
        rings.append(ring)
        
    for l_idx in range(len(rings) - 1):
        r1 = rings[l_idx]
        r2 = rings[l_idx + 1]
        for i in range(segments):
            next_i = (i + 1) % segments
            bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            
    # Bottom cap (z=11.948)
    c_bot = bm.verts.new((0, 0, 11.948))
    for i in range(segments):
        next_i = (i + 1) % segments
        bm.faces.new([rings[0][next_i], rings[0][i], c_bot])
        
    # Top tip
    c_top = bm.verts.new((0, 0, 13.70))
    for i in range(segments):
        next_i = (i + 1) % segments
        bm.faces.new([rings[-1][i], rings[-1][next_i], c_top])

    # Ensure exact extents 1.900 x 1.900 x 1.800
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)
    
    scale_x = 1.900 / (max_x - min_x)
    scale_y = 1.900 / (max_y - min_y)
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.y *= scale_y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("DomedCap")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("DomedCap", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Weathered verdigris copper green
    mat_copper = make_material("VerdigrisCopper", (0.28, 0.52, 0.45), roughness=0.45, metallic=0.6)
    obj.data.materials.append(mat_copper)
    
    return obj
