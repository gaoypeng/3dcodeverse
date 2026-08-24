"""KettleLid — removable top cover with center grip knob (part module; imported by src/model.py).

Circular stepped lid (diameter 0.108 m, thickness 0.014 m) seated into the top rim with a central cylindrical grip knob (diameter 0.026 m, height 0.016 m).
Material: brushed stainless steel with matte black plastic grip knob.  Instances: 1.  Attaches to: KettleBody (must touch, no gap).

Exports `build_kettle_lid() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh

random.seed(0)

# Plan numbers
# center (0.000, 0.000, 0.203) extents (0.108, 0.108, 0.024)
# x in [-0.054, 0.054]  y in [-0.054, 0.054]  z in [0.191, 0.215]
R_LID = 0.108 / 2  # 0.054


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_kettle_lid() -> bpy.types.Object:
    bm = bmesh.new()

    # Circular gently domed lid from z = 0.191 to z = 0.202
    # Central cylindrical grip knob from z = 0.202 to z = 0.215 (diameter 0.026)
    segments = 48
    
    # Continuous single-surface profile from bottom rim to knob top
    profile = [
        # (z, r, is_knob)
        (0.191, 0.053, False),   # bottom seating lip
        (0.194, 0.054, False),   # outer collar edge
        (0.197, 0.052, False),   # bevel inward
        (0.200, 0.042, False),   # gently domed lid top
        (0.202, 0.020, False),   # lid center plateau
        (0.202, 0.013, True),    # knob base
        (0.208, 0.012, True),    # knob waist
        (0.214, 0.013, True),    # knob top flare
        (0.215, 0.012, True),    # knob top rounded edge
    ]

    rings = []
    for z, r, _ in profile:
        ring = []
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            v = bm.verts.new((r * math.cos(angle), r * math.sin(angle), z))
            ring.append(v)
        rings.append(ring)

    # Bridge all consecutive rings
    for i in range(len(rings) - 1):
        r1 = rings[i]
        r2 = rings[i + 1]
        for j in range(segments):
            jn = (j + 1) % segments
            bm.faces.new((r1[j], r1[jn], r2[jn], r2[j]))

    # Bottom cap
    bottom_c = bm.verts.new((0, 0, profile[0][0]))
    for j in range(segments):
        jn = (j + 1) % segments
        bm.faces.new((bottom_c, rings[0][jn], rings[0][j]))

    # Knob top cap
    knob_c = bm.verts.new((0, 0, profile[-1][0]))
    for j in range(segments):
        jn = (j + 1) % segments
        bm.faces.new((knob_c, rings[-1][j], rings[-1][jn]))

    bm.normal_update()

    me = bpy.data.meshes.new("KettleLid")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("KettleLid", me)
    bpy.context.scene.collection.objects.link(obj)

    for poly in me.polygons:
        poly.use_smooth = True

    # Materials
    mat_steel = make_material("LidSteel", (0.78, 0.79, 0.80), roughness=0.25, metallic=0.95)
    mat_knob = make_material("LidKnobMat", (0.05, 0.05, 0.05), roughness=0.4, metallic=0.0)
    obj.data.materials.append(mat_steel)
    obj.data.materials.append(mat_knob)

    # Assign knob material
    for poly in me.polygons:
        z_poly = sum(me.vertices[v].co.z for v in poly.vertices) / len(poly.vertices)
        r_poly = math.hypot(
            sum(me.vertices[v].co.x for v in poly.vertices) / len(poly.vertices),
            sum(me.vertices[v].co.y for v in poly.vertices) / len(poly.vertices)
        )
        if z_poly >= 0.2018 and r_poly <= 0.015:
            poly.material_index = 1
        else:
            poly.material_index = 0

    return obj
