"""LeatherInlay — recessed leather writing pad.

Rectangular leather insert panel flush with top bevel inner margin, featuring a warm forest green finish with subtle gold-embossed blind tooling border.
Material: embossed desk leather, dark green with gold tooling. Instances: 1.
"""
import bpy
import bmesh

# DeskTop top surface is at z = 0.742 + 0.035 / 2 = 0.7595 m.
# With a 1 mm overlap for welding (contact without deep interpenetration):
# LeatherInlay thickness = 0.003 m, z_min = 0.7585, z_max = 0.7615, z_center = 0.760 m.
# Extents match plan in XY, thickness 0.003 m sitting flush on DeskTop.
LEATHER_INLAY_CENTER = (0.000, 0.000, 0.760)
LEATHER_INLAY_EXTENTS = (1.320, 0.620, 0.003)

def make_material(name, rgb, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_leather_inlay():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=LEATHER_INLAY_EXTENTS, verts=bm.verts)
    
    me = bpy.data.meshes.new("LeatherInlay")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("LeatherInlay", me)
    obj.location = LEATHER_INLAY_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    # Classic English desk rich dark forest green leather
    mat = make_material("ForestGreenLeather", (0.035, 0.15, 0.065), roughness=0.55, metallic=0.04)
    obj.data.materials.append(mat)
    
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width = 0.0008
    mod.segments = 2
    mod.limit_method = "ANGLE"
    
    return obj

