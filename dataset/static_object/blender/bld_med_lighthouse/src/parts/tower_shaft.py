"""TowerShaft — main tapered tower with alternating painted bands.
Truncated conical cylinder tapering smoothly from bottom diameter 3.20 m at z=2.198 m
to diameter 2.10 m at z=9.80 m; finished with 5 crisp alternating painted horizontal bands
(3 white, 2 red). Overlaps TowerPlinth (top at z=2.200) by 2 mm.
"""
import math
import bpy
import bmesh
from mathutils import Vector

TOWER_SHAFT_CENTER = (0.000, 0.000, 5.950)
TOWER_SHAFT_EXTENTS = (3.200, 3.200, 7.700)

def make_material(name, rgb, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_tower_shaft() -> bpy.types.Object:
    bm = bmesh.new()
    segments = 48
    
    # Plinth top is at z=2.200. Overlap with Plinth by 2 mm -> z_min = 2.198
    # Deck bottom is at z=9.700. Shaft top is at z=9.800 (or overlap by 2 mm with deck if needed, but GalleryDeck has its own target).
    z_min = 2.198
    z_max = 9.800
    h = z_max - z_min
    
    def radius_at_z(z):
        # Linearly interpolate: bottom dia = 3.20 (r=1.60) at 2.10 -> at z_min ~1.586, top dia = 2.10 (r=1.05) at 9.80
        t = (z - 2.10) / 7.70
        return 1.60 * (1.0 - t) + 1.05 * t

    z_levels = [z_min + i * (h / 5.0) for i in range(6)]
    rings = []
    for z in z_levels:
        r = radius_at_z(z)
        ring = []
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    # Materials:
    # Mat 0: White band
    # Mat 1: Red band
    band_faces = [[] for _ in range(5)]
    for b in range(5):
        r1 = rings[b]
        r2 = rings[b + 1]
        mat_idx = 0 if (b % 2 == 0) else 1
        for i in range(segments):
            next_i = (i + 1) % segments
            f = bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            f.material_index = mat_idx
            band_faces[b].append(f)
            
    # Caps
    bot_center = bm.verts.new((0, 0, z_min))
    for i in range(segments):
        next_i = (i + 1) % segments
        f = bm.faces.new([rings[0][next_i], rings[0][i], bot_center])
        f.material_index = 0
        
    top_center = bm.verts.new((0, 0, z_max))
    for i in range(segments):
        next_i = (i + 1) % segments
        f = bm.faces.new([rings[-1][i], rings[-1][next_i], top_center])
        f.material_index = 0

    # Scale in X and Y to match 3.200 diameter at the base
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    scale_x = 3.200 / (max_x - min_x)
    scale_y = 3.200 / (max_y - min_y)
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.y *= scale_y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("TowerShaft")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("TowerShaft", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_white = make_material("TowerWhite", (0.92, 0.92, 0.94), roughness=0.35, metallic=0.0)
    mat_red = make_material("TowerRed", (0.85, 0.08, 0.08), roughness=0.35, metallic=0.0)
    
    obj.data.materials.append(mat_white) # Index 0
    obj.data.materials.append(mat_red)   # Index 1
    
    return obj
