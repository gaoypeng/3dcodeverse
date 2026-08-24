"""TopTrim — Top chrome slot plate frame.

Recessed brushed metal top cover plate with dual rounded rectangular cutouts for the bread slots (each opening 0.135 m long by 0.028 m wide).
Material: brushed stainless steel.  Instances: 1.  Attaches to: BodyMain.
Plan bbox: center (0.000, 0.000, 0.188) extents (0.230, 0.140, 0.008)
  x in [-0.115, 0.115]  y in [-0.070, 0.070]  z in [0.184, 0.192]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

TOP_TRIM_CENTER = (0.000, 0.000, 0.188)
TOP_TRIM_EXTENTS = (0.230, 0.140, 0.008)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_rounded_rect_box(bm, sx, sy, sz, r, seg=8):
    hx = sx / 2.0
    hy = sy / 2.0
    hz = sz / 2.0
    r = min(r, hx - 0.001, hy - 0.001)
    
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
    
    res = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
    extruded_verts = [v for v in res["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, sz), verts=extruded_verts)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

def build_top_trim():
    bm = bmesh.new()
    sx, sy, sz = TOP_TRIM_EXTENTS
    create_rounded_rect_box(bm, sx, sy, sz, r=0.020, seg=8)
    
    me = bpy.data.meshes.new("TopTrim")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("TopTrim", me)
    obj.location = TOP_TRIM_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.001
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    mat = make_material("BrushedStainlessTop", (0.88, 0.88, 0.90), roughness=0.25, metallic=1.0)
    obj.data.materials.append(mat)
    
    # Cut dual slot openings through TopTrim
    for i, y_c in enumerate([0.032, -0.032]):
        cutter_bm = bmesh.new()
        create_rounded_rect_box(cutter_bm, 0.138, 0.028, 0.020, r=0.005, seg=6)
        cutter_me = bpy.data.meshes.new(f"TopTrimCutter_{i}")
        cutter_bm.to_mesh(cutter_me)
        cutter_bm.free()
        
        cutter_obj = bpy.data.objects.new(f"TopTrimCutter_{i}", cutter_me)
        cutter_obj.location = (0.0, y_c, TOP_TRIM_CENTER[2])
        bpy.context.scene.collection.objects.link(cutter_obj)
        
        bool_mod = obj.modifiers.new(f"SlotTrimCut_{i}", "BOOLEAN")
        bool_mod.operation = "DIFFERENCE"
        bool_mod.object = cutter_obj
        
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
            bpy.ops.object.modifier_apply(modifier=bool_mod.name)
            
        bpy.data.objects.remove(cutter_obj, do_unlink=True)
        bpy.data.meshes.remove(cutter_me)
        
    return obj
