"""RoofTrim — Fascia boards and bargeboards along eaves and gable edges.

Timber fascia boards running along both side eaves and decorative bargeboards tracing the front and rear gable pitch edges,
capped with a central ridge cap strip.
Material: painted white/cream exterior timber trim.
Bbox: center (0.000, 0.000, 2.080) extents (2.040, 2.240, 0.560)
  x in [-1.020, 1.020], y in [-1.120, 1.120], z in [1.800, 2.360]
"""
import bpy
import bmesh
from mathutils import Vector

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_roof_trim() -> bpy.types.Object:
    bm = bmesh.new()
    
    # 1. Central Ridge Cap Strip (along Y in [-1.120, 1.120], at apex z in [2.348, 2.360], width x in [-0.050, 0.050])
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.100, 2.240, 0.016), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(0.0, 0.0, 2.352), verts=bm.verts[-8:])
    
    # 2. Side Eaves Fascia Boards (along Y in [-1.120, 1.120])
    # Positioned at outer eaves x = 1.010, z = 1.820
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.020, 2.240, 0.040), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(1.010, 0.0, 1.820), verts=bm.verts[-8:])
    
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.020, 2.240, 0.040), verts=bm.verts[-8:])
    bmesh.ops.translate(bm, vec=(-1.010, 0.0, 1.820), verts=bm.verts[-8:])
    
    # 3. Bargeboards (front and rear gable eaves)
    for y_min, y_max in [(-1.120, -1.100), (1.100, 1.120)]:
        # Right side bargeboard:
        v0 = bm.verts.new(Vector((0.000, y_min, 2.360)))
        v1 = bm.verts.new(Vector((1.020, y_min, 1.850)))
        v2 = bm.verts.new(Vector((1.020, y_min, 1.800)))
        v3 = bm.verts.new(Vector((0.000, y_min, 2.300)))
        v4 = bm.verts.new(Vector((0.000, y_max, 2.360)))
        v5 = bm.verts.new(Vector((1.020, y_max, 1.850)))
        v6 = bm.verts.new(Vector((1.020, y_max, 1.800)))
        v7 = bm.verts.new(Vector((0.000, y_max, 2.300)))
        
        bm.faces.new((v0, v1, v5, v4))
        bm.faces.new((v1, v2, v6, v5))
        bm.faces.new((v2, v3, v7, v6))
        bm.faces.new((v3, v0, v4, v7))
        bm.faces.new((v0, v3, v2, v1))
        bm.faces.new((v4, v5, v6, v7))
        
        # Left side bargeboard:
        u0 = bm.verts.new(Vector(( 0.000, y_min, 2.360)))
        u1 = bm.verts.new(Vector((-1.020, y_min, 1.850)))
        u2 = bm.verts.new(Vector((-1.020, y_min, 1.800)))
        u3 = bm.verts.new(Vector(( 0.000, y_min, 2.300)))
        u4 = bm.verts.new(Vector(( 0.000, y_max, 2.360)))
        u5 = bm.verts.new(Vector((-1.020, y_max, 1.850)))
        u6 = bm.verts.new(Vector((-1.020, y_max, 1.800)))
        u7 = bm.verts.new(Vector(( 0.000, y_max, 2.300)))
        
        bm.faces.new((u0, u4, u5, u1))
        bm.faces.new((u1, u5, u6, u2))
        bm.faces.new((u2, u6, u7, u3))
        bm.faces.new((u3, u7, u4, u0))
        bm.faces.new((u0, u1, u2, u3))
        bm.faces.new((u4, u7, u6, u5))

    bm.normal_update()
    
    me = bpy.data.meshes.new("RoofTrim")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("RoofTrim", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("PaintedTrimWhite", (0.92, 0.90, 0.85), roughness=0.45)
    obj.data.materials.append(mat)
    return obj
