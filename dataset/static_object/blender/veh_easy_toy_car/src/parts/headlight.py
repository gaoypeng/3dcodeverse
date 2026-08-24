import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

HEADLIGHT_CENTER = (0.026, -0.100, 0.045)
HEADLIGHT_EXTENTS = (0.018, 0.006, 0.018)

def make_material(name, rgb, roughness=0.3, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_headlight(name: str, x_pos: float, y_pos: float, z_pos: float):
    """
    Flattened circular dome cap (diameter 18 mm, depth 6 mm), facing -Y.
    """
    bm = bmesh.new()
    # Cylinder along Y: radius 0.009 (diameter 0.018), depth 0.006
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.009, radius2=0.009, depth=0.006)
    # Rotate cylinder from Z to Y axis
    bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'), verts=bm.verts)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    obj.location = (x_pos, y_pos, z_pos)
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj

def build_headlight():
    """Headlight — front decorative headlights
    Pair of flattened circular dome caps (diameter 18 mm, depth 6 mm) embedded flush onto the curved front nose surface.
    Material: warm off-white painted wood
    """
    mat = make_material("HeadlightMat", (0.95, 0.93, 0.88), roughness=0.25, metallic=0.0)
    
    h0 = create_headlight("Headlight_0", 0.026, -0.098, 0.045)
    h0.data.materials.append(mat)
    
    h1 = create_headlight("Headlight_1", -0.026, -0.098, 0.045)
    h1.data.materials.append(mat)
    
    return [h0, h1]
