"""Common materials and helpers for BenchVise parts."""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

def get_hammered_blue_material():
    mat_name = "IndustrialBlueCastIron"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.12, 0.28, 0.52, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.55
        bsdf.inputs["Metallic"].default_value = 0.3
    return mat

def get_bright_steel_material():
    mat_name = "BrightMachinedSteel"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.85, 0.86, 0.88, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.25
        bsdf.inputs["Metallic"].default_value = 0.95
    return mat

def get_jaw_plate_material():
    mat_name = "HardenedSerratedSteel"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.22, 0.23, 0.25, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.4
        bsdf.inputs["Metallic"].default_value = 0.85
    return mat

def get_screw_material():
    mat_name = "ThreadedZincSteel"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.75, 0.76, 0.78, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.35
        bsdf.inputs["Metallic"].default_value = 0.9
    return mat

def get_chrome_material():
    mat_name = "PolishedChrome"
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        mat = bpy.data.materials.new(mat_name)
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.92, 0.93, 0.95, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.15
        bsdf.inputs["Metallic"].default_value = 1.0
    return mat

def link_object(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj

def apply_all_modifiers(obj):
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        for mod in list(obj.modifiers):
            bpy.ops.object.modifier_apply(modifier=mod.name)
