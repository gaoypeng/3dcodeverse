"""SailBlades — Four cross lattice wind sails (part module; imported by src/model.py).

Four rectangular lattice sail wings mounted on timber stocks in a 90-degree cross, total tip-to-tip span 7.60 m. Each wing has a thick central stock bar (0.08 x 0.08 m) with 18 transverse bars and 3 longitudinal laths forming a fine lattice grid (0.65 m wide, 3.45 m long per arm).
Material: white painted timber framework with natural wood lattice slats. Instances: 4 (radial). Attaches to: SailRotorHub (must touch, no gap).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -1.820, 6.800) extents (7.600, 0.220, 7.600)
# x in [-3.800, 3.800], y in [-1.930, -1.710], z in [3.000, 10.600]
# Local coords centered at (0, -1.820, 6.800):
# Y range: [-0.110, +0.110] (world Y: [-1.930, -1.710]).
# SailRotorHub is centered at (0, -1.650, 6.800), extents (0.480, 0.550, 0.480) -> world Y: [-1.925, -1.375], radius in X/Z: 0.240.
SAIL_BLADES_CENTER = (0.000, -1.820, 6.800)
SAIL_BLADES_EXTENTS = (7.600, 0.220, 7.600)

def make_material(name, rgb, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def _build_single_sail_blade_mesh(arm_angle: float) -> bpy.types.Mesh:
    """Build one sail wing extending from hub out to radius 3.80m (total arm length 3.80m),
    rotated in the X-Z plane by `arm_angle` (0 = up (+Z), pi/2 = right (+X), pi = down (-Z), 3pi/2 = left (-X)).
    The Y bounds locally will span from -0.110 to +0.110 (depth 0.220).
    
    To touch SailRotorHub (radius 0.240m) with 2mm overlap:
    Stock starts at radius r = 0.238m and extends to r = 3.800m.
    """
    bm = bmesh.new()
    
    # 1. Main timber stock along the arm length
    # Starts at r = 0.238 m (2mm inside hub radius 0.240m), ends at r = 3.800 m.
    # Length = 3.800 - 0.238 = 3.562 m.
    r_inner = 0.238
    r_outer = 3.800
    stock_len = r_outer - r_inner
    stock_mid_z = (r_inner + r_outer) * 0.5
    
    res_stock = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.08, 0.12, stock_len), verts=res_stock['verts'])
    bmesh.ops.translate(bm, vec=(0.0, 0.020, stock_mid_z), verts=res_stock['verts'])
    
    # 2. Stock inner bracket at radius r_inner = 0.238 to 0.400
    res_clamp = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.09, 0.220, 0.160), verts=res_clamp['verts'])
    bmesh.ops.translate(bm, vec=(0.0, 0.000, r_inner + 0.080), verts=res_clamp['verts'])
    
    # 3. Tip end cap (ensuring Y extents reach [-0.110, +0.110] and Z reaches 3.800)
    res_tip = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.06, 0.220, 0.04), verts=res_tip['verts'])
    bmesh.ops.translate(bm, vec=(0.0, 0.000, 3.780), verts=res_tip['verts'])
    
    # 4. Lattice framework:
    # 18 transverse bars (bars / sail ribs) spaced along the arm
    n_bars = 18
    r_start = 0.550
    r_end = 3.750
    step = (r_end - r_start) / (n_bars - 1)
    
    for b in range(n_bars):
        rz = r_start + b * step
        # Transverse rib: width 0.65m, thickness 0.03m, depth in Y = 0.04m
        # Off-center: centered around x = 0.225 (from -0.10 to +0.55)
        res_bar = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.650, 0.035, 0.030), verts=res_bar['verts'])
        bmesh.ops.translate(bm, vec=(0.225, -0.050, rz), verts=res_bar['verts'])
        
    # 3 longitudinal laths running across the transverse bars
    lath_xs = [0.080, 0.280, 0.520]
    lath_len = r_end - r_start + 0.060
    lath_center_z = (r_start + r_end) * 0.5
    for lx in lath_xs:
        res_lath = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.025, 0.020, lath_len), verts=res_lath['verts'])
        bmesh.ops.translate(bm, vec=(lx, -0.070, lath_center_z), verts=res_lath['verts'])
        
    # Leading wind board on the leading edge (x = -0.06 to -0.09)
    res_board = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.080, 0.015, lath_len), verts=res_board['verts'])
    bmesh.ops.translate(bm, vec=(-0.050, -0.050, lath_center_z), verts=res_board['verts'])

    # Rotate the entire blade in the X-Z plane around Y axis by arm_angle
    rot_mat = Matrix.Rotation(-arm_angle, 4, 'Y')
    bmesh.ops.rotate(bm, matrix=rot_mat, verts=bm.verts)

    me = bpy.data.meshes.new("SailBladeMesh")
    bm.to_mesh(me)
    bm.free()
    return me

def build_sail_blades() -> list[bpy.types.Object]:
    """Build the 4 cross lattice sails as top-level objects SailBlades_0 .. SailBlades_3.
    World center: (0.000, -1.820, 6.800).
    """
    mat_white = make_material("SailTimberMat", (0.92, 0.90, 0.85), roughness=0.5)
    
    objs = []
    angles = [0.0, math.pi * 0.5, math.pi, math.pi * 1.5]
    
    for i, ang in enumerate(angles):
        me = _build_single_sail_blade_mesh(ang)
        obj = bpy.data.objects.new(f"SailBlades_{i}", me)
        obj.location = (0.000, -1.820, 6.800)
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat_white)
        objs.append(obj)
        
    return objs
