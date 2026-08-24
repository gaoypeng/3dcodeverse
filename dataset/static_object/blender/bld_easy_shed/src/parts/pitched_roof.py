"""PitchedRoof — Two-panel pitched roof structure with overhangs.

Dual sloped roof panels joined along the center ridge at Z=2.35 m, sloping down to Z=1.80 m with 0.12 m side eaves overhang and 0.12 m front/back gable overhang.
Material: dark mineral roofing felt/shingle texture.  Instances: 2 (mirror_x).
Bbox: center (0.000, 0.000, 2.075) extents (2.040, 2.240, 0.550)
  x in [-1.020, 1.020], y in [-1.120, 1.120], z in [1.800, 2.350]
"""
import bpy
import bmesh
from mathutils import Vector

def make_material(name, rgb, roughness=0.85, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def _build_roof_panel(name: str, side: float, mat) -> bpy.types.Object:
    # side = +1 for right panel (+X), side = -1 for left panel (-X)
    # y in [-1.100, 1.100] (recessed slightly under bargeboards at y = [-1.120, 1.120])
    # x in [0.000, side * 1.000] (recessed slightly under side fascia at x = 1.010)
    # z from ridge 2.348 down to eave 1.805
    bm = bmesh.new()
    
    y_min = -1.100
    y_max =  1.100
    
    # Vertices at y_min:
    v0 = bm.verts.new(Vector((0.000,          y_min, 2.348)))
    v1 = bm.verts.new(Vector((side * 1.000,   y_min, 1.835)))
    v2 = bm.verts.new(Vector((side * 1.000,   y_min, 1.805)))
    v3 = bm.verts.new(Vector((0.000,          y_min, 2.318)))
    
    # Vertices at y_max:
    v4 = bm.verts.new(Vector((0.000,          y_max, 2.348)))
    v5 = bm.verts.new(Vector((side * 1.000,   y_max, 1.835)))
    v6 = bm.verts.new(Vector((side * 1.000,   y_max, 1.805)))
    v7 = bm.verts.new(Vector((0.000,          y_max, 2.318)))
    
    if side > 0:
        bm.faces.new((v0, v1, v5, v4))
        bm.faces.new((v1, v2, v6, v5))
        bm.faces.new((v2, v3, v7, v6))
        bm.faces.new((v3, v0, v4, v7))
        bm.faces.new((v0, v3, v2, v1))
        bm.faces.new((v4, v5, v6, v7))
    else:
        bm.faces.new((v0, v4, v5, v1))
        bm.faces.new((v1, v5, v6, v2))
        bm.faces.new((v2, v6, v7, v3))
        bm.faces.new((v3, v7, v4, v0))
        bm.faces.new((v0, v1, v2, v3))
        bm.faces.new((v4, v7, v6, v5))
        
    bm.normal_update()
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj

def build_pitched_roof() -> list[bpy.types.Object]:
    mat = make_material("RoofFeltShingle", (0.22, 0.23, 0.24), roughness=0.9)
    r0 = _build_roof_panel("PitchedRoof_0",  1.0, mat)
    r1 = _build_roof_panel("PitchedRoof_1", -1.0, mat)
    return [r0, r1]
