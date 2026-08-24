"""SailRotorHub — Central iron axle hub (windshaft cap) (part module; imported by src/model.py).

Cylindrical iron shaft collar with cross-shaped hub mortises for mounting the sail stocks, protruding forward at front of cap at z=6.80 m, diameter 0.44 m, length 0.55 m.
Material: cast iron, matte dark charcoal. Instances: 1. Attaches to: WindmillCap.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -1.650, 6.800) extents (0.480, 0.550, 0.480)
# x in [-0.240, 0.240], y in [-1.925, -1.375], z in [6.560, 7.040]
# WindmillCap front gable is at y = -1.800 (world y), center at -0.150, local y = -1.650.
SAIL_ROTOR_HUB_CENTER = (0.000, -1.650, 6.800)
SAIL_ROTOR_HUB_EXTENTS = (0.480, 0.550, 0.480)

def make_material(name, rgb, roughness=0.4, metallic=0.9):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_sail_rotor_hub() -> bpy.types.Object:
    """Sail rotor hub (cast iron windshaft head / poll end).
    Center: (0.0, -1.650, 6.800), extents: 0.480 x 0.550 x 0.480.
    Local coords:
      X: [-0.240, +0.240] (width 0.480)
      Y: [-0.275, +0.275] (world Y: [-1.925, -1.375], length 0.550)
      Z: [-0.240, +0.240] (world Z: [6.560, 7.040], height 0.480)
    """
    bm = bmesh.new()
    
    # 1. Main cylindrical axle sleeve / poll body (aligned along Y axis, local y in [-0.275, 0.275])
    # Length 0.550, centered at local y = 0.000
    res_cyl = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=24,
        radius1=0.210,
        radius2=0.230,
        depth=0.550
    )
    # Rotate cylinder from Z axis to Y axis
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi / 2.0, 4, 'X'), verts=res_cyl['verts'])
    
    # 2. Mortise boxes on the poll head:
    # Outer bounds should stay within x in [-0.240, 0.240], z in [-0.240, 0.240], y in [-0.275, 0.275]
    # Stock mortises at front-middle (local y = -0.050 to -0.220)
    # Horizontal stock mortise
    res_m1 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.480, 0.160, 0.120), verts=res_m1['verts'])
    bmesh.ops.translate(bm, vec=(0.0, -0.110, 0.0), verts=res_m1['verts'])
    
    # Vertical stock mortise
    res_m2 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.120, 0.160, 0.480), verts=res_m2['verts'])
    bmesh.ops.translate(bm, vec=(0.0, -0.110, 0.0), verts=res_m2['verts'])
    
    # 3. Hub end cap / nose cone (local y near -0.250)
    res_cap = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=16,
        radius1=0.140,
        radius2=0.080,
        depth=0.050
    )
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(math.pi / 2.0, 4, 'X'), verts=res_cap['verts'])
    bmesh.ops.translate(bm, vec=(0.0, -0.250, 0.0), verts=res_cap['verts'])

    me = bpy.data.meshes.new("SailRotorHub")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SailRotorHub", me)
    obj.location = (0.000, -1.650, 6.800)
    bpy.context.scene.collection.objects.link(obj)
    
    # Material: cast iron matte dark charcoal
    mat_iron = make_material("CastIronMat", (0.15, 0.15, 0.16), roughness=0.45, metallic=0.9)
    obj.data.materials.append(mat_iron)
    
    return obj
