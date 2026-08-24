"""ExhaustStack — vertical engine exhaust pipe.

Tall cylindrical vertical exhaust pipe rising from the right side of the engine hood, fitted with a cylindrical muffler canister and a top curved rain-flapper outlet.
Material: heat-resistant matte black iron. Instances: 1.
Bbox: center (0.260, -0.600, 1.650) extents (0.120, 0.120, 0.950)
      x in [0.200, 0.320]  y in [-0.660, -0.540]  z in [1.175, 2.125]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

EXHAUST_STACK_CENTER = (0.260, -0.600, 1.650)
EXHAUST_STACK_EXTENTS = (0.120, 0.120, 0.950)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def add_cyl(bm, center, r1, r2, height, n_seg=16, mat_index=0):
    cz = center[2]
    z_bot = cz - height/2.0
    z_top = cz + height/2.0
    cx, cy = center[0], center[1]
    bot_ring = []
    top_ring = []
    for i in range(n_seg):
        ang = 2.0 * math.pi * i / n_seg
        bot_ring.append(bm.verts.new((cx + r1 * math.cos(ang), cy + r1 * math.sin(ang), z_bot)))
        top_ring.append(bm.verts.new((cx + r2 * math.cos(ang), cy + r2 * math.sin(ang), z_top)))
    for i in range(n_seg):
        next_i = (i + 1) % n_seg
        f = bm.faces.new((bot_ring[i], bot_ring[next_i], top_ring[next_i], top_ring[i]))
        f.material_index = mat_index
    # caps
    f_b = bm.faces.new(bot_ring)
    f_b.material_index = mat_index
    f_t = bm.faces.new(reversed(top_ring))
    f_t.material_index = mat_index

def build_exhaust_stack():
    bm = bmesh.new()

    mat_exhaust = make_material("ExhaustIronMat", (0.15, 0.15, 0.15), roughness=0.7, metallic=0.4)
    mat_muffler = make_material("MufflerMat", (0.18, 0.18, 0.18), roughness=0.6, metallic=0.5)
    mat_chrome_flapper = make_material("ChromeFlapperMat", (0.75, 0.75, 0.77), roughness=0.25, metallic=0.9)

    xc, yc = 0.260, -0.600

    # 1. Lower pipe / penetration into EngineHood
    # Engine hood top at x=0.260, y=-0.600 is around z=1.350. We extend pipe down into the hood to z=1.175 (length 0.275)
    add_cyl(bm, (xc, yc, 1.300), 0.028, 0.028, 0.250, n_seg=16, mat_index=0)
    # Flange collar ring around hood entry (z=1.355)
    add_cyl(bm, (xc, yc, 1.355), 0.050, 0.050, 0.020, n_seg=16, mat_index=0)

    # 2. Muffler lower cone (z: 1.410 to 1.450)
    add_cyl(bm, (xc, yc, 1.430), 0.028, 0.058, 0.040, n_seg=16, mat_index=1)
    # Muffler body (z: 1.450 to 1.830, length 0.380, center 1.640)
    add_cyl(bm, (xc, yc, 1.640), 0.058, 0.058, 0.380, n_seg=20, mat_index=1)
    # Muffler upper cone (z: 1.830 to 1.870)
    add_cyl(bm, (xc, yc, 1.850), 0.058, 0.028, 0.040, n_seg=16, mat_index=1)

    # 3. Upper Tailpipe (z: 1.870 to 2.100, length 0.230, center 1.985)
    add_cyl(bm, (xc, yc, 1.985), 0.026, 0.026, 0.230, n_seg=16, mat_index=0)

    # 4. Rain Flapper Cap on top (z ~ 2.105..2.125)
    p_verts = []
    hx, hy, hz = 0.035, 0.040, 0.004
    for dx in [-hx, hx]:
        for dy in [-hy, hy]:
            for dz in [-hz, hz]:
                p_verts.append(bm.verts.new((xc + dx, yc + 0.010 + dy, 2.115 + dz)))
    p_faces = [
        (p_verts[0], p_verts[2], p_verts[3], p_verts[1]),
        (p_verts[4], p_verts[5], p_verts[7], p_verts[6]),
        (p_verts[0], p_verts[1], p_verts[5], p_verts[4]),
        (p_verts[2], p_verts[6], p_verts[7], p_verts[3]),
        (p_verts[0], p_verts[4], p_verts[6], p_verts[2]),
        (p_verts[1], p_verts[3], p_verts[7], p_verts[5]),
    ]
    for f in p_faces:
        face = bm.faces.new(f)
        face.material_index = 2

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("ExhaustStack")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("ExhaustStack", me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_exhaust)
    obj.data.materials.append(mat_muffler)
    obj.data.materials.append(mat_chrome_flapper)

    return obj
