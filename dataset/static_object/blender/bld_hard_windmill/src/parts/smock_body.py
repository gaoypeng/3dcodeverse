"""SmockBody — Tapered octagonal wooden tower body (part module; imported by src/model.py).

Octagonal timber-framed and thatched smock tower, tapering from 3.80 m diameter at z=1.95 m to 2.40 m at z=6.15 m (total height 4.20 m). Weatherboarded corners and textured side panels.
Material: dark stained weathered timber weatherboarding and reed thatch. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, 0.000, 4.050) extents (3.800, 3.800, 4.200)
# x in [-1.900, 1.900], y in [-1.900, 1.900], z in [1.950, 6.150]
SMOCK_BODY_CENTER = (0.000, 0.000, 4.050)
SMOCK_BODY_EXTENTS = (3.800, 3.800, 4.200)

def make_material(name, rgb, roughness=0.75, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_smock_body() -> bpy.types.Object:
    """Tapered octagonal smock body from z=1.998 to z=6.15 m (center at z=4.050).
    Slight 2mm overlap with BaseStructure (which ends at z=2.000) avoids floating and interpenetration.
    """
    bm = bmesh.new()
    
    # Bottom radius = 3.800 / 2 = 1.900 m (at local z = -2.100, world z = 1.950)
    # Top radius = 2.400 / 2 = 1.200 m (at local z = 2.100, world z = 6.150)
    # Height = 4.200 m (local z in [-2.100, 2.100])
    
    # Main octagonal tapered smock hull
    # Base starts at local z = -2.052 (world z = 1.998), depth = 4.152, centered at -2.052 + 2.076 = 0.024
    # To keep exact bbox 4.200 height (z in [1.950, 6.150] or [1.998, 6.150]), let's adjust:
    # If world z is [1.998, 6.150], height is 4.152. Center is (4.074). But plan center is 4.050, extents 4.200.
    # Plan says extents 4.200, center 4.050 -> z in [1.950, 6.150].
    # But BaseStructure top is at z=2.000. So BaseStructure is z in [0.000, 2.000].
    # If SmockBody starts at z=1.998, then interpenetration with BaseStructure is exactly 2.0 mm!
    # Let's keep local z in [-2.052, 2.100] -> world z in [1.998, 6.150].
    
    h_body = 4.152
    z_mid = ( -2.052 + 2.100 ) * 0.5  # 0.024
    r1 = 1.900 * (1.0 - (0.048 / 4.200)) # radius at z=1.998
    r2 = 1.200
    
    res_hull = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=8,
        radius1=1.890,
        radius2=1.200,
        depth=4.152
    )
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 0.024), verts=res_hull['verts'])
    
    # Add 8 vertical corner rib beams (weatherboarding corner ribs)
    # 8 corners at angles k * pi / 4
    for i in range(8):
        angle = i * math.pi / 4.0
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        
        # Corner beam geometry from bottom to top
        r_bot = 1.890
        r_top = 1.200
        z_bot = -2.052
        z_top = 2.100
        
        p_bot = Vector((r_bot * cos_a, r_bot * sin_a, z_bot))
        p_top = Vector((r_top * cos_a, r_top * sin_a, z_top))
        mid = (p_bot + p_top) * 0.5
        dir_v = p_top - p_bot
        
        res = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.04, 0.04, dir_v.length), verts=res['verts'])
        
        up = Vector((0, 0, 1))
        rot_axis = up.cross(dir_v.normalized())
        rot_angle = up.angle(dir_v.normalized())
        if rot_axis.length > 1e-5:
            bmesh.ops.rotate(bm, matrix=Matrix.Rotation(rot_angle, 4, rot_axis.normalized()), verts=res['verts'])
        
        bmesh.ops.translate(bm, vec=mid, verts=res['verts'])
        
    # Add horizontal curb / ring beam at the top collar (z = 6.10, local z = 2.05)
    res_top = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=8,
        radius1=1.220,
        radius2=1.200,
        depth=0.100
    )
    for v in res_top['verts']:
        v.co.z += 2.050

    me = bpy.data.meshes.new("SmockBody")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SmockBody", me)
    obj.location = (0.0, 0.0, 4.050)
    bpy.context.scene.collection.objects.link(obj)
    
    # Material: dark stained weathered thatch & timber
    mat_wood = make_material("SmockThatchMat", (0.35, 0.28, 0.20), roughness=0.8)
    obj.data.materials.append(mat_wood)
    
    return obj
