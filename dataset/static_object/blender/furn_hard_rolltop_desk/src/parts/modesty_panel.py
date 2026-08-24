"""ModestyPanel — rear kneehole privacy panel bridging both pedestals.

Vertical mahogany backboard spanning the central knee-hole gap between the two pedestals, recessed 80 mm from the back desk edge.
Material: polished mahogany wood, warm dark brown. Instances: 1.
"""
import bpy
import bmesh

MODESTY_PANEL_CENTER = (0.000, 0.260, 0.480)
MODESTY_PANEL_EXTENTS = (0.580, 0.020, 0.490)

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_modesty_panel():
    mat = make_material("MahoganyModestyPanel", (0.27, 0.11, 0.04), roughness=0.4, metallic=0.0)
    
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=MODESTY_PANEL_EXTENTS, verts=bm.verts)
    
    me = bpy.data.meshes.new("ModestyPanel")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("ModestyPanel", me)
    obj.location = MODESTY_PANEL_CENTER
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width = 0.002
    mod.segments = 2
    mod.limit_method = "ANGLE"
    
    return obj
