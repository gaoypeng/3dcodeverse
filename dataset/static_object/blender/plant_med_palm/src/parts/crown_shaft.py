"""CrownShaft — green emergence point of frond stems (part module; imported by src/model.py).

Smooth tapered green sheath cluster (diameter 0.045 m tapering to 0.03 m, height 0.06 m) capping the trunk where fronds diverge.
Material: smooth fresh crownshaft green, semi-gloss. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector

random.seed(0)

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_crown_shaft() -> bpy.types.Object:
    """Build green crownshaft sheath capping the palm trunk."""
    bm = bmesh.new()
    segments = 32
    n_rings = 10
    z_min, z_max = 0.330, 0.390
    
    rings = []
    for step in range(n_rings):
        t = step / (n_rings - 1)
        z = z_min + t * (z_max - z_min)
        
        # Diameter 0.045 (r=0.0225) at bottom tapering to 0.030 (r=0.015) at top
        r = 0.0225 * (1.0 - t) + 0.0150 * t
        
        # Subtle vertical sheath creases (overlapping leaf bases)
        ring = []
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            flute = 0.0012 * math.cos(angle * 4.0)
            r_fluted = r + flute
            x = r_fluted * math.cos(angle)
            y = r_fluted * math.sin(angle)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    # Cap bottom
    v_bot = bm.verts.new((0, 0, z_min))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((v_bot, rings[0][i_next], rings[0][i]))
        
    # Connect rings
    for r_idx in range(n_rings - 1):
        r1 = rings[r_idx]
        r2 = rings[r_idx + 1]
        for i in range(segments):
            i_next = (i + 1) % segments
            bm.faces.new((r1[i], r1[i_next], r2[i_next], r2[i]))
            
    # Cap top
    v_top = bm.verts.new((0, 0, z_max))
    for i in range(segments):
        i_next = (i + 1) % segments
        bm.faces.new((v_top, rings[-1][i], rings[-1][i_next]))
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("CrownShaft")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("CrownShaft", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Fresh semi-gloss plant green
    mat = make_material("CrownShaftMat", (0.22, 0.48, 0.12), roughness=0.35, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj
