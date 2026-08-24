import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

TAILLIGHT_CENTER = (0.026, 0.100, 0.045)
TAILLIGHT_EXTENTS = (0.014, 0.006, 0.014)

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_taillight(name: str, x_pos: float, y_pos: float, z_pos: float):
    """
    Circular button cap (diameter 14 mm, depth 6 mm), facing +Y.
    """
    bm = bmesh.new()
    # Cylinder along Y: radius 0.007 (diameter 0.014), depth 0.006
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.007, radius2=0.007, depth=0.006)
    # Rotate cylinder from Z to Y axis
    bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'X'), verts=bm.verts)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    obj.location = (x_pos, y_pos, z_pos)
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.0015
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj

def build_taillight():
    """Taillight — rear decorative taillights
    Pair of small circular button caps (diameter 14 mm, depth 6 mm) placed symmetrically on the rear bumper curvature.
    Material: matte dark orange painted wood
    """
    # Matte dark orange painted wood
    mat = make_material("TaillightMat", (0.85, 0.35, 0.05), roughness=0.35, metallic=0.0)
    
    t0 = create_taillight("Taillight_0", 0.026, 0.096, 0.045)
    t0.data.materials.append(mat)
    
    t1 = create_taillight("Taillight_1", -0.026, 0.096, 0.045)
    t1.data.materials.append(mat)
    
    return [t0, t1]
