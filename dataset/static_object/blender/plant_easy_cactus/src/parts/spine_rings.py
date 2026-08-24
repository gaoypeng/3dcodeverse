"""SpineRings — protective radial spines and areoles (part module; imported by src/model.py).

Evenly spaced areole clusters along all 20 ribs, each bearing 5-7 radiating stiff needle spines curved slightly downward and outward (needle length 0.012-0.018 m).
Material: golden cactus spines, translucent yellow-amber.
Plan bbox: center (0.000, 0.000, 0.265) extents (0.185, 0.185, 0.150)
  z in [0.190, 0.340], x in [-0.0925, 0.0925], y in [-0.0925, 0.0925]
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(42)

SPINE_RINGS_CENTER = (0.000, 0.000, 0.265)
SPINE_RINGS_EXTENTS = (0.185, 0.185, 0.150)

def make_material(name, rgb, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat

def create_spine_needle(bm, base_pt: Vector, out_dir: Vector, length: float, r_base: float, r_tip: float, curve_down: float = 0.003):
    """Generates a slender tapered spine needle connected directly to areole base."""
    up = Vector((0, 0, 1))
    if abs(out_dir.dot(up)) > 0.95:
        up = Vector((0, 1, 0))
    side = out_dir.cross(up).normalized()
    spine_up = side.cross(out_dir).normalized()
    
    p0 = base_pt
    p1 = base_pt + out_dir * (length * 0.5) - Vector((0, 0, curve_down * 0.4))
    p2 = base_pt + out_dir * length - Vector((0, 0, curve_down))
    
    cross_segs = 4
    r0_verts = []
    for i in range(cross_segs):
        ang = 2 * math.pi * i / cross_segs
        off = (side * math.cos(ang) + spine_up * math.sin(ang)) * r_base
        r0_verts.append(bm.verts.new(p0 + off))
        
    r1_verts = []
    r_mid = (r_base + r_tip) * 0.5
    for i in range(cross_segs):
        ang = 2 * math.pi * i / cross_segs
        off = (side * math.cos(ang) + spine_up * math.sin(ang)) * r_mid
        r1_verts.append(bm.verts.new(p1 + off))
        
    v_tip = bm.verts.new(p2)
    
    for i in range(cross_segs):
        i_next = (i + 1) % cross_segs
        bm.faces.new([r0_verts[i], r0_verts[i_next], r1_verts[i_next], r1_verts[i]])
        bm.faces.new([r1_verts[i], r1_verts[i_next], v_tip])
        
    bm.faces.new([r0_verts[3], r0_verts[2], r0_verts[1], r0_verts[0]])

def build_spine_rings() -> bpy.types.Object:
    """Builds the areoles and radiating golden spine clusters along 20 ribs connected via areole rings."""
    bm = bmesh.new()
    
    num_ribs = 20
    areole_tiers = 8
    z_center = 0.260
    h_half = 0.085
    r_cactus_max = 0.085
    
    # Generate spine clusters
    for tier in range(areole_tiers):
        v_frac = (tier + 0.5) / areole_tiers
        lat = -math.pi * 0.38 + (math.pi * 0.76) * v_frac
        
        base_r = math.cos(lat) * r_cactus_max
        z_level = z_center + h_half * math.sin(lat)
        rib_crest_r = base_r
        
        for rib in range(num_ribs):
            phi = 2 * math.pi * rib / num_ribs
            
            # Embed areole base 1mm into cactus rib
            ax = (rib_crest_r - 0.001) * math.cos(phi)
            ay = (rib_crest_r - 0.001) * math.sin(phi)
            az = z_level
            areole_pos = Vector((ax, ay, az))
            
            # Areole cushion (small woolly nodule)
            bmesh.ops.create_uvsphere(
                bm,
                u_segments=6,
                v_segments=4,
                radius=0.0025,
                matrix=Matrix.Translation(areole_pos)
            )
            
            radial_dir = Vector((math.cos(phi), math.sin(phi), 0.15 * math.sin(lat))).normalized()
            tangent_dir = Vector((-math.sin(phi), math.cos(phi), 0)).normalized()
            vert_dir = Vector((0, 0, 1))
            
            # Central spine
            central_len = 0.015
            create_spine_needle(
                bm,
                base_pt=areole_pos,
                out_dir=(radial_dir - Vector((0, 0, 0.1))).normalized(),
                length=central_len,
                r_base=0.0009,
                r_tip=0.0002,
                curve_down=0.003
            )
            
            # Radial spines (5 radiating outwards)
            radial_angles = [0, 72, 144, 216, 288]
            for r_deg in radial_angles:
                r_rad = math.radians(r_deg)
                spread_dir = (
                    radial_dir * 0.7 +
                    tangent_dir * (0.6 * math.cos(r_rad)) +
                    vert_dir * (0.6 * math.sin(r_rad))
                ).normalized()
                
                rad_len = 0.011 + 0.003 * math.cos(r_rad)
                create_spine_needle(
                    bm,
                    base_pt=areole_pos,
                    out_dir=spread_dir,
                    length=rad_len,
                    r_base=0.0007,
                    r_tip=0.0002,
                    curve_down=0.002
                )

    bm.normal_update()
    
    # Scale to ensure exact target bounds:
    # Extents: (0.185, 0.185, 0.150) -> x/y in [-0.0925, 0.0925], z in [0.190, 0.340]
    max_x = max(abs(v.co.x) for v in bm.verts)
    max_y = max(abs(v.co.y) for v in bm.verts)
    if max_x > 0 and max_y > 0:
        for v in bm.verts:
            v.co.x *= (0.0925 / max_x)
            v.co.y *= (0.0925 / max_y)
            
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)
    z_span = max_z - min_z
    if z_span > 0:
        for v in bm.verts:
            nz = (v.co.z - min_z) / z_span
            v.co.z = 0.190 + nz * 0.150
            
    bm.normal_update()
    me = bpy.data.meshes.new("SpineRings")
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new("SpineRings", me)
    bpy.context.scene.collection.objects.link(obj)
    
    mat = make_material("GoldenSpines", (0.95, 0.78, 0.22), roughness=0.30, metallic=0.05)
    obj.data.materials.append(mat)
    
    for poly in obj.data.polygons:
        poly.use_smooth = True
        
    return obj
