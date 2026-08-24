"""ClawBase — swept transition block from eye to rear claw.

Forged steel spine sweeping backward (+Y) and curving downward (-Z), initiating the arc of the nail-pulling claw.
Material: forged carbon steel.  Instances: 1.  Attaches to: HammerEye.
Plan bbox: center (0.000, 0.032, 0.300) extents (0.030, 0.035, 0.040)
  x in [-0.015, 0.015]  y in [0.0145, 0.0495]  z in [0.280, 0.320]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

CLAW_BASE_CENTER = (0.000, 0.032, 0.300)
CLAW_BASE_EXTENTS = (0.030, 0.035, 0.040)

def make_material(name, rgb, roughness=0.35, metallic=0.92):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_claw_base():
    """Build swept transitional spine between hammer eye and claw prongs."""
    bm = bmesh.new()
    
    # ClawBase sweeps from y = 0.0145 to y = 0.0495
    # As y increases, z curves downward from 0.305 down towards 0.285
    # Width (x) narrows slightly or maintains ~0.030 span
    
    # Sections along Y: (y, z_center, half_width_x, half_height_z)
    sections_def = [
        (0.0145, 0.305, 0.0150, 0.0150), # matches HammerEye rear face
        (0.0250, 0.304, 0.0145, 0.0140),
        (0.0380, 0.298, 0.0140, 0.0130),
        (0.0495, 0.288, 0.0135, 0.0120), # transition to claw prongs
    ]
    
    n_pts = 16
    rings = []
    for y, zc, hx, hz in sections_def:
        # Rounded octagonal cross section
        r_verts = []
        for i in range(n_pts):
            ang = 2.0 * math.pi * i / n_pts
            # superellipse / rounded box
            cos_a = math.cos(ang)
            sin_a = math.sin(ang)
            # sign-preserving power for squircle
            x = hx * math.copysign(abs(cos_a)**0.8, cos_a)
            z = zc + hz * math.copysign(abs(sin_a)**0.8, sin_a)
            r_verts.append(bm.verts.new((x, y, z)))
        rings.append(r_verts)
        
    bm.faces.new(reversed(rings[0]))
    for r in range(len(rings) - 1):
        r1 = rings[r]
        r2 = rings[r + 1]
        for i in range(n_pts):
            next_i = (i + 1) % n_pts
            bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
    bm.faces.new(rings[-1])
    
    bm.normal_update()
    me = bpy.data.meshes.new("ClawBase")
    bm.to_mesh(me)
    bm.free()
    
    for poly in me.polygons:
        poly.use_smooth = True
        
    obj = bpy.data.objects.new("ClawBase", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("ClawBaseSteel", (0.55, 0.57, 0.60), roughness=0.35, metallic=0.92)
    obj.data.materials.append(mat)
    
    return obj
