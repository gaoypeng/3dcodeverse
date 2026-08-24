"""Saddle — contoured seat for the rider (part module; imported by src/model.py).

Ergonomic dished seat plate (20 mm thick) with raised cantle at rear (+Y) and soft perimeter bevels, contoured to fit the torso cylinder.
Material: burnished leather, saddle tan brown. Instances: 1.
Plan bbox: center (0.000, 0.020, 0.455) extents (0.160, 0.200, 0.040)
  x in [-0.080, 0.080]  y in [-0.080, 0.120]  z in [0.435, 0.475]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

def make_material(name, rgb, roughness=0.55, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_saddle():
    bm = bmesh.new()
    
    # Saddle extents:
    # X in [-0.080, 0.080] (width 0.160)
    # Y in [-0.080, 0.120] (length 0.200)
    # Z in [0.435, 0.475] (height 0.040)
    
    # HorseBody cylinder top reaches z ≈ 0.445 at x = 0, and slopes down with sqrt(0.07^2 - x^2) + 0.375.
    # At x = 0: top is 0.445. At x = ±0.07: top is 0.375.
    # Saddle bottom surface should rest flush on the HorseBody (z_bot ~ body top - 0.001 m overlap for welding)
    
    nx, ny = 16, 20
    
    # Generate bottom vertices conforming to the HorseBody top curve, and top vertices forming the saddle seat
    top_v = []
    bot_v = []
    
    for iy in range(ny + 1):
        ty = iy / ny  # 0 to 1
        y = -0.080 + ty * 0.200
        
        # Cantle curve: rises at the back (ty > 0.5)
        z_cantle = 0.012 * max(0.0, (ty - 0.4) / 0.6) ** 2
        # Slight pommel rise at front (ty < 0.3)
        z_pommel = 0.004 * max(0.0, (0.3 - ty) / 0.3) ** 2
        
        top_row = []
        bot_row = []
        
        for ix in range(nx + 1):
            tx = ix / nx  # 0 to 1
            x = -0.080 + tx * 0.160
            
            # Torso surface height at this x:
            # Torso is centered at z=0.375 with radius ~ 0.070
            if abs(x) <= 0.0699:
                z_torso = 0.375 + math.sqrt(max(0.0, 0.070**2 - x**2))
            else:
                # Flaps extending down past the cylinder equators slightly
                z_torso = 0.375 - 0.02 * (abs(x) - 0.0699) / 0.0101
            
            # Bottom of saddle touches/overlaps horse body
            # Overlap by ~1.5mm into the body for clean contact welding
            z_bottom = z_torso - 0.0015
            
            # Ensure z_bottom stays within plausible bbox
            z_bottom = max(0.435, min(z_bottom, 0.445))
            
            # Top surface:
            # At center x=0: z_top around 0.463 + cantle/pommel
            # At edges x=±0.08: drops to follow contour
            z_top = z_bottom + 0.018 + z_cantle + z_pommel
            # Clamp top to stay within 0.475 bbox height
            z_top = min(0.475, max(z_bottom + 0.006, z_top))
            
            v_t = bm.verts.new((x, y, z_top))
            v_b = bm.verts.new((x, y, z_bottom))
            top_row.append(v_t)
            bot_row.append(v_b)
            
        top_v.append(top_row)
        bot_v.append(bot_row)
        
    bm.verts.ensure_lookup_table()
    
    # Create quad faces
    for iy in range(ny):
        for ix in range(nx):
            # Top face (facing +Z)
            bm.faces.new([top_v[iy][ix], top_v[iy][ix+1], top_v[iy+1][ix+1], top_v[iy+1][ix]])
            # Bottom face (facing -Z)
            bm.faces.new([bot_v[iy][ix], bot_v[iy+1][ix], bot_v[iy+1][ix+1], bot_v[iy][ix+1]])
            
    # Skirt / side faces
    # Front edge (iy = 0)
    for ix in range(nx):
        bm.faces.new([top_v[0][ix], bot_v[0][ix], bot_v[0][ix+1], top_v[0][ix+1]])
    # Back edge (iy = ny)
    for ix in range(nx):
        bm.faces.new([top_v[ny][ix], top_v[ny][ix+1], bot_v[ny][ix+1], bot_v[ny][ix]])
    # Left edge (ix = 0)
    for iy in range(ny):
        bm.faces.new([top_v[iy][0], top_v[iy+1][0], bot_v[iy+1][0], bot_v[iy][0]])
    # Right edge (ix = nx)
    for iy in range(ny):
        bm.faces.new([top_v[iy][nx], bot_v[iy][nx], bot_v[iy+1][nx], top_v[iy+1][nx]])
        
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    me = bpy.data.meshes.new("Saddle")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("Saddle", me)
    bpy.context.scene.collection.objects.link(obj)
    mat = make_material("SaddleLeather", (0.38, 0.18, 0.08), roughness=0.6, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
