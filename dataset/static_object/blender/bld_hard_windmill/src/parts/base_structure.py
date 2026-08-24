"""BaseStructure — Octagonal masonry ground floor base (part module; imported by src/model.py).

Solid octagonal brick/stone plinth, outer diameter 4.20 m at base tapering slightly to 3.90 m at top, height 2.00 m from z=0.0 to z=2.00 m. Beveled vertical corner edges.
Material: weathered red Dutch brick masonry. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, 0.000, 1.000) extents (4.200, 4.200, 2.000)
# x in [-2.100, 2.100], y in [-2.100, 2.100], z in [0.000, 2.000]
BASE_STRUCTURE_CENTER = (0.000, 0.000, 1.000)
BASE_STRUCTURE_EXTENTS = (4.200, 4.200, 2.000)

def make_material(name, rgb, roughness=0.7, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_base_structure() -> bpy.types.Object:
    """Solid octagonal brick plinth tapering from 4.2m diameter at base to 3.9m at top, z from 0 to 2.0m."""
    bm = bmesh.new()
    
    # Base plinth: z from -1.000 to +1.000 (world z: 0.0 to 2.000)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=8,
        radius1=2.100,
        radius2=1.950,
        depth=2.000
    )
    
    # Foundation stone course trim (z from -1.000 to -0.800)
    res_bot = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=8,
        radius1=2.100,
        radius2=2.080,
        depth=0.200
    )
    for v in res_bot['verts']:
        v.co.z -= 0.900

    # Stone stringer/cornice at the top of the base (z from 0.92 to 1.00)
    res_top = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=8,
        radius1=2.000,
        radius2=1.950,
        depth=0.080
    )
    for v in res_top['verts']:
        v.co.z += 0.960

    me = bpy.data.meshes.new("BaseStructure")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("BaseStructure", me)
    obj.location = (0.0, 0.0, 1.000)
    bpy.context.scene.collection.objects.link(obj)
    
    # Material: weathered red Dutch brick masonry
    mat_brick = make_material("DutchBrickMat", (0.58, 0.22, 0.15), roughness=0.85)
    obj.data.materials.append(mat_brick)
    
    return obj
