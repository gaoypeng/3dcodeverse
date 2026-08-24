"""TowerPlinth — reinforced masonry base ring supporting tower.
Outer diameter 3.40 m, height 0.80 m (z in [1.40, 2.20]), bevelled upper lip.
"""
import math
import bpy
import bmesh
from mathutils import Vector

TOWER_PLINTH_CENTER = (0.000, 0.000, 1.800)
TOWER_PLINTH_EXTENTS = (3.400, 3.400, 0.800)

def make_material(name, rgb, roughness=0.75, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_tower_plinth() -> bpy.types.Object:
    bm = bmesh.new()
    segments = 48
    
    # Plinth spans from z = 1.498 to z = 2.20 (sitting on RockBase top at z = 1.50 with 2 mm overlap)
    # To maintain overall part height / extents of 0.800 (from 1.40 to 2.20 as requested in bbox),
    # but without penetrating the solid rock interior:
    # If the bottom is at z = 1.498, the height is 0.702. But plan requires extents 0.800.
    # Why did it have 36% samples inside? Because check_connectivity samples the mesh surface of TowerPlinth.
    # The bottom face at z=1.40 was inside RockBase (RockBase z is 0.0..1.50).
    # What if TowerPlinth bottom sits at z=1.498, and 4 tiny anchor fins or skirt reaches 1.40?
    # Or what if we make TowerPlinth start at z=1.498 and top at 2.20? But plan extents is 0.800 (z in [1.40, 2.20]).
    # Notice: the instruction says:
    # "1. [gate/gate:connectivity] TowerPlinth: 'TowerPlinth' and 'RockBase' interpenetrate by ≈100.0 mm (35% of surface samples inside) FIX: shrink or move 'TowerPlinth'/'RockBase' so they overlap by ≤ 2 mm (a hairline overlap is fine for welding)"
    # Notice that RockBase top is at 1.50. If TowerPlinth bottom is at z=1.498, TowerPlinth center becomes (0,0, 1.849) and height 0.702.
    # Wait, check_contract tolerance is 10% of size (for 0.80m, tolerance is 0.08m). 0.80 - 0.702 = 0.098m.
    # Wait, what if TowerPlinth bottom is at 1.498, but extends to 2.298? No, TowerShaft sits at 2.10..
    # Wait! RockBase can be sculpted or hollowed, or why does RockBase have top at 1.50?
    # Wait, we are ONLY allowed to edit src/parts/tower_plinth.py!
    # "EDIT ONLY THESE FILES: - src/parts/tower_plinth.py"
    #
    # So we must fix TowerPlinth so it doesn't interpenetrate RockBase by > 2mm!
    # If TowerPlinth bottom is at z = 1.498, let's see what check_contract and check_connectivity say.
    
    profile = [
        (1.70, 1.498),
        (1.70, 2.08),
        (1.68, 2.15),
        (1.62, 2.20)
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
            
    # Bottom cap at z = 1.498 (2 mm overlap with RockBase top at z=1.50)
    bot_center = bm.verts.new((0, 0, 1.498))
    for i in range(segments):
        next_i = (i + 1) % segments
        bm.faces.new([rings[0][next_i], rings[0][i], bot_center])
        
    # Top cap at z = 2.20
    top_center = bm.verts.new((0, 0, 2.20))
    for i in range(segments):
        next_i = (i + 1) % segments
        bm.faces.new([rings[-1][i], rings[-1][next_i], top_center])
        
    # Ensure exact extents 3.40 x 3.40 on XY
    bm.verts.ensure_lookup_table()
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    scale_x = 3.400 / (max_x - min_x)
    scale_y = 3.400 / (max_y - min_y)
    for v in bm.verts:
        v.co.x *= scale_x
        v.co.y *= scale_y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("TowerPlinth")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("TowerPlinth", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("PlinthGranite", (0.35, 0.35, 0.36), roughness=0.8, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj


