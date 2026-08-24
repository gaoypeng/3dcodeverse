"""WoodenHandle — cylindrical ergonomic wooden grip and shaft.

Turned hardwood shaft with round-to-oval profile (diameter 26 mm to 30 mm), flared butt at z=0, gentle ergonomic waist taper, and upper tenon fitted tightly through the steel hammer eye.
Material: natural clear-lacquered hickory wood with visible longitudinal grain.  Instances: 1.
Plan bbox: center (0.000, 0.000, 0.155) extents (0.032, 0.034, 0.310)
  x in [-0.016, 0.016]  y in [-0.017, 0.017]  z in [0.000, 0.310]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

WOODEN_HANDLE_CENTER = (0.000, 0.000, 0.155)
WOODEN_HANDLE_EXTENTS = (0.032, 0.034, 0.310)

def make_material(name, rgb, roughness=0.55, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_wooden_handle():
    """Build ergonomic turned hammer handle with oval cross sections along Z."""
    bm = bmesh.new()
    
    # Profile definition: (z_height, radius_x, radius_y)
    # Total z range [0.000, 0.310]
    profile_data = [
        (0.000, 0.0145, 0.0155), # rounded butt bottom
        (0.005, 0.0160, 0.0170), # flared butt maximum (matches bbox bounds +/-0.016, +/-0.017)
        (0.020, 0.0150, 0.0160), # butt transition
        (0.060, 0.0135, 0.0145), # lower grip
        (0.120, 0.0120, 0.0130), # waist / ergonomic curve
        (0.200, 0.0130, 0.0140), # upper shaft widening
        (0.260, 0.0135, 0.0150), # under-head shoulder
        (0.280, 0.0118, 0.0133), # tenon entering eye
        (0.305, 0.0118, 0.0133), # inside eye
        (0.310, 0.0118, 0.0133), # top of handle tenon
    ]
    
    n_segments = 32
    rings = []
    
    for z, rx, ry in profile_data:
        ring_verts = []
        for i in range(n_segments):
            angle = 2.0 * math.pi * i / n_segments
            x = rx * math.cos(angle)
            y = ry * math.sin(angle)
            v = bm.verts.new((x, y, z))
            ring_verts.append(v)
        rings.append(ring_verts)
        
    # Bottom cap
    bm.faces.new(reversed(rings[0]))
    
    # Bridge rings
    for r in range(len(rings) - 1):
        r1 = rings[r]
        r2 = rings[r + 1]
        for i in range(n_segments):
            next_i = (i + 1) % n_segments
            bm.faces.new([r1[i], r1[next_i], r2[next_i], r2[i]])
            
    # Top cap
    bm.faces.new(rings[-1])
    
    bm.normal_update()
    me = bpy.data.meshes.new("WoodenHandle")
    bm.to_mesh(me)
    bm.free()
    
    for poly in me.polygons:
        poly.use_smooth = True
        
    obj = bpy.data.objects.new("WoodenHandle", me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Warm rich hickory hardwood tone
    mat = make_material("HickoryWood", (0.68, 0.42, 0.18), roughness=0.55, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj
