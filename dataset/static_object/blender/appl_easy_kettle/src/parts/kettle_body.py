"""KettleBody — main liquid chamber and reservoir jug (part module; imported by src/model.py).

Tapered cylindrical jug body with lower diameter 0.138 m and upper collar diameter 0.112 m, total height 0.175 m, bottom overlapping the heating base by 3 mm.
Material: brushed stainless steel.  Instances: 1.  Attaches to: HeatingBase (must touch, no gap).

Exports `build_kettle_body() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh

random.seed(0)

# Plan numbers
KETTLE_BODY_CENTER = (0.000, 0.000, 0.106)
KETTLE_BODY_EXTENTS = (0.138, 0.138, 0.175)
# z in [0.019, 0.194]
R_BOTTOM = 0.138 / 2  # 0.069
R_TOP = 0.112 / 2     # 0.056
H_BODY = 0.175
Z_MIN = 0.019
Z_MAX = 0.194


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_kettle_body() -> bpy.types.Object:
    bm = bmesh.new()

    # Create the main jug body:
    # A tapered body from z=0.019 to z=0.194
    # Lower rim sits at 0.019 (overlapping base top at 0.022 by 3 mm)
    # Bottom diameter 0.138, Top collar diameter 0.112
    segments = 48
    
    # We can create a profile and spin/loft, or build rings
    # Let's create rings along Z to give it a sleek kettle contour:
    # - z=0.019: r = 0.067 (slight base curve)
    # - z=0.025: r = 0.069 (maximum lower diameter 0.138 m)
    # - z=0.080: r = 0.068
    # - z=0.150: r = 0.060
    # - z=0.185: r = 0.056 (top collar diameter 0.112 m)
    # - z=0.194: r = 0.056 (rim collar)
    
    profile = [
        (0.019, 0.067, True),   # z, radius, is_bottom_cap
        (0.023, 0.069, False),
        (0.060, 0.0685, False),
        (0.120, 0.064, False),
        (0.170, 0.058, False),
        (0.188, 0.056, False),
        (0.194, 0.056, True),   # top cap / lip
    ]

    rings = []
    for z, r, _ in profile:
        ring_verts = []
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            x = r * math.cos(angle)
            y = r * math.sin(angle)
            v = bm.verts.new((x, y, z))
            ring_verts.append(v)
        rings.append(ring_verts)

    # Bridge rings with faces
    for i in range(len(rings) - 1):
        r1 = rings[i]
        r2 = rings[i + 1]
        for j in range(segments):
            jn = (j + 1) % segments
            bm.faces.new((r1[j], r1[jn], r2[jn], r2[j]))

    # Bottom cap
    bottom_center = bm.verts.new((0, 0, profile[0][0]))
    for j in range(segments):
        jn = (j + 1) % segments
        bm.faces.new((bottom_center, rings[0][jn], rings[0][j]))

    # Top cap (slightly recessed lip)
    top_center = bm.verts.new((0, 0, profile[-1][0]))
    for j in range(segments):
        jn = (j + 1) % segments
        bm.faces.new((top_center, rings[-1][j], rings[-1][jn]))

    bm.normal_update()

    me = bpy.data.meshes.new("KettleBody")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("KettleBody", me)
    bpy.context.scene.collection.objects.link(obj)

    # Smooth shading
    for poly in me.polygons:
        poly.use_smooth = True

    # Brushed stainless steel material
    mat = make_material("BrushedStainlessSteel", (0.78, 0.79, 0.80), roughness=0.25, metallic=0.95)
    obj.data.materials.append(mat)

    return obj
