"""LanternRoom — faceted glass-enclosed beacon chamber.
12-sided polygonal glass lantern room, diameter 1.85 m, resting on GalleryDeck at z=10.098 m to 11.95 m.
BBox: center (0.000, 0.000, 11.024) extents (1.850, 1.850, 1.852)
"""
import math
import bpy
import bmesh
from mathutils import Vector

def make_material(name, rgb, roughness=0.1, metallic=0.0, alpha=1.0, transmission=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if transmission > 0:
        bsdf.inputs["Transmission Weight"].default_value = transmission
        if "Alpha" in bsdf.inputs:
            bsdf.inputs["Alpha"].default_value = alpha
        mat.blend_method = 'BLEND'
    return mat

def build_lantern_room() -> bpy.types.Object:
    bm = bmesh.new()
    sides = 12
    R = 0.925
    z_min = 10.098   # 2 mm overlap into GalleryDeck (top at 10.10) for clean weld without interpenetration
    z_sill = 10.35
    z_cornice = 11.85
    z_max = 11.95
    
    def make_ring_verts(z_val, r_val):
        return [bm.verts.new((r_val * math.cos(2 * math.pi * i / sides),
                              r_val * math.sin(2 * math.pi * i / sides),
                              z_val)) for i in range(sides)]
                              
    v_sill_bot = make_ring_verts(z_min, R)
    v_sill_top = make_ring_verts(z_sill, R)
    v_corn_bot = make_ring_verts(z_cornice, R)
    v_corn_top = make_ring_verts(z_max, R)
    
    # Metal Sill faces (Mat 0)
    for i in range(sides):
        next_i = (i + 1) % sides
        f = bm.faces.new([v_sill_bot[i], v_sill_bot[next_i], v_sill_top[next_i], v_sill_top[i]])
        f.material_index = 0
        
    # Metal Cornice faces (Mat 0)
    for i in range(sides):
        next_i = (i + 1) % sides
        f = bm.faces.new([v_corn_bot[i], v_corn_bot[next_i], v_corn_top[next_i], v_corn_top[i]])
        f.material_index = 0
        
    # Hollow walls (wall thickness = 0.05m):
    r_inner = R - 0.05
    v_sill_bot_in = make_ring_verts(z_min, r_inner)
    v_sill_top_in = make_ring_verts(z_sill, r_inner)
    v_corn_bot_in = make_ring_verts(z_cornice, r_inner)
    v_corn_top_in = make_ring_verts(z_max, r_inner)
    
    # Bottom rim connecting outer to inner at z_min:
    for i in range(sides):
        next_i = (i + 1) % sides
        f = bm.faces.new([v_sill_bot[i], v_sill_bot[next_i], v_sill_bot_in[next_i], v_sill_bot_in[i]])
        f.material_index = 0
        
    # Inner sill wall:
    for i in range(sides):
        next_i = (i + 1) % sides
        f = bm.faces.new([v_sill_bot_in[next_i], v_sill_bot_in[i], v_sill_top_in[i], v_sill_top_in[next_i]])
        f.material_index = 0

    # Top rim connecting outer to inner at z_max:
    for i in range(sides):
        next_i = (i + 1) % sides
        f = bm.faces.new([v_corn_top_in[i], v_corn_top_in[next_i], v_corn_top[next_i], v_corn_top[i]])
        f.material_index = 0
        
    # Inner cornice wall:
    for i in range(sides):
        next_i = (i + 1) % sides
        f = bm.faces.new([v_corn_top_in[next_i], v_corn_top_in[i], v_corn_bot_in[i], v_corn_bot_in[next_i]])
        f.material_index = 0

    # Glass Panes (Mat 1) from v_sill_top to v_corn_bot (inset r=0.98*R)
    v_glass_bot = make_ring_verts(z_sill, R * 0.98)
    v_glass_top = make_ring_verts(z_cornice, R * 0.98)
    for i in range(sides):
        next_i = (i + 1) % sides
        f = bm.faces.new([v_glass_bot[i], v_glass_bot[next_i], v_glass_top[next_i], v_glass_top[i]])
        f.material_index = 1
        
    # Mullion ribs (Mat 0) at each 12 corners
    mul_w = 0.025
    for i in range(sides):
        ang = 2 * math.pi * i / sides
        p1 = (R * math.cos(ang) - mul_w * math.sin(ang), R * math.sin(ang) + mul_w * math.cos(ang), z_sill)
        p2 = (R * math.cos(ang) + mul_w * math.sin(ang), R * math.sin(ang) - mul_w * math.cos(ang), z_sill)
        p3 = (R * math.cos(ang) + mul_w * math.sin(ang), R * math.sin(ang) - mul_w * math.cos(ang), z_cornice)
        p4 = (R * math.cos(ang) - mul_w * math.sin(ang), R * math.sin(ang) + mul_w * math.cos(ang), z_cornice)
        
        vm1 = bm.verts.new(p1)
        vm2 = bm.verts.new(p2)
        vm3 = bm.verts.new(p3)
        vm4 = bm.verts.new(p4)
        
        f = bm.faces.new([vm1, vm2, vm3, vm4])
        f.material_index = 0

    # Ensure exact extents 1.850 x 1.850 in XY
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    
    scale_x = 1.850 / (max_x - min_x)
    scale_y = 1.850 / (max_y - min_y)
    
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.y *= scale_y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("LanternRoom")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("LanternRoom", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_metal = make_material("LanternFrame", (0.10, 0.10, 0.12), roughness=0.3, metallic=0.85)
    mat_glass = make_material("LanternGlass", (0.85, 0.92, 0.98), roughness=0.05, metallic=0.0, alpha=0.35, transmission=0.9)
    
    obj.data.materials.append(mat_metal) # 0
    obj.data.materials.append(mat_glass) # 1
    
    return obj
