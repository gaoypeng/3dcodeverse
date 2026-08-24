"""ExposedRoots — buttress roots wrapping and gripping the rock (part module; imported by src/model.py).

Network of 4 intertwining, knobby cylindrical roots (10 to 18 mm diameter) tightly conforming to the contours of the rock face and plunging into the moss.
Material: aged weathered woody bark, warm greyish brown with subtle lichen spots.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

EXPOSED_ROOTS_CENTER = (-0.020, 0.010, 0.110)
EXPOSED_ROOTS_EXTENTS = (0.150, 0.130, 0.150)
# bounds: x in [-0.095, 0.055], y in [-0.055, 0.075], z in [0.035, 0.185]

def make_material(name, rgb, roughness=0.75, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_tube(bm, path_points, radii, n_circle=12):
    """Extrude a circular cross section along 3D path points with varying radii."""
    rings = []
    n_pts = len(path_points)
    for i in range(n_pts):
        p = Vector(path_points[i])
        r = radii[i]
        
        if i == 0:
            tangent = (Vector(path_points[1]) - p).normalized()
        elif i == n_pts - 1:
            tangent = (p - Vector(path_points[i-1])).normalized()
        else:
            tangent = (Vector(path_points[i+1]) - Vector(path_points[i-1])).normalized()
            
        up = Vector((0, 0, 1)) if abs(tangent.z) < 0.9 else Vector((1, 0, 0))
        n1 = tangent.cross(up).normalized()
        n2 = tangent.cross(n1).normalized()
        
        ring = []
        for j in range(n_circle):
            angle = 2.0 * math.pi * j / n_circle
            r_var = r * (1.0 + 0.03 * math.sin(3 * angle + i * 2.0))
            offset = n1 * (math.cos(angle) * r_var) + n2 * (math.sin(angle) * r_var)
            v_pos = p + offset
            v_pos.x = max(-0.095, min(0.055, v_pos.x))
            v_pos.y = max(-0.055, min(0.075, v_pos.y))
            v_pos.z = max(0.035, min(0.185, v_pos.z))
            ring.append(bm.verts.new(v_pos))
        rings.append(ring)
        
    for i in range(n_pts - 1):
        r1 = rings[i]
        r2 = rings[i+1]
        for j in range(n_circle):
            j_next = (j + 1) % n_circle
            bm.faces.new([r1[j], r1[j_next], r2[j_next], r2[j]])
            
    v_start = bm.verts.new(path_points[0])
    v_end = bm.verts.new(path_points[-1])
    for j in range(n_circle):
        j_next = (j + 1) % n_circle
        bm.faces.new([v_start, rings[0][j_next], rings[0][j]])
        bm.faces.new([v_end, rings[-1][j], rings[-1][j_next]])

def build_exposed_roots() -> bpy.types.Object:
    bm = bmesh.new()

    # Root 1: Front-right (radius ~0.006)
    r1_pts = [
        (-0.020, 0.005, 0.173),
        (0.016, -0.018, 0.155),
        (0.038, -0.035, 0.120),
        (0.048, -0.045, 0.080),
        (0.052, -0.050, 0.050),
        (0.054, -0.052, 0.038),
    ]
    r1_radii = [0.007, 0.006, 0.006, 0.005, 0.005, 0.004]
    create_tube(bm, r1_pts, r1_radii)

    # Root 2: Left-front
    r2_pts = [
        (-0.025, 0.000, 0.174),
        (-0.058, -0.014, 0.150),
        (-0.078, -0.026, 0.115),
        (-0.085, -0.029, 0.075),
        (-0.090, -0.026, 0.050),
        (-0.092, -0.020, 0.038),
    ]
    r2_radii = [0.007, 0.006, 0.006, 0.005, 0.005, 0.004]
    create_tube(bm, r2_pts, r2_radii)

    # Root 3: Back-left
    r3_pts = [
        (-0.028, 0.014, 0.173),
        (-0.062, 0.040, 0.145),
        (-0.078, 0.054, 0.110),
        (-0.080, 0.062, 0.075),
        (-0.076, 0.068, 0.050),
        (-0.070, 0.070, 0.038),
    ]
    r3_radii = [0.007, 0.006, 0.006, 0.005, 0.005, 0.004]
    create_tube(bm, r3_pts, r3_radii)

    # Root 4: Back-right
    r4_pts = [
        (-0.015, 0.016, 0.174),
        (0.020, 0.040, 0.150),
        (0.038, 0.054, 0.115),
        (0.044, 0.058, 0.080),
        (0.046, 0.060, 0.050),
        (0.048, 0.062, 0.038),
    ]
    r4_radii = [0.007, 0.006, 0.006, 0.005, 0.005, 0.004]
    create_tube(bm, r4_pts, r4_radii)

    # Root collar / nebari junction at the crown of the rock (sitting at z=0.170 to 0.185)
    r_collar_pts = [
        (-0.022, 0.009, 0.170),
        (-0.022, 0.009, 0.185),
    ]
    r_collar_radii = [0.014, 0.013]
    create_tube(bm, r_collar_pts, r_collar_radii, n_circle=16)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("ExposedRoots")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("ExposedRoots", me)
    bpy.context.scene.collection.objects.link(obj)

    mat = make_material("ExposedRootsMat", (0.32, 0.22, 0.14), roughness=0.8, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
