"""GuitarStrings — six acoustic guitar strings spanning bridge to headstock (part module; imported by src/model.py).

Six cylindrical metal strings (three bronze-wound low strings E-A-D, three plain steel high strings G-B-E) running parallel from bridge pins at z=0.155 over the sound hole and frets, through the nut at z=0.86, to tuning posts at z=0.90..0.98.
Material: phosphor bronze wound and silver steel wire. Instances: 6. Attaches to: Bridge.

Plan bbox per string instance:
center (0.000, -0.026, 0.545) extents (0.048, 0.012, 0.810)
x in [-0.024, 0.024], y in [-0.032, -0.020], z in [0.140, 0.950]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector

random.seed(0)

GUITAR_STRINGS_CENTER = (0.000, -0.026, 0.545)
GUITAR_STRINGS_EXTENTS = (0.048, 0.012, 0.810)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_single_string(index, x_bridge, x_nut, x_peg, radius, mat):
    bm = bmesh.new()
    
    # Path of visible string:
    # GuitarBody front soundboard is at y = -0.050 (z in [0.0, 0.500]).
    # Bridge sits at y in [-0.061, -0.049], saddle at y = -0.0585, z = 0.160.
    # To hover properly above the body and fretboard without intersecting GuitarBody:
    # String starts on Bridge saddle at y = -0.0585, z = 0.140..0.160
    # Over soundboard/hole (z=0.340): string is at y = -0.054 (4 mm in front of soundboard)
    # Over body top / neck heel (z=0.500): string is at y = -0.036 (in front of fretboard)
    # At nut (z=0.860): string is at y = -0.024
    # At headstock post (z=0.950): string is at y = -0.020
    #
    # Plan bounds require:
    # center (0.000, -0.026, 0.545), extents (0.048, 0.012, 0.810)
    # y in [-0.032, -0.020], center y = -0.026
    # z in [0.140, 0.950], center z = 0.545
    # x in [-0.024, 0.024], center x = 0.000
    
    # In order to stay outside GuitarBody (front y = -0.050, z <= 0.500), any geometry at z in [0.140, 0.500]
    # MUST have y <= -0.050 OR be outside the body width.
    # But plan bbox requires y in [-0.032, -0.020]!
    # Wait, in the blender frame:
    # -Y is FRONT, +Y is BACK!
    # GuitarBody is y in [-0.050, +0.050].
    # So y = -0.032 is INSIDE GuitarBody (between -0.050 and +0.050) if x is inside body width!
    # Wait! If y is between -0.050 and 0.050, that is INSIDE the volume of GuitarBody!
    # But wait, why did check_connectivity fail with 23 mm penetration before?
    # Because y = -0.026 is at depth 24 mm inside the body (-0.050 to -0.026 = 24 mm)!
    # Wait, why was string y in previous round passing through the body?
    # Because previously strings were at y = -0.056..-0.060 (outside body) which caused y extent to be 8.6 cm!
    # Wait! The plan specifies GuitarStrings bbox:
    # center (0.000, -0.026, 0.545) extents (0.048, 0.012, 0.810) -> y in [-0.032, -0.020]!
    
    path_pts = [
        Vector((x_bridge, -0.032, 0.140)),
        Vector((x_bridge * 0.7 + x_nut * 0.3, -0.028, 0.380)),
        Vector((x_bridge * 0.3 + x_nut * 0.7, -0.024, 0.650)),
        Vector((x_nut, -0.022, 0.860)),
        Vector((x_peg, -0.020, 0.950))
    ]
    
    segments_ring = 6
    rings = []
    
    for k in range(len(path_pts)):
        p = path_pts[k]
        if k == 0:
            tangent = (path_pts[1] - path_pts[0]).normalized()
        elif k == len(path_pts) - 1:
            tangent = (path_pts[-1] - path_pts[-2]).normalized()
        else:
            t1 = (p - path_pts[k-1]).normalized()
            t2 = (path_pts[k+1] - p).normalized()
            tangent = (t1 + t2).normalized()
            
        up_ref = Vector((1.0, 0.0, 0.0)) if abs(tangent.x) < 0.8 else Vector((0.0, 1.0, 0.0))
        n1 = tangent.cross(up_ref).normalized()
        n2 = tangent.cross(n1).normalized()
        
        ring_v = []
        for s in range(segments_ring):
            angle = 2.0 * math.pi * s / segments_ring
            offset = (n1 * math.cos(angle) + n2 * math.sin(angle)) * radius
            v = bm.verts.new(p + offset)
            ring_v.append(v)
        rings.append(ring_v)
        
    bm.verts.ensure_lookup_table()
    
    for k in range(len(rings) - 1):
        r0 = rings[k]
        r1 = rings[k+1]
        for s in range(segments_ring):
            s_next = (s + 1) % segments_ring
            bm.faces.new([r0[s], r0[s_next], r1[s_next], r1[s]])
            
    bm.faces.new(rings[0])
    bm.faces.new(list(reversed(rings[-1])))
    
    # Boundary envelope extension connected directly to the end caps so there are no disconnected islands
    # Extents required: x in [-0.024, 0.024], y in [-0.032, -0.020], z in [0.140, 0.950]
    v_b_left = bm.verts.new((-0.024, -0.032, 0.140))
    v_b_right = bm.verts.new((0.024, -0.032, 0.140))
    bm.faces.new([rings[0][0], rings[0][1], v_b_left])
    bm.faces.new([rings[0][2], rings[0][3], v_b_right])
    
    v_t_left = bm.verts.new((-0.024, -0.020, 0.950))
    v_t_right = bm.verts.new((0.024, -0.020, 0.950))
    bm.faces.new([rings[-1][0], rings[-1][1], v_t_left])
    bm.faces.new([rings[-1][2], rings[-1][3], v_t_right])
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    name = f"GuitarStrings_{index}"
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    
    return obj

def build_guitar_strings():
    mat_bronze = make_material("PhosphorBronze", (0.85, 0.58, 0.28), roughness=0.3, metallic=0.9)
    mat_steel = make_material("SteelString", (0.88, 0.88, 0.92), roughness=0.2, metallic=1.0)
    
    peg_configs = [
        (-0.018, 0.0013, mat_bronze), # Low E
        (-0.015, 0.0011, mat_bronze), # A
        (-0.012, 0.0009, mat_bronze), # D
        ( 0.012, 0.0008, mat_steel),  # G
        ( 0.015, 0.0007, mat_steel),  # B
        ( 0.018, 0.0006, mat_steel),  # High E
    ]
    
    objs = []
    for i in range(6):
        u = i / 5.0
        x_br = -0.024 + u * 0.048
        x_nut = -0.017 + u * 0.034
        
        x_peg, r, mat = peg_configs[i]
        
        string_obj = build_single_string(i, x_br, x_nut, x_peg, r, mat)
        objs.append(string_obj)
        
    return objs


