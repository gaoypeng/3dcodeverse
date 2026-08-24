"""BaseFeet — four non-slip rubber feet supporting the machine.

Short cylindrical rubber feet (diameter 0.03 m, height 0.015 m) set under the four corners of the base.
Material: matte black rubber.
"""
import bpy
import bmesh
from mathutils import Vector

def make_material(name, rgb, roughness=0.8, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_base_feet():
    """Build BaseFeet_0 .. BaseFeet_3 as cylinders."""
    mat = make_material("RubberFeetMat", (0.05, 0.05, 0.05), roughness=0.85, metallic=0.0)
    
    positions = [
        (0.090, 0.050, 0.0075),
        (-0.090, 0.050, 0.0075),
        (-0.090, -0.050, 0.0075),
        (0.090, -0.050, 0.0075),
    ]
    
    objs = []
    for i, (cx, cy, cz) in enumerate(positions):
        bm = bmesh.new()
        # Diameter 0.03 -> radius 0.015, height 0.015
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            cap_tris=False,
            segments=24,
            radius1=0.015,
            radius2=0.014,  # slight taper
            depth=0.015
        )
        me = bpy.data.meshes.new(f"BaseFeet_{i}")
        bm.to_mesh(me)
        bm.free()
        
        obj = bpy.data.objects.new(f"BaseFeet_{i}", me)
        obj.location = (cx, cy, cz)
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(mat)
        objs.append(obj)
        
    return objs
