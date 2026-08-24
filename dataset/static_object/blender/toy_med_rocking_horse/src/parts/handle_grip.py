"""HandleGrip — hand grip dowel for the rider (part module; imported by src/model.py).

Horizontal cylinder (22 mm diameter, 240 mm total width) passing through the upper neck with rounded spherical end knobs (32 mm diameter).
Material: polished beech dowel, smooth finish. Instances: 1.
Plan bbox: center (0.000, -0.240, 0.530) extents (0.240, 0.032, 0.032)
  x in [-0.120, 0.120]  y in [-0.256, -0.224]  z in [0.514, 0.546]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_handle_grip():
    # HorseNeckHead half-width = 0.045 (surface at x = ±0.045).
    # To weld with ≤ 2 mm overlap into the neck surface without heavy volume interpenetration error,
    # we create the right grip projecting from x = 0.0435 (1.5mm overlap inside neck) to 0.120 (knob),
    # and the left grip projecting from x = -0.0435 to -0.120.
    # Total extents along X: -0.120 to +0.120 (0.240m).
    bm = bmesh.new()
    
    # Right grip shaft: spans x = 0.0435 to x = 0.104 (length = 0.0605, center = 0.07375)
    shaft_len = 0.104 - 0.0435
    shaft_center_x = (0.104 + 0.0435) / 2.0
    
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=24,
        radius1=0.011,
        radius2=0.011,
        depth=shaft_len
    )
    bmesh.ops.rotate(
        bm,
        cent=Vector((0, 0, 0)),
        matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'),
        verts=bm.verts
    )
    bmesh.ops.translate(bm, vec=Vector((shaft_center_x, 0, 0)), verts=bm.verts)
    
    # Right knob at x = 0.104 (radius 0.016 -> max x = 0.120)
    bm_knob_r = bmesh.new()
    bmesh.ops.create_uvsphere(bm_knob_r, u_segments=20, v_segments=12, radius=0.016)
    bmesh.ops.translate(bm_knob_r, vec=Vector((0.104, 0, 0)), verts=bm_knob_r.verts)
    
    # Left shaft: spans x = -0.104 to x = -0.0435 (length = 0.0605, center = -0.07375)
    bm_shaft_l = bmesh.new()
    bmesh.ops.create_cone(
        bm_shaft_l,
        cap_ends=True,
        cap_tris=False,
        segments=24,
        radius1=0.011,
        radius2=0.011,
        depth=shaft_len
    )
    bmesh.ops.rotate(
        bm_shaft_l,
        cent=Vector((0, 0, 0)),
        matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'),
        verts=bm_shaft_l.verts
    )
    bmesh.ops.translate(bm_shaft_l, vec=Vector((-shaft_center_x, 0, 0)), verts=bm_shaft_l.verts)
    
    # Left knob at x = -0.104 (radius 0.016 -> min x = -0.120)
    bm_knob_l = bmesh.new()
    bmesh.ops.create_uvsphere(bm_knob_l, u_segments=20, v_segments=12, radius=0.016)
    bmesh.ops.translate(bm_knob_l, vec=Vector((-0.104, 0, 0)), verts=bm_knob_l.verts)
    
    # Merge sub-bmeshes
    for sub in [bm_knob_r, bm_shaft_l, bm_knob_l]:
        offset = len(bm.verts)
        for v in sub.verts:
            bm.verts.new(v.co)
        bm.verts.ensure_lookup_table()
        for f in sub.faces:
            bm.faces.new([bm.verts[v.index + offset] for v in f.verts])
        sub.free()
        
    # Translate whole handle grip to (0.000, -0.240, 0.530)
    bmesh.ops.translate(bm, vec=Vector((0.000, -0.240, 0.530)), verts=bm.verts)
    
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    me = bpy.data.meshes.new("HandleGrip")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("HandleGrip", me)
    bpy.context.scene.collection.objects.link(obj)
    mat = make_material("BeechHandle", (0.88, 0.76, 0.58), roughness=0.3, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
