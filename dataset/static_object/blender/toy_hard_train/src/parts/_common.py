"""Shared helper functions and material definitions for ToySteamLocomotive."""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

def link(obj: bpy.types.Object) -> bpy.types.Object:
    """Link into the scene collection."""
    bpy.context.scene.collection.objects.link(obj)
    return obj

def obj_from_bmesh(name: str, bm: bmesh.types.BMesh, location=(0, 0, 0)) -> bpy.types.Object:
    """Create a linked Object from a BMesh."""
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.update()
    o = bpy.data.objects.new(name, me)
    o.location = location
    return link(o)

def make_material(name: str, rgb: tuple, roughness: float = 0.5, metallic: float = 0.0) -> bpy.types.Material:
    """Create or get Principled BSDF material."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

# Standard palette for the toy steam locomotive
def mat_satin_dark_red():
    return make_material("SatinDarkRed", (0.55, 0.05, 0.05), roughness=0.35, metallic=0.0)

def mat_gloss_dark_green():
    return make_material("GlossDarkGreen", (0.02, 0.22, 0.08), roughness=0.25, metallic=0.0)

def mat_polished_brass():
    return make_material("PolishedBrass", (0.92, 0.72, 0.20), roughness=0.20, metallic=0.9)

def mat_bright_red_wheel():
    return make_material("BrightRedWheel", (0.80, 0.04, 0.04), roughness=0.35, metallic=0.1)

def mat_chrome_steel():
    return make_material("ChromeSteel", (0.85, 0.85, 0.88), roughness=0.25, metallic=0.95)

def mat_matte_black():
    return make_material("MatteBlack", (0.05, 0.05, 0.05), roughness=0.70, metallic=0.1)

def mat_matte_coal():
    return make_material("MatteCoal", (0.02, 0.02, 0.02), roughness=0.85, metallic=0.0)

def mat_cast_iron():
    return make_material("CastIron", (0.15, 0.15, 0.16), roughness=0.60, metallic=0.6)
