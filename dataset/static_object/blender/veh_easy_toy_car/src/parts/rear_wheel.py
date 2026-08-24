import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

REAR_WHEEL_CENTER = (0.054, 0.065, 0.035)
REAR_WHEEL_EXTENTS = (0.024, 0.070, 0.070)

def make_material(name, rgb, roughness=0.55, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_wheel_mesh(name: str, x_pos: float, y_pos: float, z_pos: float, is_right: bool):
    """
    Creates a chunky toddler toy wheel:
    - Cylinder oriented along X axis (diameter 0.070 m, width 0.024 m)
    - Axle hole through the center (r=4.1 mm)
    - Shallow concentric hub cap detail on the outer face
    - Soft bevel along outer and inner rim
    """
    bm = bmesh.new()
    
    r = 0.035
    w = 0.024
    segments = 32
    
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=r, radius2=r, depth=w)
    bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'), verts=bm.verts)
    
    bm_hole = bmesh.new()
    bmesh.ops.create_cone(bm_hole, cap_ends=True, segments=24, radius1=0.0042, radius2=0.0042, depth=0.035)
    bmesh.ops.rotate(bm_hole, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'), verts=bm_hole.verts)
    
    hub_sign = 1.0 if is_right else -1.0
    bm_hub = bmesh.new()
    bmesh.ops.create_cone(bm_hub, cap_ends=True, segments=24, radius1=0.018, radius2=0.018, depth=0.004)
    bmesh.ops.rotate(bm_hub, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'), verts=bm_hub.verts)
    bmesh.ops.translate(bm_hub, vec=(hub_sign * (w * 0.5), 0, 0), verts=bm_hub.verts)
    
    me_hole = bpy.data.meshes.new("_wr_hole")
    bm_hole.to_mesh(me_hole); bm_hole.free()
    hole_obj = bpy.data.objects.new("_wr_hole", me_hole)
    hole_obj.location = (x_pos, y_pos, z_pos)
    bpy.context.scene.collection.objects.link(hole_obj)
    
    me_hub = bpy.data.meshes.new("_wr_hub")
    bm_hub.to_mesh(me_hub); bm_hub.free()
    hub_obj = bpy.data.objects.new("_wr_hub", me_hub)
    hub_obj.location = (x_pos, y_pos, z_pos)
    bpy.context.scene.collection.objects.link(hub_obj)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    
    obj = bpy.data.objects.new(name, me)
    obj.location = (x_pos, y_pos, z_pos)
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.003
    bev.segments = 3
    bev.limit_method = "ANGLE"
    bev.angle_limit = math.radians(60)
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier="Bevel")
        
    cut_hub = obj.modifiers.new("HubDish", "BOOLEAN")
    cut_hub.object = hub_obj
    cut_hub.operation = "DIFFERENCE"
    cut_hub.solver = "EXACT"
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier="HubDish")
        
    cut_axle = obj.modifiers.new("AxleHole", "BOOLEAN")
    cut_axle.object = hole_obj
    cut_axle.operation = "DIFFERENCE"
    cut_axle.solver = "EXACT"
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier="AxleHole")
        
    for tool_obj in [hole_obj, hub_obj]:
        t_mesh = tool_obj.data.name
        bpy.data.objects.remove(tool_obj, do_unlink=True)
        if t_mesh in bpy.data.meshes:
            bpy.data.meshes.remove(bpy.data.meshes[t_mesh], do_unlink=True)
            
    bpy.context.view_layer.update()
    bb = [Vector(v) for v in obj.bound_box]
    min_x = min(v.x for v in bb); max_x = max(v.x for v in bb)
    min_y = min(v.y for v in bb); max_y = max(v.y for v in bb)
    min_z = min(v.z for v in bb); max_z = max(v.z for v in bb)
    
    scale_x = REAR_WHEEL_EXTENTS[0] / (max_x - min_x)
    scale_y = REAR_WHEEL_EXTENTS[1] / (max_y - min_y)
    scale_z = REAR_WHEEL_EXTENTS[2] / (max_z - min_z)
    
    bm_adj = bmesh.new()
    bm_adj.from_mesh(obj.data)
    for v in bm_adj.verts:
        v.co.x = (v.co.x) * scale_x
        v.co.y = (v.co.y) * scale_y
        v.co.z = (v.co.z) * scale_z
    bm_adj.to_mesh(obj.data)
    bm_adj.free()
    obj.data.update()
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj

def build_rear_wheel():
    """RearWheel — rear rolling wheel
    Material: natural oiled beech wood with matte finish
    """
    mat = make_material("BeechWoodMatRear", (0.86, 0.72, 0.52), roughness=0.5, metallic=0.0)
    
    w0 = create_wheel_mesh("RearWheel_0", 0.054, 0.065, 0.035, is_right=True)
    w0.data.materials.append(mat)
    
    w1 = create_wheel_mesh("RearWheel_1", -0.054, 0.065, 0.035, is_right=False)
    w1.data.materials.append(mat)
    
    return [w0, w1]
