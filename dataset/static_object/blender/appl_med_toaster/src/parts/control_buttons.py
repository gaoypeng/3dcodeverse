"""ControlButtons — Auxiliary function push buttons (Defrost / Cancel / Reheat).

Three small horizontal oval push buttons (diameter 0.010 m each) aligned side-by-side above the browning dial on the front panel.
Material: brushed aluminum with translucent LED ring indicator.
Plan bbox: center (0.000, -0.086, 0.085) extents (0.060, 0.010, 0.014)
  x in [-0.030, 0.030]  y in [-0.091, -0.081]  z in [0.078, 0.092]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

CONTROL_BUTTONS_CENTER = (0.000, -0.086, 0.085)
CONTROL_BUTTONS_EXTENTS = (0.060, 0.010, 0.014)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_control_buttons():
    # 3 small circular / oval buttons combined into one mesh, span = 0.060 m along X
    # Button radius = 0.006 m (diameter 0.012 m), depth = 0.010 m along Y
    bm = bmesh.new()
    
    xs = [-0.024, 0.0, 0.024]
    r_x = 0.006
    r_z = 0.006
    depth = CONTROL_BUTTONS_EXTENTS[1] # 0.010
    
    for x_off in xs:
        # Create cylinder along Z
        bm_cyl = bmesh.new()
        bmesh.ops.create_cone(
            bm_cyl,
            cap_ends=True,
            segments=20,
            radius1=r_z,
            radius2=r_z,
            depth=depth
        )
        # Rotate to align along Y
        bmesh.ops.rotate(
            bm_cyl,
            cent=(0, 0, 0),
            matrix=Matrix.Rotation(math.radians(90), 4, 'X'),
            verts=bm_cyl.verts
        )
        # Translate to local X offset
        bmesh.ops.translate(
            bm_cyl,
            vec=(x_off, 0, 0),
            verts=bm_cyl.verts
        )
        # Merge into bm
        bm_cyl.to_mesh(bpy.data.meshes.new("tmp"))
        # Add to main bm directly
        for v in bm_cyl.verts:
            pass
        # simpler: copy geometry into bm
        # We can construct them directly in bm
        bm_cyl.free()
    
    # Clean construction directly in bm:
    bm.free()
    bm = bmesh.new()
    for x_off in xs:
        # Cylinder along Z
        ret = bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            segments=24,
            radius1=0.006,
            radius2=0.0055,
            depth=depth
        )
        verts = ret["verts"]
        # Rotate around X
        bmesh.ops.rotate(
            bm,
            cent=(0, 0, 0),
            matrix=Matrix.Rotation(math.radians(90), 4, 'X'),
            verts=verts
        )
        # Translate to x_off
        bmesh.ops.translate(
            bm,
            vec=(x_off, 0, 0),
            verts=verts
        )
        
    me = bpy.data.meshes.new("ControlButtons")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("ControlButtons", me)
    obj.location = CONTROL_BUTTONS_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0008
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    mat = make_material("ButtonAlum", (0.85, 0.85, 0.88), roughness=0.3, metallic=0.9)
    obj.data.materials.append(mat)
    return obj
