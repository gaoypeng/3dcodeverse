"""FrondLeaflets — pinnate leaflets along each frond rachis (part module; imported by src/model.py).

Array of 24–32 slender lanceolate leaflets per frond (each 0.06–0.12 m long, 0.008–0.012 m wide, 0.001 m thick with subtle V-crease cross section) arranged in opposing pairs along the curved rachis, drooping slightly at tips.
Material: palm foliage leaf, rich tropical emerald green with faint parallel venation. Instances: 8 (radial).
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector

random.seed(0)

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def get_frond_point_and_tangent(t: float):
    """Cubic Bezier curve matching FrondStem spine in +X plane."""
    p0 = Vector((0.024, 0.0, 0.354))
    p1 = Vector((0.080, 0.0, 0.690))
    p2 = Vector((0.245, 0.0, 0.690))
    p3 = Vector((0.336, 0.0, 0.354))
    
    b0 = (1.0 - t) ** 3
    b1 = 3.0 * (1.0 - t) ** 2 * t
    b2 = 3.0 * (1.0 - t) * (t ** 2)
    b3 = t ** 3
    pos = p0 * b0 + p1 * b1 + p2 * b2 + p3 * b3
    
    # Derivative
    d0 = -3.0 * (1.0 - t) ** 2
    d1 = 3.0 * (1.0 - 4.0 * t + 3.0 * t ** 2)
    d2 = 3.0 * (2.0 * t - 3.0 * t ** 2)
    d3 = 3.0 * t ** 2
    tan = (p0 * d0 + p1 * d1 + p2 * d2 + p3 * d3).normalized()
    return pos, tan

def add_single_leaflet(bm, origin: Vector, forward_dir: Vector, out_side_dir: Vector, up_dir: Vector,
                       length: float, width: float, side_sign: float):
    """
    Construct a slender 3D lanceolate leaflet blade with V-crease profile and droop.
    side_sign is +1 for +Y side, -1 for -Y side, 0 for terminal.
    """
    n_seg = 4
    blade_verts_left = []
    blade_verts_mid = []
    blade_verts_right = []
    
    for k in range(n_seg + 1):
        frac = k / n_seg
        w = width * math.sin(frac * math.pi) if frac > 0 else width * 0.2
        if frac == 1.0:
            w = 0.0005
            
        droop = 0.018 * (frac ** 2)
        
        if side_sign != 0.0:
            dir_leaflet = (forward_dir * 0.42 + out_side_dir * (0.90 * side_sign)).normalized()
        else:
            dir_leaflet = forward_dir
            
        p_mid = origin + dir_leaflet * (length * frac) - Vector((0, 0, droop))
        
        v_offset = (up_dir * 0.0015)
        cross_leaflet = dir_leaflet.cross(up_dir).normalized() * (w * 0.5)
        
        p_left = p_mid - cross_leaflet + v_offset
        p_right = p_mid + cross_leaflet + v_offset
        
        vl = bm.verts.new(p_left)
        vm = bm.verts.new(p_mid)
        vr = bm.verts.new(p_right)
        
        blade_verts_left.append(vl)
        blade_verts_mid.append(vm)
        blade_verts_right.append(vr)
        
    for k in range(n_seg):
        bm.faces.new((blade_verts_left[k], blade_verts_left[k + 1],
                      blade_verts_mid[k + 1], blade_verts_mid[k]))
        bm.faces.new((blade_verts_mid[k], blade_verts_mid[k + 1],
                      blade_verts_right[k + 1], blade_verts_right[k]))

def build_single_frond_leaflets_mesh(name: str) -> bpy.types.Object:
    """Build base leaflets mesh along +X rachis."""
    bm = bmesh.new()
    
    # 14 pairs of leaflets along the rachis (total 28 leaflets per frond)
    n_pairs = 14
    for pair_idx in range(n_pairs):
        t = 0.15 + (pair_idx / (n_pairs - 1)) * 0.83
        origin, tan = get_frond_point_and_tangent(t)
        
        out_side_dir = Vector((0.0, 1.0, 0.0))  # Y axis
        up_dir = out_side_dir.cross(tan).normalized()
        
        len_factor = math.sin((t - 0.10) / 0.90 * math.pi)
        length = 0.065 + 0.035 * len_factor
        width = 0.009 + 0.003 * len_factor
        
        add_single_leaflet(bm, origin, tan, out_side_dir, up_dir, length, width, side_sign=1.0)
        add_single_leaflet(bm, origin, tan, out_side_dir, up_dir, length, width, side_sign=-1.0)
        
    # Terminal leaflet at tip
    tip_pos, tip_tan = get_frond_point_and_tangent(0.99)
    out_side_dir = Vector((0.0, 1.0, 0.0))
    up_dir = out_side_dir.cross(tip_tan).normalized()
    add_single_leaflet(bm, tip_pos, tip_tan, out_side_dir, up_dir, length=0.045, width=0.006, side_sign=0.0)
    
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    
    # Lush rich tropical emerald green
    mat = make_material("FrondLeafletsMat", (0.07, 0.38, 0.09), roughness=0.32, metallic=0.0)
    obj.data.materials.append(mat)
    
    return obj

def build_frond_leaflets() -> list[bpy.types.Object]:
    """Build all 8 FrondLeaflets objects."""
    objs = []
    for i in range(8):
        angle = i * (2.0 * math.pi / 8.0)
        obj = build_single_frond_leaflets_mesh(f"FrondLeaflets_{i}")
        obj.rotation_euler = (0, 0, angle)
        objs.append(obj)
    return objs
