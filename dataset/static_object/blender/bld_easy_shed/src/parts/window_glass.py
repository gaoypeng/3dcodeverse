"""WindowGlass — Transparent window glazing panes.

Thin flat glass sheets inset behind the window frame grid creating four rectangular transparent lights.
Material: smooth transparent glass with slight sky reflection.
Bbox: center (0.902, 0.150, 1.200) extents (0.008, 0.550, 0.550)
  x in [0.898, 0.906], y in [-0.125, 0.425], z in [0.925, 1.475]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.1, metallic=0.1, alpha=0.3):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if "Alpha" in bsdf.inputs:
        bsdf.inputs["Alpha"].default_value = alpha
    return mat

def build_window_glass() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Glass pane: 0.008 x 0.550 x 0.550
    # Centered at (0.902, 0.150, 1.200)
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.008, 0.550, 0.550), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.902, 0.150, 1.200), verts=bm.verts)
    
    bm.normal_update()
    
    me = bpy.data.meshes.new("WindowGlass")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("WindowGlass", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("GlassGlazing", (0.75, 0.88, 0.95), roughness=0.05, metallic=0.2, alpha=0.35)
    obj.data.materials.append(mat)
    return obj
