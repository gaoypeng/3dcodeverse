"""ClawProng — curved tapered fork prong for pulling nails.

Curved wedge prong forming one half of the forked claw, tapering to a sharp chisel tip at the rear and forming a central V-groove nail slot with its mirrored twin.
Material: hardened forged steel, brushed and ground edge.  Instances: 2 (mirror_x).  Attaches to: ClawBase.
Plan bbox: center (0.010, 0.065, 0.280) extents (0.012, 0.045, 0.045)
  x in [0.004, 0.016]  y in [0.0425, 0.0875]  z in [0.2575, 0.3025]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

CLAW_PRONG_CENTER = (0.010, 0.065, 0.280)
CLAW_PRONG_EXTENTS = (0.012, 0.045, 0.045)

def make_material(name, rgb, roughness=0.25, metallic=0.95):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_prong_mesh(name, sign_x):
    """Build one curved, tapered claw prong."""
    bm = bmesh.new()
    
    # 4 profile cross-sections along the downward/backward curve
    # Each section is a trapezoid/quadrilateral forming the V-slot on the inside and curved claw on outside
    # y ranges from ~0.045 to 0.0875
    # z ranges from ~0.300 down to 0.258
    # x ranges from ~0.004 (inside slot edge) to 0.016 (outside cheek)
    
    # For positive prong (sign_x = +1):
    # section vertices order: [inner_top, outer_top, outer_bot, inner_bot]
    sections = [
        # y, z_top, z_bot, x_inner, x_outer
        (0.0440, 0.3020, 0.2760, 0.0040, 0.0160), # base joint at ClawBase
        (0.0580, 0.2940, 0.2670, 0.0045, 0.0155), # mid curve
        (0.0730, 0.2830, 0.2595, 0.0060, 0.0145), # narrowing toward tip
        (0.0875, 0.2680, 0.2575, 0.0080, 0.0130), # chisel tip at rear
    ]
    
    rings = []
    for y, z_top, z_bot, x_in, x_out in sections:
        # Apply sign_x
        xi = x_in * sign_x
        xo = x_out * sign_x
        
        # 4 vertices per cross section
        if sign_x > 0:
            v0 = bm.verts.new((xi, y, z_top)) # inner top
            v1 = bm.verts.new((xo, y, z_top)) # outer top
            v2 = bm.verts.new((xo, y, z_bot)) # outer bot
            v3 = bm.verts.new((xi, y, z_bot)) # inner bot
            rings.append([v0, v1, v2, v3])
        else:
            v0 = bm.verts.new((xi, y, z_top)) # inner top
            v1 = bm.verts.new((xi, y, z_bot)) # inner bot
            v2 = bm.verts.new((xo, y, z_bot)) # outer bot
            v3 = bm.verts.new((xo, y, z_top)) # outer top
            rings.append([v0, v1, v2, v3])
            
    # Front cap (facing ClawBase)
    bm.faces.new(reversed(rings[0]))
    
    # Loft through sections
    for r in range(len(rings) - 1):
        r1 = rings[r]
        r2 = rings[r + 1]
        for i in range(4):
            next_i = (i + 1) % 4
            bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            
    # Rear chisel tip cap
    bm.faces.new(rings[-1])
    
    bm.normal_update()
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    for poly in me.polygons:
        poly.use_smooth = True
        
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0008
    bev.segments = 2
    
    mat = make_material(f"{name}Mat", (0.70, 0.72, 0.75), roughness=0.25, metallic=0.96)
    obj.data.materials.append(mat)
    
    return obj

def build_claw_prong():
    """Build the two mirrored claw prongs forming the nail pulling fork."""
    prong_0 = create_prong_mesh("ClawProng_0", sign_x=1.0)
    prong_1 = create_prong_mesh("ClawProng_1", sign_x=-1.0)
    return [prong_0, prong_1]
