"""FootCrossbar — structural stretcher and footrest dowel (part module; imported by src/model.py).

Round wooden dowel 26 mm in diameter spanning horizontally between the two rockers with chamfered ends.
Material: natural solid birch, satin clear coat. Instances: 2 (mirror_y).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan center: (0.000, -0.220, 0.070), extents (0.260, 0.026, 0.026)
CROSSBAR_LENGTH = 0.260
CROSSBAR_RADIUS = 0.013

def make_material(name, rgb, roughness=0.45, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_crossbar(name: str, y_pos: float, z_pos: float, mat: bpy.types.Material) -> bpy.types.Object:
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=24,
        radius1=CROSSBAR_RADIUS,
        radius2=CROSSBAR_RADIUS,
        depth=CROSSBAR_LENGTH
    )
    bmesh.ops.rotate(
        bm,
        cent=Vector((0, 0, 0)),
        matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'),
        verts=bm.verts
    )
    bmesh.ops.translate(bm, vec=Vector((0.0, y_pos, z_pos)), verts=bm.verts)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    
    return obj

def build_foot_crossbar():
    mat = make_material("BirchWoodDowel", (0.82, 0.70, 0.52), roughness=0.4, metallic=0.0)
    # Plan states center z=0.070, extents z=0.026 -> z in [0.057, 0.083]
    # At y=±0.220, the rocker is at z_bot = 0.070 * (0.22/0.425)^2 ≈ 0.0188, z_top ≈ 0.0488
    # If the crossbar sits at z=0.060, it touches the rocker top nicely and remains in bbox
    cb0 = create_crossbar("FootCrossbar_0", -0.220, 0.060, mat)
    cb1 = create_crossbar("FootCrossbar_1", 0.220, 0.060, mat)
    return [cb0, cb1]
