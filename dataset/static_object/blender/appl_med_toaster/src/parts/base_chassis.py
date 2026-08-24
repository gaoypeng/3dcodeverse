"""BaseChassis — Molded heat-resistant plastic base plate supporting internal electronics and body shell.

Rounded rectangular black plastic base tray with smoothed perimeter fillet (r=0.012 m), recessed inset for chrome body and lower front lip.
Material: matte dark grey bakelite plastic.
Plan bbox: center (0.000, 0.000, 0.022) extents (0.270, 0.176, 0.024)
  x in [-0.135, 0.135]  y in [-0.088, 0.088]  z in [0.010, 0.034]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

BASE_CHASSIS_CENTER = (0.000, 0.000, 0.022)
BASE_CHASSIS_EXTENTS = (0.270, 0.176, 0.024)
BASE_CHASSIS_MIN = (-0.135, -0.088, 0.010)
BASE_CHASSIS_MAX = (0.135, 0.088, 0.034)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_rounded_rect_box(bm, sx, sy, sz, r, seg=8):
    """Creates a rounded rectangle extruded box along Z, centered at local (0,0,0)."""
    # 2D profile in XY plane, then extrude in Z
    hx = sx / 2.0
    hy = sy / 2.0
    hz = sz / 2.0
    r = min(r, hx - 0.001, hy - 0.001)
    
    # 4 corner centers
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
            
    # Bottom face
    bm.faces.new(verts_bottom)
    
    # Extrude up to +hz
    res = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
    extruded_verts = [v for v in res["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, sz), verts=extruded_verts)
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

def build_base_chassis():
    bm = bmesh.new()
    # Main base plate rounded rectangle
    sx, sy, sz = BASE_CHASSIS_EXTENTS
    create_rounded_rect_box(bm, sx, sy, sz, r=0.016, seg=10)
    
    me = bpy.data.meshes.new("BaseChassis")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("BaseChassis", me)
    obj.location = BASE_CHASSIS_CENTER
    bpy.context.scene.collection.objects.link(obj)
    
    # Add subtle bevel modifier for realistic edges
    bev = obj.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = "ANGLE"
    
    mat = make_material("BaseBakelite", (0.12, 0.12, 0.13), roughness=0.6, metallic=0.05)
    obj.data.materials.append(mat)
    return obj
