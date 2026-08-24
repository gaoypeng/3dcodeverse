"""FrondStem — central rachis spine for each frond (part module; imported by src/model.py).

Eight tapered curved cylindrical stems (base diameter 0.008 m tapering to 0.002 m, arc length ~0.36 m) emerging radially at 45-degree azimuthal intervals, initially ascending outward then gracefully arching downward towards the tip.
Material: palm rachis stem, vibrant yellow-green with smooth satin finish. Instances: 8 (radial).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector

random.seed(0)

def make_material(name, rgb, roughness=0.45, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def get_frond_spine_points(n_pts=25):
    """
    Generate points along the base frond in the X-Z plane (+X direction, Y=0).
    Base at (0.024, 0, 0.354), reaches apex at z=0.612, droops to z=0.354 at tip (x=0.336).
    """
    pts = []
    p0 = Vector((0.024, 0.0, 0.354))
    p1 = Vector((0.080, 0.0, 0.690))
    p2 = Vector((0.245, 0.0, 0.690))
    p3 = Vector((0.336, 0.0, 0.354))
    
    for step in range(n_pts):
        t = step / (n_pts - 1)
        b0 = (1.0 - t) ** 3
        b1 = 3.0 * (1.0 - t) ** 2 * t
        b2 = 3.0 * (1.0 - t) * (t ** 2)
        b3 = t ** 3
        pt = p0 * b0 + p1 * b1 + p2 * b2 + p3 * b3
        pts.append(pt)
    return pts

def build_single_rachis_mesh(name: str) -> bpy.types.Object:
    """Build base tapered curved stem along +X axis."""
    spine = get_frond_spine_points(n_pts=25)
    bm = bmesh.new()
    
    circum_segs = 12
    n_pts = len(spine)
    
    rings = []
    for i in range(n_pts):
        pt = spine[i]
        t = i / (n_pts - 1)
        # Radius tapers from 0.004 m (diam 8mm) to 0.001 m (diam 2mm)
        r = 0.0040 * (1.0 - t) + 0.0010 * t
        
        if i == 0:
            tangent = (spine[1] - spine[0]).normalized()
        elif i == n_pts - 1:
            tangent = (spine[-1] - spine[-2]).normalized()
        else:
            tangent = (spine[i + 1] - spine[i - 1]).normalized()
            
        normal = Vector((0.0, 1.0, 0.0))
        binormal = tangent.cross(normal).normalized()
        normal = binormal.cross(tangent).normalized()
        
        ring = []
        for j in range(circum_segs):
            theta = 2.0 * math.pi * j / circum_segs
            offset = normal * (r * math.cos(theta)) + binormal * (r * math.sin(theta))
            v_pos = pt + offset
            ring.append(bm.verts.new(v_pos))
        rings.append(ring)
        
    # Cap start
    v_start = bm.verts.new(spine[0])
    for j in range(circum_segs):
        j_next = (j + 1) % circum_segs
        bm.faces.new((v_start, rings[0][j_next], rings[0][j]))
        
    # Connect tube
    for i in range(n_pts - 1):
        r1 = rings[i]
        r2 = rings[i + 1]
        for j in range(circum_segs):
            j_next = (j + 1) % circum_segs
            bm.faces.new((r1[j], r1[j_next], r2[j_next], r2[j]))
            
    # Cap end
    v_end = bm.verts.new(spine[-1])
    for j in range(circum_segs):
        j_next = (j + 1) % circum_segs
        bm.faces.new((v_end, rings[-1][j], rings[-1][j_next]))
        
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Vibrant saturated yellow-green rachis
    mat = make_material("FrondStemMat", (0.28, 0.52, 0.08), roughness=0.35, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj

def build_frond_stem() -> list[bpy.types.Object]:
    """Build all 8 FrondStem objects at 45 degree intervals around Z."""
    objs = []
    for i in range(8):
        angle = i * (2.0 * math.pi / 8.0)
        obj = build_single_rachis_mesh(f"FrondStem_{i}")
        obj.rotation_euler = (0, 0, angle)
        objs.append(obj)
    return objs
