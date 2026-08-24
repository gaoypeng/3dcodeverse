"""DripTray — liquid collection reservoir at the base of the brew station.

Shallow rectangular basin projecting forward from the front lower chassis with smooth rounded front corners.
Material: brushed stainless steel.
Plan bbox: center (0.000, -0.140, 0.045) extents (0.250, 0.120, 0.060)
  x in [-0.125, 0.125], y in [-0.200, -0.080], z in [0.015, 0.075]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.25, metallic=0.95):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_drip_tray():
    """Build the drip tray basin under the grouphead and steam wand."""
    bm = bmesh.new()
    
    # Outer drip tray box matching plan exactly
    # x in [-0.125, 0.125] (width 0.250)
    # y in [-0.200, -0.080] (depth 0.120, center -0.140)
    # z in [0.015, 0.075] (height 0.060, center 0.045)
    v_start = len(bm.verts)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.250, 0.120, 0.060), verts=bm.verts[v_start:])
    bmesh.ops.translate(bm, vec=(0.000, -0.140, 0.045), verts=bm.verts[v_start:])
    
    me = bpy.data.meshes.new("DripTray")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("DripTray", me)
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.006
    bev.segments = 3
    bev.limit_method = 'ANGLE'
    
    mat = make_material("DripTrayStainless", (0.80, 0.81, 0.83), roughness=0.25, metallic=0.95)
    obj.data.materials.append(mat)
    
    return obj
