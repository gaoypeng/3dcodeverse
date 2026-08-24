"""BodyMain — Main curved outer toaster housing.

Smooth rounded rectangular dome shell with continuous 25 mm edge bevels and convex bulging side surfaces, finished in mirror chrome.
Material: polished mirror chrome.
Plan bbox: center (0.000, 0.000, 0.110) extents (0.260, 0.165, 0.160)
  x in [-0.130, 0.130]  y in [-0.0825, 0.0825]  z in [0.030, 0.190]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

BODY_MAIN_CENTER = (0.000, 0.000, 0.110)
BODY_MAIN_EXTENTS = (0.260, 0.165, 0.160)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_body_main():
    bm = bmesh.new()
    
    sx, sy, sz = BODY_MAIN_EXTENTS
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    
    # 2D rounded rectangle profile along XY, then extruded along Z
    r = 0.026
    seg = 12
    
    corners = [
        (hx - r, hy - r, 0, math.pi / 2),
        (-hx + r, hy - r, math.pi / 2, math.pi),
        (-hx + r, -hy + r, math.pi, 3 * math.pi / 2),
        (hx - r, -hy + r, 3 * math.pi / 2, 2 * math.pi)
    ]
    
    verts_bottom = []
    for cx, cy, a_start, a_end in corners:
        for i in range(seg):
            angle = a_start + (a_end - a_start) * (i / seg)
            vx = cx + r * math.cos(angle)
            vy = cy + r * math.sin(angle)
            v = bm.verts.new((vx, vy, -hz))
            verts_bottom.append(v)
            
    bm.faces.new(verts_bottom)
    
    # Extrude up
    res = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
    extruded_verts = [v for v in res["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, sz), verts=extruded_verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("BodyMain")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("BodyMain", me)
    obj.location = BODY_MAIN_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    # Smooth rounding bevel
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.016
    bev.segments = 4
    bev.limit_method = "ANGLE"
    
    # Smooth shading
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    mat = make_material("ChromeBody", (0.95, 0.95, 0.97), roughness=0.08, metallic=1.0)
    obj.data.materials.append(mat)
    
    # Hollow out the bread slots from BodyMain
    for i, y_c in enumerate([0.032, -0.032]):
        cutter_bm = bmesh.new()
        bmesh.ops.create_cube(cutter_bm, size=1.0)
        bmesh.ops.scale(cutter_bm, vec=(0.142, 0.032, 0.120), verts=cutter_bm.verts)
        cutter_me = bpy.data.meshes.new(f"TempCutter_{i}")
        cutter_bm.to_mesh(cutter_me)
        cutter_bm.free()
        
        cutter_obj = bpy.data.objects.new(f"TempCutter_{i}", cutter_me)
        cutter_obj.location = (0.0, y_c, BODY_MAIN_CENTER[2] + 0.030)
        bpy.context.scene.collection.objects.link(cutter_obj)
        
        bool_mod = obj.modifiers.new(f"SlotCut_{i}", "BOOLEAN")
        bool_mod.operation = "DIFFERENCE"
        bool_mod.object = cutter_obj
        
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=bool_mod.name)
            
        bpy.data.objects.remove(cutter_obj, do_unlink=True)
        bpy.data.meshes.remove(cutter_me)
        
    return obj
