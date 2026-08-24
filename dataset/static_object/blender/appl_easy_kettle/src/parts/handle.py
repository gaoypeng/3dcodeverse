"""Handle — ergonomic rear loop handle opposite the spout (part module; imported by src/model.py).

Curved D-shaped loop handle with rounded rectangular cross-section (22 × 16 mm), attached to the upper rear and lower rear body at +Y.
Material: matte black heat-resistant plastic.  Instances: 1.  Attaches to: KettleBody (must touch, no gap).

Exports `build_handle() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh

random.seed(0)

# Plan numbers
# center (0.000, 0.092, 0.125) extents (0.028, 0.060, 0.130)
# x in [-0.014, 0.014]  y in [0.062, 0.122]  z in [0.060, 0.190]


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_handle() -> bpy.types.Object:
    bm = bmesh.new()

    # Spine points along the D-curve matching plan bbox: y in [0.062, 0.122], z in [0.060, 0.190]
    spine = [
        # Top anchor (y=0.068, z=0.184)
        (0.068, 0.184),
        (0.078, 0.185),
        (0.092, 0.184),
        (0.104, 0.178),
        (0.114, 0.165),
        (0.116, 0.145),
        (0.116, 0.125),
        (0.116, 0.105),
        (0.114, 0.085),
        (0.104, 0.072),
        (0.092, 0.066),
        (0.078, 0.065),
        (0.068, 0.066),
    ]

    half_x = 0.011  # 22 mm wide
    half_t = 0.006  # 12 mm thick

    cross_sections = []
    num_pts = len(spine)

    for i in range(num_pts):
        y, z = spine[i]
        
        # Tangent
        if i == 0:
            dy = spine[1][0] - spine[0][0]
            dz = spine[1][1] - spine[0][1]
        elif i == num_pts - 1:
            dy = spine[-1][0] - spine[-2][0]
            dz = spine[-1][1] - spine[-2][1]
        else:
            dy = spine[i + 1][0] - spine[i - 1][0]
            dz = spine[i + 1][1] - spine[i - 1][1]

        length = math.hypot(dy, dz)
        if length > 1e-6:
            ty, tz = dy / length, dz / length
        else:
            ty, tz = 0, 1

        # Normal vector
        ny, nz = -tz, ty

        wx = half_x
        wt = half_t

        n_prof = 12
        ring_verts = []
        for p in range(n_prof):
            ang = 2 * math.pi * p / n_prof
            ox = math.cos(ang) * wx
            on = math.sin(ang) * wt
            
            vx = ox
            vy = y + on * ny
            vz = z + on * nz
            ring_verts.append(bm.verts.new((vx, vy, vz)))
        cross_sections.append(ring_verts)

    # Bridge rings
    for i in range(num_pts - 1):
        r1 = cross_sections[i]
        r2 = cross_sections[i + 1]
        for j in range(12):
            jn = (j + 1) % 12
            bm.faces.new((r1[j], r1[jn], r2[jn], r2[j]))

    # Cap ends
    top_c = bm.verts.new((0.0, spine[0][0], spine[0][1]))
    for j in range(12):
        jn = (j + 1) % 12
        bm.faces.new((top_c, cross_sections[0][j], cross_sections[0][jn]))

    bot_c = bm.verts.new((0.0, spine[-1][0], spine[-1][1]))
    for j in range(12):
        jn = (j + 1) % 12
        bm.faces.new((bot_c, cross_sections[-1][jn], cross_sections[-1][j]))

    bm.normal_update()

    me = bpy.data.meshes.new("Handle")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("Handle", me)
    bpy.context.scene.collection.objects.link(obj)

    for poly in me.polygons:
        poly.use_smooth = True

    mat = make_material("HandleBlackPlastic", (0.06, 0.06, 0.06), roughness=0.45, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
