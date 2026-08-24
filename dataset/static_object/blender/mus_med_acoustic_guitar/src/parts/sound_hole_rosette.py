"""SoundHoleRosette — sound hole and decorative rosette inlay (part module; imported by src/model.py).

Circular opening (d=0.088m) into the body centered on the soundboard with concentric ring inlays (abalone and black/white purfling) flush with the top surface.
Material: dark void interior with pearloid and black rosette rings. Instances: 1. Attaches to: GuitarBody.

Plan bbox: center (0.000, -0.050, 0.340) extents (0.110, 0.006, 0.110)
x in [-0.055, 0.055], y in [-0.053, -0.047], z in [0.285, 0.395]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

SOUND_HOLE_ROSETTE_CENTER = (0.000, -0.050, 0.340)
SOUND_HOLE_ROSETTE_EXTENTS = (0.110, 0.006, 0.110)

def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_sound_hole_rosette():
    bm = bmesh.new()
    
    # Rosette sits flush on soundboard front (soundboard at y = -0.050)
    # Rosette thickness = 0.004 (y in [-0.052, -0.048])
    segments = 36
    r_outer = 0.055
    r_hole = 0.044
    
    y_front = -0.052
    y_back = -0.048
    
    # 1. Single solid disc encompassing both the dark soundhole and rosette
    # Create front vertices at various radii: center, r_hole, r_outer
    v_c_f = bm.verts.new((0.0, y_front, 0.340))
    v_c_b = bm.verts.new((0.0, y_back, 0.340))
    
    ring_hole_f = []
    ring_hole_b = []
    ring_out_f = []
    ring_out_b = []
    
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        ca, sa = math.cos(a), math.sin(a)
        
        ring_hole_f.append(bm.verts.new((r_hole * ca, y_front, 0.340 + r_hole * sa)))
        ring_hole_b.append(bm.verts.new((r_hole * ca, y_back, 0.340 + r_hole * sa)))
        
        ring_out_f.append(bm.verts.new((r_outer * ca, y_front, 0.340 + r_outer * sa)))
        ring_out_b.append(bm.verts.new((r_outer * ca, y_back, 0.340 + r_outer * sa)))
        
    bm.verts.ensure_lookup_table()
    
    for i in range(segments):
        i_next = (i + 1) % segments
        # Center hole front
        bm.faces.new([v_c_f, ring_hole_f[i_next], ring_hole_f[i]])
        # Rosette ring front
        bm.faces.new([ring_hole_f[i], ring_hole_f[i_next], ring_out_f[i_next], ring_out_f[i]])
        # Outer rim
        bm.faces.new([ring_out_f[i], ring_out_f[i_next], ring_out_b[i_next], ring_out_b[i]])
        # Back cap
        bm.faces.new([v_c_b, ring_out_b[i], ring_out_b[i_next]])
        
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("SoundHoleRosette")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SoundHoleRosette", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat_hole = make_material("RosetteMat", (0.08, 0.06, 0.05), roughness=0.8, metallic=0.0)
    obj.data.materials.append(mat_hole)
    
    return obj
