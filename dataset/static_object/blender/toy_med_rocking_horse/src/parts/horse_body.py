"""HorseBody — main torso and barrel of the horse (part module; imported by src/model.py).

Rounded cylindrical torso tapered slightly towards the flank with bullnose end fillets and underside mortise recesses for leg joints.
Material: natural solid birch, satin clear coat. Instances: 1.
Plan bbox: center (0.000, -0.010, 0.375) extents (0.140, 0.400, 0.140)
  x in [-0.070, 0.070]  y in [-0.210, 0.190]  z in [0.305, 0.445]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

def make_material(name, rgb, roughness=0.45, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_horse_body():
    bm = bmesh.new()
    
    # Cylinder along Y with hemisphere caps
    # Length along Y = 0.400, radius = 0.070
    # Center = (0, -0.010, 0.375)
    # y range = [-0.210, 0.190]
    # Cylinder mid-section between y = -0.140 and y = +0.120
    
    n_ring = 32
    n_cap_rings = 8
    
    rings = []
    
    # Front tip vertex
    v_front_tip = bm.verts.new((0.0, -0.210, 0.375))
    
    # Front cap rings (excluding pole theta=-pi/2, ending at theta=0)
    for ic in range(1, n_cap_rings + 1):
        theta = -math.pi / 2.0 + (math.pi / 2.0) * (ic / n_cap_rings)
        r = 0.070 * math.cos(theta)
        y = -0.140 + 0.070 * math.sin(theta)
        ring = []
        for ir in range(n_ring):
            phi = 2.0 * math.pi * ir / n_ring
            x = r * math.cos(phi)
            z = 0.375 + r * math.sin(phi)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    # Cylindrical body rings
    n_body_rings = 16
    for ib in range(1, n_body_rings + 1):
        frac = ib / n_body_rings
        y = -0.140 + frac * (0.120 - (-0.140))
        r = 0.070
        ring = []
        for ir in range(n_ring):
            phi = 2.0 * math.pi * ir / n_ring
            x = r * math.cos(phi)
            z = 0.375 + r * math.sin(phi)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    # Rear cap rings (theta from 0 to pi/2, excluding theta=pi/2)
    for ic in range(1, n_cap_rings):
        theta = (math.pi / 2.0) * (ic / n_cap_rings)
        r = 0.070 * math.cos(theta)
        y = 0.120 + 0.070 * math.sin(theta)
        ring = []
        for ir in range(n_ring):
            phi = 2.0 * math.pi * ir / n_ring
            x = r * math.cos(phi)
            z = 0.375 + r * math.sin(phi)
            ring.append(bm.verts.new((x, y, z)))
        rings.append(ring)
        
    # Rear tip vertex
    v_rear_tip = bm.verts.new((0.0, 0.190, 0.375))
    
    bm.verts.ensure_lookup_table()
    
    # Faces for front cap tip
    r_front = rings[0]
    for j in range(n_ring):
        j_next = (j + 1) % n_ring
        bm.faces.new([v_front_tip, r_front[j], r_front[j_next]])
        
    # Quad strips between consecutive rings
    for i in range(len(rings) - 1):
        r0 = rings[i]
        r1 = rings[i + 1]
        for j in range(n_ring):
            j_next = (j + 1) % n_ring
            bm.faces.new([r0[j], r1[j], r1[j_next], r0[j_next]])
            
    # Faces for rear cap tip
    r_rear = rings[-1]
    for j in range(n_ring):
        j_next = (j + 1) % n_ring
        bm.faces.new([v_rear_tip, r_rear[j_next], r_rear[j]])
        
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    # Recalculate normals to ensure consistent outward orientation
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new("HorseBody")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("HorseBody", me)
    bpy.context.scene.collection.objects.link(obj)
    mat = make_material("BirchWoodBody", (0.82, 0.70, 0.52), roughness=0.4, metallic=0.0)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
