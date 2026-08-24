"""ControlSwitches — power, brew, and steam toggle buttons with indicator lights.

Three rounded rectangular push-buttons with small LED indicators positioned on the front control fascia.
Material: brushed metal switches with red/green LED lenses.
Plan bbox: center (0.040, -0.101, 0.320) extents (0.070, 0.012, 0.025)
  x in [0.005, 0.075], y in [-0.107, -0.095], z in [0.307, 0.333]
"""
import bpy
import bmesh
from mathutils import Vector, Matrix

def make_material(name, rgb, roughness=0.2, metallic=0.9):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_control_switches():
    """Build 3 toggle/push buttons with LED jewel bezels on the front panel."""
    bm = bmesh.new()
    
    # 3 switch positions along X: x = 0.015, 0.040, 0.065 (span 0.005 to 0.075)
    # y: -0.107 to -0.095 (center -0.101)
    # z: 0.308 to 0.332 (center 0.320)
    
    switch_xs = [0.017, 0.040, 0.063]
    
    # Common backplate bezel strip
    v_start = len(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.068, 0.004, 0.024), verts=bm.verts[v_start:])
    bmesh.ops.translate(bm, vec=(0.040, -0.097, 0.320), verts=bm.verts[v_start:])
    
    for sx in switch_xs:
        # Button body (metallic rocker / push button)
        v_start = len(bm.verts)
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.014, 0.007, 0.014), verts=bm.verts[v_start:])
        bmesh.ops.translate(bm, vec=(sx, -0.102, 0.317), verts=bm.verts[v_start:])
        
        # LED indicator dot above button
        v_start = len(bm.verts)
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.005, 0.006, 0.004), verts=bm.verts[v_start:])
        bmesh.ops.translate(bm, vec=(sx, -0.102, 0.327), verts=bm.verts[v_start:])

    me = bpy.data.meshes.new("ControlSwitches")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("ControlSwitches", me)
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.001
    bev.segments = 2
    bev.limit_method = 'ANGLE'
    
    mat = make_material("SwitchMetal", (0.85, 0.85, 0.85), roughness=0.2, metallic=0.9)
    obj.data.materials.append(mat)
    
    return obj
