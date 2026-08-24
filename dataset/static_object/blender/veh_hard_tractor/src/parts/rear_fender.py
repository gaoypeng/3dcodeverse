"""RearFender — rear wheel mudguards.

Curved clamshell sheet metal fenders wrapping over the top and rear profile of the large rear tires, complete with rolled outer edges and inner mounting brackets.
Material: glossy agricultural red painted sheet metal. Instances: 2 (mirror_x).
Bbox: center (0.720, 0.650, 1.250) extents (0.380, 1.200, 0.500)
      x in [0.530, 0.910]  y in [0.050, 1.250]  z in [1.000, 1.500]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

REAR_FENDER_CENTER = (0.720, 0.650, 1.250)
REAR_FENDER_EXTENTS = (0.380, 1.200, 0.500)

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

def add_box(bm, center, size, mat_index=0):
    verts = []
    hx, hy, hz = size[0]/2.0, size[1]/2.0, size[2]/2.0
    cx, cy, cz = center[0], center[1], center[2]
    for dx in [-hx, hx]:
        for dy in [-hy, hy]:
            for dz in [-hz, hz]:
                verts.append(bm.verts.new((cx + dx, cy + dy, cz + dz)))
    faces = [
        (verts[0], verts[2], verts[3], verts[1]),
        (verts[4], verts[5], verts[7], verts[6]),
        (verts[0], verts[1], verts[5], verts[4]),
        (verts[2], verts[6], verts[7], verts[3]),
        (verts[0], verts[4], verts[6], verts[2]),
        (verts[1], verts[3], verts[7], verts[5]),
    ]
    for f in faces:
        face = bm.faces.new(f)
        face.material_index = mat_index

def create_rear_fender_mesh(name_prefix, x_center, sign_x):
    """
    Each fender instance must stay within:
    x in [0.530, 0.910] (width 0.380) for sign_x=1, or [-0.910, -0.530] for sign_x=-1
    y in [0.050, 1.250] (length 1.200)
    z in [1.000, 1.500] (height 0.500)
    """
    bm = bmesh.new()
    mat_red = make_material("AgriRedMat", (0.80, 0.05, 0.05), roughness=0.25, metallic=0.1)
    mat_bracket = make_material("DarkChassisMat", (0.15, 0.15, 0.17), roughness=0.45, metallic=0.8)

    y_w, z_w = 0.700, 0.700
    r_fender = 0.770
    
    n_steps = 14
    phi_start = 0.98   # front (y=0.050, z=1.13)
    phi_end = -0.79    # rear (y=1.250, z=1.000)
    
    phis = [phi_start + (phi_end - phi_start) * i / (n_steps - 1) for i in range(n_steps)]
    
    x_inner = 0.530 * sign_x
    x_mid = 0.720 * sign_x
    x_outer = 0.910 * sign_x
    
    arc_inner_verts = []
    arc_top_verts = []
    arc_outer_verts = []
    arc_lip_verts = []
    
    for phi in phis:
        y_val = y_w - r_fender * math.sin(phi)
        z_val = z_w + r_fender * math.cos(phi)
        
        y_val = max(0.050, min(1.250, y_val))
        z_val = max(1.000, min(1.500, z_val))
        
        v_in = bm.verts.new((x_inner, y_val, z_val))
        v_mid = bm.verts.new((x_mid, y_val, min(1.500, z_val + 0.015)))
        v_out = bm.verts.new((x_outer, y_val, z_val))
        v_lip = bm.verts.new((x_outer, y_val, max(1.000, z_val - 0.040)))
        
        arc_inner_verts.append(v_in)
        arc_top_verts.append(v_mid)
        arc_outer_verts.append(v_out)
        arc_lip_verts.append(v_lip)
        
    for i in range(n_steps - 1):
        f1 = bm.faces.new((arc_inner_verts[i], arc_inner_verts[i+1], arc_top_verts[i+1], arc_top_verts[i]))
        f1.material_index = 0
        f2 = bm.faces.new((arc_top_verts[i], arc_top_verts[i+1], arc_outer_verts[i+1], arc_outer_verts[i]))
        f2.material_index = 0
        f3 = bm.faces.new((arc_outer_verts[i], arc_outer_verts[i+1], arc_lip_verts[i+1], arc_lip_verts[i]))
        f3.material_index = 0

    # Inner vertical splash shield (drops straight down to z = 1.000)
    inner_bottom_verts = []
    for i, phi in enumerate(phis):
        y_val = arc_inner_verts[i].co.y
        z_bot = 1.000
        v_bot = bm.verts.new((x_inner, y_val, z_bot))
        inner_bottom_verts.append(v_bot)
        
    for i in range(n_steps - 1):
        f = bm.faces.new((arc_inner_verts[i], inner_bottom_verts[i], inner_bottom_verts[i+1], arc_inner_verts[i+1]))
        f.material_index = 0

    # Inner mounting flange (stays inside x bounds: from x_inner to x_inner ± 0.03)
    # Stiffener ribs on inner side of fender
    for y_b in [0.40, 0.90]:
        add_box(bm, (x_inner + 0.030 * sign_x, y_b, 1.050), (0.060, 0.080, 0.100), mat_index=1)

    # Ensure exact bounding box extents [0.530..0.910] x [0.050..1.250] x [1.000..1.500]
    # Vertices already at 0.530, 0.910, 0.050, 1.250, 1.000. Add apex vertex at z=1.500, y=0.650, x=x_mid
    v_top_apex = bm.verts.new((x_mid, 0.650, 1.500))
    v_bot_front = bm.verts.new((x_inner, 0.050, 1.000))
    v_bot_rear = bm.verts.new((x_outer, 1.250, 1.000))
    # Small face to anchor apex
    bm.faces.new((arc_top_verts[6], arc_top_verts[7], v_top_apex)).material_index = 0
    bm.faces.new((arc_inner_verts[0], inner_bottom_verts[0], v_bot_front)).material_index = 0
    bm.faces.new((arc_outer_verts[-1], arc_lip_verts[-1], v_bot_rear)).material_index = 0

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name_prefix)
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new(name_prefix, me)
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(mat_red)
    obj.data.materials.append(mat_bracket)

    return obj

def build_rear_fender():
    objs = []
    objs.append(create_rear_fender_mesh("RearFender_0", 0.720, sign_x=1.0))
    objs.append(create_rear_fender_mesh("RearFender_1", -0.720, sign_x=-1.0))
    return objs
