"""TwistedTrunk — main structural trunk and primary branches (part module; imported by src/model.py).

Heavy tapered trunk (base dia 42 mm, tapering to 14 mm at apex) featuring an S-curve twist, rough furrowed bark, and 3 primary side boughs extending outwards.
Material: gnarled rough pine bark, dark grey-brown with reddish fissures.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

TWISTED_TRUNK_CENTER = (0.010, -0.010, 0.270)
TWISTED_TRUNK_EXTENTS = (0.260, 0.200, 0.240)
# bounds: x in [-0.120, 0.140], y in [-0.110, 0.090], z in [0.150, 0.390]

def make_material(name, rgb, roughness=0.8, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_tube(bm, path_points, radii, n_circle=16, bark_twist=True):
    """Extrude circular cross section along 3D path points with varying radii."""
    rings = []
    n_pts = len(path_points)
    for i in range(n_pts):
        p = Vector(path_points[i])
        r = radii[i]
        
        # Tangent
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
            twist = (i / max(1, n_pts - 1)) * 3.5 if bark_twist else 0.0
            r_furrow = r * (1.0 + 0.12 * math.sin(5 * (angle + twist)))
            offset = n1 * (math.cos(angle) * r_furrow) + n2 * (math.sin(angle) * r_furrow)
            v_pos = p + offset
            # clamp to trunk bbox
            v_pos.x = max(-0.120, min(0.140, v_pos.x))
            v_pos.y = max(-0.110, min(0.090, v_pos.y))
            v_pos.z = max(0.150, min(0.405, v_pos.z)) # reaches into apex foliage pad
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

def build_twisted_trunk() -> bpy.types.Object:
    bm = bmesh.new()

    # Main Trunk: S-curve from root collar (-0.022, 0.009, 0.183) curving right/forward, then left/back, then up into apex pad
    main_trunk_pts = [
        (-0.022, 0.009, 0.183),
        (-0.008, -0.008, 0.215),
        (0.018, -0.030, 0.250),  # curves forward and right
        (0.028, -0.022, 0.290),
        (0.010, 0.005, 0.330),   # curves back toward center/left
        (-0.006, 0.012, 0.365),
        (0.015, -0.008, 0.395),
        (0.020, -0.010, 0.404),  # penetrates 4mm into ApexFoliagePad (bottom at 0.400)
    ]
    main_trunk_radii = [0.021, 0.019, 0.016, 0.013, 0.011, 0.009, 0.007, 0.005]
    create_tube(bm, main_trunk_pts, main_trunk_radii, n_circle=16, bark_twist=True)

    # Branch 1 (Lower Branch leading to LowerFoliagePad at (0.120, -0.060, 0.280))
    b1_pts = [
        (0.022, -0.025, 0.265),
        (0.055, -0.045, 0.272),
        (0.090, -0.058, 0.278),
        (0.120, -0.065, 0.280),
        (0.135, -0.070, 0.281),
    ]
    b1_radii = [0.010, 0.008, 0.006, 0.005, 0.004]
    create_tube(bm, b1_pts, b1_radii, n_circle=12, bark_twist=False)

    # Secondary twig on Branch 1
    b1_sub_pts = [
        (0.085, -0.052, 0.275),
        (0.110, -0.082, 0.277),
        (0.128, -0.092, 0.278),
    ]
    b1_sub_radii = [0.005, 0.004, 0.003]
    create_tube(bm, b1_sub_pts, b1_sub_radii, n_circle=8, bark_twist=False)

    # Branch 2 (Middle Branch leading to MiddleFoliagePad at (-0.110, 0.050, 0.350))
    b2_pts = [
        (0.006, 0.006, 0.338),
        (-0.035, 0.025, 0.345),
        (-0.075, 0.042, 0.350),
        (-0.105, 0.050, 0.352),
        (-0.118, 0.052, 0.353),
    ]
    b2_radii = [0.008, 0.007, 0.005, 0.004, 0.003]
    create_tube(bm, b2_pts, b2_radii, n_circle=12, bark_twist=False)

    # Branch 3 (Apex supporting fork)
    b3_pts = [
        (-0.002, 0.010, 0.370),
        (0.038, -0.025, 0.403),
    ]
    b3_radii = [0.007, 0.004]
    create_tube(bm, b3_pts, b3_radii, n_circle=10, bark_twist=False)

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    me = bpy.data.meshes.new("TwistedTrunk")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("TwistedTrunk", me)
    bpy.context.scene.collection.objects.link(obj)

    mat = make_material("TwistedTrunkMat", (0.26, 0.17, 0.11), roughness=0.82, metallic=0.0)
    obj.data.materials.append(mat)

    return obj
