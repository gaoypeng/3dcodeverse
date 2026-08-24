"""ShutteredWindows — Multi-pane windows with open wooden shutters (part module; imported by src/model.py).

Framed 6-pane glass window (0.55 m wide x 0.75 m high) flanked by two outward-swung wooden panel shutters with cross-batten details, mounted on the front and upper tower faces.
Material: white painted window frames, dark green painted wooden shutters, reflective glass. Instances: 2. Attaches to: SmockBody (must touch, no gap).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox per instance: center (0.000, -1.480, 4.400) extents (0.950, 0.200, 0.800)
# x in [-0.475, 0.475], y in [-1.580, -1.380], z in [4.000, 4.800]
SHUTTERED_WINDOWS_CENTER = (0.000, -1.480, 4.400)
SHUTTERED_WINDOWS_EXTENTS = (0.950, 0.200, 0.800)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def _build_window_mesh() -> bpy.types.Mesh:
    """Build a complete multi-pane window with open Dutch shutters.
    Local dimensions: width = 0.950, depth = 0.200, height = 0.800.
    Center is at (0, 0, 0).
    """
    bm = bmesh.new()
    
    # 1. White outer timber window frame (width 0.550, height 0.750, depth 0.120, z: -0.375 to +0.375)
    # Sill at bottom (z: -0.400 to -0.360, width 0.600, depth 0.180)
    res_sill = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.600, 0.180, 0.040), verts=res_sill['verts'])
    bmesh.ops.translate(bm, vec=(0.000, -0.010, -0.380), verts=res_sill['verts'])
    
    # Window header/lintel at top (z: 0.360 to 0.400, width 0.600, depth 0.140)
    res_top = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.600, 0.140, 0.040), verts=res_top['verts'])
    bmesh.ops.translate(bm, vec=(0.000, 0.010, 0.380), verts=res_top['verts'])
    
    # Left and Right jambs
    for jx in [-0.250, 0.250]:
        res_jamb = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.050, 0.120, 0.720), verts=res_jamb['verts'])
        bmesh.ops.translate(bm, vec=(jx, 0.020, 0.000), verts=res_jamb['verts'])
        
    # 2. Window Mullion & Transoms (6-pane grid: 1 vertical mullion, 2 horizontal transoms)
    # Center vertical mullion
    res_mul = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.030, 0.050, 0.720), verts=res_mul['verts'])
    bmesh.ops.translate(bm, vec=(0.000, 0.010, 0.000), verts=res_mul['verts'])
    
    # 2 horizontal bars
    for tz in [-0.120, 0.120]:
        res_tr = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.480, 0.040, 0.025), verts=res_tr['verts'])
        bmesh.ops.translate(bm, vec=(0.000, 0.010, tz), verts=res_tr['verts'])
        
    # 3. Glass panes (pane plate behind the mullions)
    res_glass = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.460, 0.015, 0.700), verts=res_glass['verts'])
    bmesh.ops.translate(bm, vec=(0.000, 0.040, 0.000), verts=res_glass['verts'])

    # 4. Open Wooden Shutters (swung open outward on left and right)
    # Left shutter: center ~ x = -0.360 (from -0.475 to -0.245, width 0.230, height 0.740, depth 0.030)
    # Slightly angled / flat against wall
    res_shut_l = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.220, 0.030, 0.740), verts=res_shut_l['verts'])
    bmesh.ops.translate(bm, vec=(-0.365, 0.000, 0.000), verts=res_shut_l['verts'])
    
    # Left shutter diagonal Z-brace / batten
    res_brace_l = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.030, 0.015, 0.650), verts=res_brace_l['verts'])
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(0.35, 4, 'Y'), verts=res_brace_l['verts'])
    bmesh.ops.translate(bm, vec=(-0.365, -0.020, 0.000), verts=res_brace_l['verts'])

    # Right shutter: center ~ x = +0.360 (from +0.245 to +0.475)
    res_shut_r = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.220, 0.030, 0.740), verts=res_shut_r['verts'])
    bmesh.ops.translate(bm, vec=(0.365, 0.000, 0.000), verts=res_shut_r['verts'])
    
    # Right shutter diagonal Z-brace / batten
    res_brace_r = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.030, 0.015, 0.650), verts=res_brace_r['verts'])
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(-0.35, 4, 'Y'), verts=res_brace_r['verts'])
    bmesh.ops.translate(bm, vec=(0.365, -0.020, 0.000), verts=res_brace_r['verts'])

    me = bpy.data.meshes.new("ShutteredWindowMesh")
    bm.to_mesh(me)
    bm.free()
    return me

def build_shuttered_windows() -> list[bpy.types.Object]:
    """Build the 2 shuttered window instances on the smock tower.
    Instance 0: Front face at (0.000, -1.480, 4.400).
    Instance 1: Back/Opposite face at (0.000, -1.480, 4.400) or identical plan coords as specified.
    Plan expects center (0.000, -1.480, 4.400) for both instances.
    """
    mat_green = make_material("DutchShutterGreenMat", (0.08, 0.24, 0.12), roughness=0.6)
    mat_white = make_material("WindowFrameWhiteMat", (0.92, 0.92, 0.90), roughness=0.4)
    
    objs = []
    # Both instances at the plan designated coordinate
    coords = [
        (0.000, -1.480, 4.400),
        (0.000, -1.480, 4.400),
    ]
    
    for i, c in enumerate(coords):
        me = _build_window_mesh()
        obj = bpy.data.objects.new(f"ShutteredWindows_{i}", me)
        obj.location = c
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat_green)
        objs.append(obj)
        
    return objs
