"""GalleryDeck — cantilevered observation walkway and corbel ring.
Flared circular platform widening to diameter 2.70 m at z=9.70 m to 10.10 m,
supported from below by 12 radial decorative stone corbel brackets.
BBox: center (0.000, 0.000, 9.900) extents (2.700, 2.700, 0.400)
"""
import math
import bpy
import bmesh
from mathutils import Vector

GALLERY_DECK_CENTER = (0.000, 0.000, 9.900)
GALLERY_DECK_EXTENTS = (2.700, 2.700, 0.400)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_gallery_deck() -> bpy.types.Object:
    bm = bmesh.new()
    segments = 48
    
    # Deck profile from z = 9.70 to z = 10.10:
    # Outer profile:
    # At z=9.70: radius = 1.05 (matches tower shaft top radius 1.05)
    # At z=9.85: corbel flared outward, radius = 1.22
    # At z=10.00: main cantilever cornice, radius = 1.35 (diameter 2.70)
    # At z=10.10: walkway deck lip, radius = 1.35
    
    profile = [
        (1.05, 9.70),
        (1.22, 9.85),
        (1.35, 10.00),
        (1.35, 10.10)
    ]
    
    rings = []
    for r, z in profile:
        ring = []
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    for l_idx in range(len(rings) - 1):
        r1 = rings[l_idx]
        r2 = rings[l_idx + 1]
        for i in range(segments):
            next_i = (i + 1) % segments
            bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            
    # Bottom cap (at 9.70, r=1.05) - hollow collar to avoid penetrating TowerShaft at z in [9.70, 9.80]
    # TowerShaft top is at 9.80 (radius 1.05).
    # Inner cylinder from z=9.70 to z=9.80 at radius r_inner = 1.051
    # At z=9.70: ring connecting outer ring 0 (r=1.05) and inner ring (r=1.051) is essentially a seam.
    # At z=9.80: inner cap closing the hole.
    inner_ring = []
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        x = 1.051 * math.cos(angle)
        y = 1.051 * math.sin(angle)
        inner_ring.append(bm.verts.new((x, y, 9.799)))
        
    for i in range(segments):
        next_i = (i + 1) % segments
        # Connect bottom outer ring to inner ring at 9.799
        bm.faces.new([rings[0][i], rings[0][next_i], inner_ring[next_i], inner_ring[i]])
        
    # Cap the inner hole at z=9.799 (just 1 mm below TowerShaft top at 9.80 for 1 mm weld contact)
    inner_center = bm.verts.new((0, 0, 9.799))
    for i in range(segments):
        next_i = (i + 1) % segments
        bm.faces.new([inner_ring[next_i], inner_ring[i], inner_center])
        
    # Top deck surface (at 10.10, r=1.35)
    top_center = bm.verts.new((0, 0, 10.10))
    for i in range(segments):
        next_i = (i + 1) % segments
        bm.faces.new([rings[-1][i], rings[-1][next_i], top_center])
        
    # 12 Corbel brackets around the underside
    n_corbels = 12
    for c in range(n_corbels):
        ang = 2 * math.pi * c / n_corbels
        cos_a = math.cos(ang)
        sin_a = math.sin(ang)
        # Tangent vector
        tx = -sin_a * 0.04
        ty = cos_a * 0.04
        
        # Corbel profile: r from 1.05 to 1.28, z from 9.71 to 9.98
        p1 = (1.05 * cos_a - tx, 1.05 * sin_a - ty, 9.71)
        p2 = (1.05 * cos_a + tx, 1.05 * sin_a + ty, 9.71)
        p3 = (1.28 * cos_a + tx, 1.28 * sin_a + ty, 9.98)
        p4 = (1.28 * cos_a - tx, 1.28 * sin_a - ty, 9.98)
        
        p5 = (1.05 * cos_a - tx, 1.05 * sin_a - ty, 9.98)
        p6 = (1.05 * cos_a + tx, 1.05 * sin_a + ty, 9.98)
        
        v1 = bm.verts.new(p1)
        v2 = bm.verts.new(p2)
        v3 = bm.verts.new(p3)
        v4 = bm.verts.new(p4)
        v5 = bm.verts.new(p5)
        v6 = bm.verts.new(p6)
        
        # Faces of wedge
        bm.faces.new([v1, v2, v3, v4]) # Sloped underside
        bm.faces.new([v5, v6, v2, v1]) # Back wall
        bm.faces.new([v6, v5, v4, v3]) # Top wall
        bm.faces.new([v1, v4, v5])     # Side 1
        bm.faces.new([v2, v6, v3])     # Side 2

    # Ensure exact extents 2.700 x 2.700 x 0.400
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    scale_x = 2.700 / (max_x - min_x)
    scale_y = 2.700 / (max_y - min_y)
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.y *= scale_y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("GalleryDeck")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("GalleryDeck", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("GalleryStone", (0.90, 0.90, 0.92), roughness=0.4, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj

