"""CeramicTray — shallow oval bonsai pot container (part module; imported by src/model.py).

Shallow oval tray (340 mm × 240 mm × 40 mm) with a rolled upper rim, gentle downward taper, four small low corner feet (6 mm height), and a drainage recess.
Material: unglazed dark brown stoneware ceramic, matte textured.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan numbers
CERAMIC_TRAY_CENTER = (0.000, 0.000, 0.020)
CERAMIC_TRAY_EXTENTS = (0.340, 0.240, 0.040)

def make_material(name, rgb, roughness=0.65, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_ceramic_tray() -> bpy.types.Object:
    bm = bmesh.new()

    # Base tray dimensions
    rx_top = 0.170
    ry_top = 0.120
    rx_bot = 0.150
    ry_bot = 0.105
    z_foot_top = 0.006
    z_top = 0.040
    n_seg = 48

    # Create oval body rings
    # Ring 0: bottom base (z = 0.006)
    # Ring 1: lower-mid
    # Ring 2: upper rim outer (z = 0.040)
    # Ring 3: rim lip outer bulge (z = 0.036)
    # Ring 4: inner rim top (z = 0.040, rx=0.155, ry=0.105)
    # Ring 5: inner floor (z = 0.012, rx=0.138, ry=0.088)

    # 1. Main outer lofted bowl
    # We can create concentric rings and bridge them
    rings = [
        # (z, rx, ry)
        (0.006, rx_bot, ry_bot),
        (0.018, rx_bot + 0.010, ry_bot + 0.008),
        (0.035, rx_top - 0.002, ry_top - 0.002),
        (0.040, rx_top, ry_top), # outer lip top
        (0.040, rx_top - 0.012, ry_top - 0.012), # inner rim top
        (0.014, rx_bot - 0.012, ry_bot - 0.012), # inner floor rim
    ]

    vert_rings = []
    for (z, rx, ry) in rings:
        v_ring = []
        for i in range(n_seg):
            theta = 2.0 * math.pi * i / n_seg
            vx = rx * math.cos(theta)
            vy = ry * math.sin(theta)
            v = bm.verts.new((vx, vy, z))
            v_ring.append(v)
        vert_rings.append(v_ring)

    # Bridge the rings to create outer wall and inner cavity
    for r in range(len(rings) - 1):
        r1 = vert_rings[r]
        r2 = vert_rings[r+1]
        for i in range(n_seg):
            i_next = (i + 1) % n_seg
            if r < 4:
                # outer walls going up
                bm.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])
            else:
                # inner wall going down
                bm.faces.new([r1[i], r2[i], r2[i_next], r1[i_next]])

    # Bottom outer cap (z=0.006)
    v_bot_center = bm.verts.new((0, 0, 0.006))
    r_bot = vert_rings[0]
    for i in range(n_seg):
        i_next = (i + 1) % n_seg
        bm.faces.new([v_bot_center, r_bot[i_next], r_bot[i]])

    # Inner floor cap (z=0.014)
    v_floor_center = bm.verts.new((0, 0, 0.014))
    r_floor = vert_rings[-1]
    for i in range(n_seg):
        i_next = (i + 1) % n_seg
        bm.faces.new([v_floor_center, r_floor[i], r_floor[i_next]])

    # 4 Low feet extending to z = 0.000
    # Placed symmetrically at corners of the base
    foot_positions = [
        (0.100, 0.065),
        (-0.100, 0.065),
        (-0.100, -0.065),
        (0.100, -0.065),
    ]
    foot_dx = 0.022
    foot_dy = 0.014
    for fx, fy in foot_positions:
        # 8 vertices for a small box foot
        fverts = []
        for sx in [-1, 1]:
            for sy in [-1, 1]:
                # bottom vert (z=0.000)
                v_b = bm.verts.new((fx + sx * foot_dx, fy + sy * foot_dy, 0.000))
                # top vert (z=0.007 inside base)
                v_t = bm.verts.new((fx + sx * foot_dx * 1.1, fy + sy * foot_dy * 1.1, 0.007))
                fverts.append((v_b, v_t))
        # fverts order: (-,-), (-,+), (+,-), (+,+)
        v00_b, v00_t = fverts[0]
        v01_b, v01_t = fverts[1]
        v10_b, v10_t = fverts[2]
        v11_b, v11_t = fverts[3]

        bm.faces.new([v00_b, v10_b, v11_b, v01_b]) # bottom
        bm.faces.new([v00_b, v01_b, v01_t, v00_t]) # side -x
        bm.faces.new([v10_b, v10_t, v11_t, v11_b]) # side +x
        bm.faces.new([v00_b, v00_t, v10_t, v10_b]) # side -y
        bm.faces.new([v01_b, v11_b, v11_t, v01_t]) # side +y

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("CeramicTray")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("CeramicTray", me)
    bpy.context.scene.collection.objects.link(obj)

    # Ceramic stoneware material (dark warm brown/terracotta slate)
    mat = make_material("CeramicTrayMat", (0.28, 0.17, 0.12), roughness=0.68, metallic=0.02)
    obj.data.materials.append(mat)

    return obj
