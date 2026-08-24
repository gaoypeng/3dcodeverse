import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

CAR_BODY_CENTER = (0.000, 0.000, 0.062)
CAR_BODY_EXTENTS = (0.088, 0.204, 0.096)

def make_material(name, rgb, roughness=0.2, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_car_body():
    """CarBody — main unified vehicle body and cabin
    Smooth, one-piece contoured car body with a bulbous cabin top, rounded front hood, and tapered rear.
    Material: glossy bright red painted wood
    """
    mat = make_material("CarBodyMat", (0.85, 0.05, 0.05), roughness=0.2, metallic=0.0)

    bm = bmesh.new()
    
    # 13 Y stations from y=-0.102 to y=+0.102 (front is -Y, rear is +Y)
    # (y, wb, wm, wt, zb, zw, zt, is_cabin)
    stations = [
        (-0.102, 0.026, 0.034, 0.026, 0.022, 0.042, 0.050, False), # front nose tip
        (-0.088, 0.036, 0.042, 0.034, 0.016, 0.045, 0.056, False), # front hood
        (-0.065, 0.038, 0.043, 0.035, 0.014, 0.048, 0.058, False), # over front wheels
        (-0.040, 0.040, 0.044, 0.038, 0.014, 0.050, 0.064, False), # base of windshield
        (-0.020, 0.042, 0.044, 0.034, 0.014, 0.052, 0.098, True),  # windshield
        ( 0.000, 0.042, 0.044, 0.035, 0.014, 0.052, 0.110, True),  # cabin peak
        ( 0.020, 0.042, 0.044, 0.034, 0.014, 0.052, 0.104, True),  # cabin rear
        ( 0.040, 0.042, 0.044, 0.037, 0.014, 0.050, 0.072, False), # rear window base
        ( 0.065, 0.038, 0.043, 0.035, 0.014, 0.048, 0.058, False), # over rear wheels
        ( 0.088, 0.036, 0.042, 0.032, 0.016, 0.045, 0.054, False), # rear deck
        ( 0.102, 0.026, 0.034, 0.024, 0.022, 0.040, 0.048, False), # rear bumper tip
    ]
    
    loops = []
    for y, wb, wm, wt, zb, zw, zt, is_cab in stations:
        z_mid_low = (zb + zw) * 0.5
        z_mid_high = (zw + zt) * 0.5
        wt_eff = wt * (0.75 if is_cab else 0.85)
        
        pts = [
            ( 0.0,         zb),                 # 0 bottom center
            ( wb * 0.7,    zb + (zw - zb)*0.1), # 1 bottom right
            ( wm,          z_mid_low),          # 2 lower right
            ( wm,          zw),                 # 3 waist right
            ( wt,          z_mid_high),         # 4 upper right
            ( wt_eff,      zt - (zt - zw)*0.1), # 5 top right
            ( 0.0,         zt),                 # 6 top center
            (-wt_eff,      zt - (zt - zw)*0.1), # 7 top left
            (-wt,          z_mid_high),         # 8 upper left
            (-wm,          zw),                 # 9 waist left
            (-wm,          z_mid_low),          # 10 lower left
            (-wb * 0.7,    zb + (zw - zb)*0.1), # 11 bottom left
        ]
        v_loop = []
        for x, z in pts:
            v_loop.append(bm.verts.new((x, y, z)))
        loops.append(v_loop)
        
    n_pts = len(loops[0])
    for i in range(len(loops) - 1):
        l1 = loops[i]
        l2 = loops[i + 1]
        for j in range(n_pts):
            j_next = (j + 1) % n_pts
            bm.faces.new([l1[j], l2[j], l2[j_next], l1[j_next]])
            
    # Close front cap (at station 0, y=-0.102)
    v_front_center = bm.verts.new((0, stations[0][0] - 0.005, (stations[0][4] + stations[0][6]) * 0.5))
    for j in range(n_pts):
        j_next = (j + 1) % n_pts
        bm.faces.new([v_front_center, loops[0][j_next], loops[0][j]])
        
    # Close rear cap (at station -1, y=+0.102)
    v_rear_center = bm.verts.new((0, stations[-1][0] + 0.005, (stations[-1][4] + stations[-1][6]) * 0.5))
    for j in range(n_pts):
        j_next = (j + 1) % n_pts
        bm.faces.new([v_rear_center, loops[-1][j], loops[-1][j_next]])

    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    me = bpy.data.meshes.new("CarBody")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("CarBody", me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    
    sub = obj.modifiers.new("Subsurf", "SUBSURF")
    sub.levels = 2
    sub.render_levels = 2
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier="Subsurf")

    # Rescale CarBody to exact target bbox
    bpy.context.view_layer.update()
    bb = [Vector(v) for v in obj.bound_box]
    min_x = min(v.x for v in bb); max_x = max(v.x for v in bb)
    min_y = min(v.y for v in bb); max_y = max(v.y for v in bb)
    min_z = min(v.z for v in bb); max_z = max(v.z for v in bb)
    
    curr_cx = (min_x + max_x) * 0.5
    curr_cy = (min_y + max_y) * 0.5
    curr_cz = (min_z + max_z) * 0.5
    curr_sx = max_x - min_x
    curr_sy = max_y - min_y
    curr_sz = max_z - min_z
    
    target_sx, target_sy, target_sz = CAR_BODY_EXTENTS
    target_cx, target_cy, target_cz = CAR_BODY_CENTER
    
    scale_x = target_sx / curr_sx if curr_sx > 0 else 1.0
    scale_y = target_sy / curr_sy if curr_sy > 0 else 1.0
    scale_z = target_sz / curr_sz if curr_sz > 0 else 1.0
    
    bm_adj = bmesh.new()
    bm_adj.from_mesh(obj.data)
    for v in bm_adj.verts:
        v.co.x = target_cx + (v.co.x - curr_cx) * scale_x
        v.co.y = target_cy + (v.co.y - curr_cy) * scale_y
        v.co.z = target_cz + (v.co.z - curr_cz) * scale_z
        
    bm_adj.to_mesh(obj.data)
    bm_adj.free()

    # Axle clearance holes via boolean cylinders (radius 5.5 mm)
    bm_cut = bmesh.new()
    # Front axle hole (at y = -0.065, z = 0.035)
    bmesh.ops.create_cone(bm_cut, cap_ends=True, segments=32, radius1=0.0055, radius2=0.0055, depth=0.15)
    bmesh.ops.rotate(bm_cut, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'), verts=bm_cut.verts)
    bmesh.ops.translate(bm_cut, vec=(0, -0.065, 0.035), verts=bm_cut.verts)
    
    # Rear axle hole (at y = +0.065, z = 0.035)
    v_start = len(bm_cut.verts)
    bmesh.ops.create_cone(bm_cut, cap_ends=True, segments=32, radius1=0.0055, radius2=0.0055, depth=0.15)
    new_v = bm_cut.verts[v_start:]
    bmesh.ops.rotate(bm_cut, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'), verts=new_v)
    bmesh.ops.translate(bm_cut, vec=(0, 0.065, 0.035), verts=new_v)

    me_c = bpy.data.meshes.new("_cut")
    bm_cut.to_mesh(me_c); bm_cut.free()
    c_obj = bpy.data.objects.new("_cut", me_c)
    bpy.context.scene.collection.objects.link(c_obj)

    b_mod = obj.modifiers.new("AxleCuts", "BOOLEAN")
    b_mod.object = c_obj
    b_mod.operation = "DIFFERENCE"
    b_mod.solver = "EXACT"
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier="AxleCuts")

    bpy.data.objects.remove(c_obj, do_unlink=True)
    bpy.data.meshes.remove(me_c, do_unlink=True)

    for poly in obj.data.polygons:
        poly.use_smooth = True

    return obj
