"""WindmillCap — Thatched rotating cap roof and windshaft housing (part module; imported by src/model.py).

Traditional Dutch boat-shaped thatched cap, rounded arch top, sloping rear tail. Base diameter 2.50 m sitting on the body collar at z=6.10 m, peak height reaches z=7.50 m. Front projecting gable housing for the main drive axle.
Material: natural reed thatch roof with white painted timber fascia trim. Instances: 1.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan bbox: center (0.000, -0.150, 6.800) extents (2.600, 3.300, 1.450)
# x in [-1.300, 1.300], y in [-1.800, 1.500], z in [6.075, 7.525]
WINDMILL_CAP_CENTER = (0.000, -0.150, 6.800)
WINDMILL_CAP_EXTENTS = (2.600, 3.300, 1.450)

def make_material(name, rgb, roughness=0.75, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def build_windmill_cap() -> bpy.types.Object:
    """Boat-shaped Dutch windmill cap (mansard / arched thatched roof with gable front and tail).
    World center: (0.0, -0.150, 6.800).
    Local Z: -0.725 to +0.725 (world z: 6.075 to 7.525).
    Local Y: -1.650 to +1.650 (world y: -1.800 to +1.500).
    Local X: -1.300 to +1.300 (world x: -1.300 to +1.300).
    """
    bm = bmesh.new()
    
    # 1. Base collar ring (circular/octagonal wooden kerb at cap bottom, local z = -0.725 to -0.625)
    res_base = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=24,
        radius1=1.260,
        radius2=1.260,
        depth=0.100
    )
    bmesh.ops.translate(bm, vec=(0.0, 0.150, -0.675), verts=res_base['verts']) # centered at body axis (0,0)
    
    # 2. Main roof body: extends from local y = -1.223 (world y = -1.373) to local y = +1.650 (world y = +1.500)
    # The back face of SailRotorHub is at world y = -1.375 (local y in cap frame: -1.225).
    # Touching at local y = -1.223 gives exactly 2mm overlap with SailRotorHub!
    y_slices = [
        (-1.223, 1.100, -0.700, 0.720, 0.600),   # front cap breast wall touching SailRotorHub rear
        (-0.200, 1.300, -0.725, 0.725, 0.680),   # peak crest
        (0.800,  1.150, -0.700, 0.550, 0.500),   # rear slope
        (1.650,  0.450, -0.650, 0.220, 0.150),   # tail tip
    ]
    
    rings = []
    n_pts = 16
    for (y_pos, rx, z_base, z_peak, z_shoulder) in y_slices:
        pts = []
        for j in range(n_pts):
            th = 2.0 * math.pi * j / n_pts
            ct = math.cos(th)
            st = math.sin(th)
            
            px = rx * ct
            if st >= 0:
                pz = z_base + (z_peak - z_base) * (st ** 0.7)
            else:
                pz = z_base + 0.05 * (st + 1.0)
            
            pts.append(bm.verts.new((px, y_pos, pz)))
        rings.append(pts)
        
    for i in range(len(rings) - 1):
        r1 = rings[i]
        r2 = rings[i + 1]
        for j in range(n_pts):
            j_next = (j + 1) % n_pts
            bm.faces.new([r1[j], r1[j_next], r2[j_next], r2[j]])
            
    # Cap front and rear ends of the main roof body
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    
    # 3. Side & Top bargeboards / roof hood overhang reaching forward to local y = -1.650 (world y = -1.800)
    # They stay outside the central rotor hub area (hub is |x| <= 0.240, |z| <= 0.240).
    # Left bargeboard (x: -0.65 to -0.55, z: 0.25 to 0.70)
    res_b_l = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.08, 0.427, 0.40), verts=res_b_l['verts'])
    bmesh.ops.translate(bm, vec=(-0.55, -1.4365, 0.40), verts=res_b_l['verts'])
    
    # Right bargeboard (x: +0.55 to +0.65, z: 0.25 to 0.70)
    res_b_r = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.08, 0.427, 0.40), verts=res_b_r['verts'])
    bmesh.ops.translate(bm, vec=(0.55, -1.4365, 0.40), verts=res_b_r['verts'])
    
    # Top ridge hood apex trim at local y = -1.650, z = 0.65 (well above hub which top is at z = 0.240)
    res_b_top = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.50, 0.427, 0.10), verts=res_b_top['verts'])
    bmesh.ops.translate(bm, vec=(0.00, -1.4365, 0.65), verts=res_b_top['verts'])
    
    # 4. Tail pole / tail spar (hark/staart) at the back sloping down from tail
    res_tail = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.10, 0.80, 0.10), verts=res_tail['verts'])
    bmesh.ops.rotate(bm, matrix=Matrix.Rotation(-0.4, 4, 'X'), verts=res_tail['verts'])
    bmesh.ops.translate(bm, vec=(0.0, 1.250, -0.300), verts=res_tail['verts'])

    me = bpy.data.meshes.new("WindmillCap")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("WindmillCap", me)
    obj.location = (0.000, -0.150, 6.800)
    bpy.context.scene.collection.objects.link(obj)
    
    # Material: natural reed thatch with white timber trim
    mat_cap = make_material("CapThatchMat", (0.42, 0.35, 0.25), roughness=0.85)
    obj.data.materials.append(mat_cap)
    
    return obj
