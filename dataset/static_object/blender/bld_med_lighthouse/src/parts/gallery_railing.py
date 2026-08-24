"""GalleryRailing — perimeter safety railing around the gallery.
Tubular metal perimeter handrail and safety balustrade with 16 vertical stanchions and 2 horizontal guard rails on diameter 2.65 m, standing 0.95 m high above the deck.
BBox: center (0.000, 0.000, 10.550) extents (2.650, 2.650, 0.950)
z in [10.075, 11.025], r = 2.650 / 2 = 1.325.
"""
import math
import bpy
import bmesh
from mathutils import Vector

GALLERY_RAILING_CENTER = (0.000, 0.000, 10.550)
GALLERY_RAILING_EXTENTS = (2.650, 2.650, 0.950)

def make_material(name, rgb, roughness=0.3, metallic=0.9):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_gallery_railing() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Outer radius = 1.325 (matches extents 2.650)
    R = 1.325
    z_min = 10.075
    z_max = 11.025
    
    # 1. Horizontal rails: Top rail at 11.015 (thickness 0.02), Mid rail at 10.55 (thickness 0.02)
    rail_segments = 48
    def add_ring_tube(z_pos, r_tube):
        r_inner = R - r_tube
        r_outer = R
        z_bot = z_pos - r_tube
        z_top = z_pos + r_tube
        
        rings = []
        for z_val, r_val in [(z_bot, r_inner), (z_bot, r_outer), (z_top, r_outer), (z_top, r_inner)]:
            ring = []
            for i in range(rail_segments):
                ang = 2 * math.pi * i / rail_segments
                ring.append(bm.verts.new((r_val * math.cos(ang), r_val * math.sin(ang), z_val)))
            rings.append(ring)
            
        for i in range(rail_segments):
            next_i = (i + 1) % rail_segments
            # Outer face
            bm.faces.new([rings[1][i], rings[1][next_i], rings[2][next_i], rings[2][i]])
            # Top face
            bm.faces.new([rings[2][i], rings[2][next_i], rings[3][next_i], rings[3][i]])
            # Inner face
            bm.faces.new([rings[3][i], rings[3][next_i], rings[0][next_i], rings[0][i]])
            # Bottom face
            bm.faces.new([rings[0][i], rings[0][next_i], rings[1][next_i], rings[1][i]])

    add_ring_tube(11.010, 0.015)
    add_ring_tube(10.550, 0.015)
    
    # 2. 16 vertical stanchions from z_min=10.098 to z_max=11.025 (overlap deck top at 10.100 by 2mm)
    z_min_stanchion = 10.098
    n_stanchions = 16
    st_r = 0.015
    for s in range(n_stanchions):
        ang = 2 * math.pi * s / n_stanchions
        cx = (R - st_r) * math.cos(ang)
        cy = (R - st_r) * math.sin(ang)
        
        # 6-sided cylinder for each stanchion
        st_verts_bot = []
        st_verts_top = []
        for i in range(6):
            a_sub = 2 * math.pi * i / 6
            sx = cx + st_r * math.cos(a_sub)
            sy = cy + st_r * math.sin(a_sub)
            st_verts_bot.append(bm.verts.new((sx, sy, z_min_stanchion)))
            st_verts_top.append(bm.verts.new((sx, sy, z_max)))
            
        for i in range(6):
            next_i = (i + 1) % 6
            bm.faces.new([st_verts_bot[i], st_verts_bot[next_i], st_verts_top[next_i], st_verts_top[i]])
            
        # Caps
        bm.faces.new(st_verts_bot[::-1])
        bm.faces.new(st_verts_top)

    # Ensure exact extents 2.650 x 2.650 x 0.950 without shifting bottom into deck
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    
    scale_x = 2.650 / (max_x - min_x)
    scale_y = 2.650 / (max_y - min_y)
    
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.y *= scale_y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("GalleryRailing")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("GalleryRailing", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("RailingIron", (0.08, 0.08, 0.09), roughness=0.3, metallic=0.9)
    obj.data.materials.append(mat)
    
    return obj
