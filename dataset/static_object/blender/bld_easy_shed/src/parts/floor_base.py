"""FloorBase — Foundation platform and floor deck.

Sturdy rectangular wooden base platform constructed from treated timber joists and plank decking, elevating the shed slightly off the ground.
Material: treated dark pine wood planks.
Bbox: center (0.000, 0.000, 0.050) extents (1.820, 2.020, 0.100)
  x in [-0.910, 0.910], y in [-1.010, 1.010], z in [0.000, 0.100]
"""
import bpy
import bmesh

def make_material(name, rgb, roughness=0.7, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_floor_base() -> bpy.types.Object:
    bm = bmesh.new()
    
    # Base dimensions: 1.82 x 2.02 x 0.10, from z=0 to z=0.10
    # Planks running along Y (or X), with slight gaps/bevels or solid perimeter rim joist + deck planks
    # Main platform block:
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(1.820, 2.020, 0.100), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 0.050), verts=bm.verts)
    
    me = bpy.data.meshes.new("FloorBase")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("FloorBase", me)
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", 'BEVEL')
    bev.width = 0.004
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    mat = make_material("DarkPineWood", (0.28, 0.18, 0.10), roughness=0.75)
    obj.data.materials.append(mat)
    return obj
