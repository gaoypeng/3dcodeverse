"""DeskTop — main horizontal writing desktop surface.

Solid mahogany top panel, 35 mm thick, with an ogee/bevelled outer edge overhang extending 35 mm beyond the pedestals on all four sides.
Material: polished mahogany wood, warm dark brown. Instances: 1.
"""
import bpy
import bmesh

DESK_TOP_CENTER = (0.000, 0.000, 0.742)
DESK_TOP_EXTENTS = (1.460, 0.760, 0.035)

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_desk_top():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=DESK_TOP_EXTENTS, verts=bm.verts)
    
    me = bpy.data.meshes.new("DeskTop")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("DeskTop", me)
    obj.location = DESK_TOP_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("MahoganyDeskTop", (0.28, 0.12, 0.05), roughness=0.32, metallic=0.0)
    obj.data.materials.append(mat)
    
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width = 0.005
    mod.segments = 3
    mod.limit_method = "ANGLE"
    mod.angle_limit = 0.5
    
    return obj
