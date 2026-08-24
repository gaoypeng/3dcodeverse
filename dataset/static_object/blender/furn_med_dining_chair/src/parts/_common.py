"""Shared material and geometry helpers for MidCenturyDiningChair."""
import bpy
import bmesh
from mathutils import Vector, Matrix

def get_wood_material():
    """Shared teak wood material."""
    name = "TeakWood"
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    # Warm oiled teak wood color
    bsdf.inputs["Base Color"].default_value = (0.52, 0.30, 0.14, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.45
    bsdf.inputs["Metallic"].default_value = 0.0
    return mat

def link(obj: bpy.types.Object) -> bpy.types.Object:
    """Link into the scene collection."""
    bpy.context.scene.collection.objects.link(obj)
    return obj

def obj_from_bmesh(name: str, bm: bmesh.types.BMesh, location=(0, 0, 0)) -> bpy.types.Object:
    """bmesh -> mesh -> object, freed and linked."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.update()
    o = bpy.data.objects.new(name, me)
    o.location = location
    return link(o)
