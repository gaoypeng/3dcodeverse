"""WaterGauge — transparent water level viewing window (part module; imported by src/model.py).

Vertical water level viewing window integrated smoothly into the curved kettle wall,
featuring an elegant bezel frame, transparent acrylic window, and measurement gradation markings.
Material: transparent acrylic with printed white markings and dark frame bezel.
Instances: 1.  Attaches to: KettleBody (must touch, overlap <= 2 mm).

Exports `build_water_gauge() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh

random.seed(0)

# Plan numbers
# center (0.067, 0.015, 0.115) extents (0.008, 0.020, 0.088)
# x in [0.063, 0.071]  y in [0.005, 0.025]  z in [0.071, 0.159]
WATER_GAUGE_CENTER = (0.067, 0.015, 0.115)
WATER_GAUGE_EXTENTS = (0.008, 0.020, 0.088)


def make_material(name, rgb, roughness=0.5, metallic=0.0, alpha=1.0, transmission_weight=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if "Transmission Weight" in bsdf.inputs and transmission_weight > 0.0:
        bsdf.inputs["Transmission Weight"].default_value = transmission_weight
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED"
    return mat


def build_water_gauge() -> bpy.types.Object:
    bm = bmesh.new()

    # Refined curved water gauge housing & acrylic window:
    # Target bounds:
    # X: 0.063 to 0.071 (thickness 0.008, centered at 0.067)
    # Overlap with KettleBody (radius at z=0.115 is ~0.0645) -> inner x at 0.0635 gives 1.0 mm overlap!
    # Y: 0.005 to 0.025 (width 0.020, centered at 0.015)
    # Z: 0.071 to 0.159 (height 0.088, centered at 0.115)
    
    n_capsule_pts = 16
    n_z_levels = 18
    
    z_min = 0.071
    z_max = 0.159
    z_len = z_max - z_min
    y_center = 0.015
    y_half = 0.010 # width = 0.020
    x_inner = 0.0635
    x_outer = 0.071
    
    # 1. Bezel frame & body
    levels = []
    for iz in range(n_z_levels):
        t = iz / (n_z_levels - 1)
        z = z_min + t * z_len
        
        cap_r = 0.008
        if z < z_min + cap_r:
            dz = (z_min + cap_r - z) / cap_r
            factor_y = math.sqrt(max(0.01, 1.0 - dz * dz))
            factor_x = math.sqrt(max(0.01, 1.0 - dz * dz))
        elif z > z_max - cap_r:
            dz = (z - (z_max - cap_r)) / cap_r
            factor_y = math.sqrt(max(0.01, 1.0 - dz * dz))
            factor_x = math.sqrt(max(0.01, 1.0 - dz * dz))
        else:
            factor_y = 1.0
            factor_x = 1.0
            
        cur_y_half = y_half * factor_y
        
        ring_verts = []
        for ip in range(n_capsule_pts):
            theta = 2 * math.pi * ip / n_capsule_pts
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            
            x = (x_inner + x_outer) / 2 + cos_t * (x_outer - x_inner) / 2 * factor_x
            y = y_center + sin_t * cur_y_half
            
            v = bm.verts.new((x, y, z))
            ring_verts.append(v)
        levels.append(ring_verts)

    # Bridge the levels
    for iz in range(n_z_levels - 1):
        r1 = levels[iz]
        r2 = levels[iz + 1]
        for ip in range(n_capsule_pts):
            ip_next = (ip + 1) % n_capsule_pts
            f = bm.faces.new((r1[ip], r1[ip_next], r2[ip_next], r2[ip]))
            # Outward-facing polygons (cos_theta > 0.3) get glass material index (1), others get bezel (0)
            mid_ip = (ip + 0.5)
            theta_mid = 2 * math.pi * mid_ip / n_capsule_pts
            if math.cos(theta_mid) > 0.4 and 2 <= iz <= n_z_levels - 4:
                f.material_index = 1
            else:
                f.material_index = 0

    # Cap bottom
    v_bot = bm.verts.new(((x_inner + x_outer) / 2, y_center, z_min))
    for ip in range(n_capsule_pts):
        ip_next = (ip + 1) % n_capsule_pts
        f = bm.faces.new((v_bot, levels[0][ip_next], levels[0][ip]))
        f.material_index = 0

    # Cap top
    v_top = bm.verts.new(((x_inner + x_outer) / 2, y_center, z_max))
    for ip in range(n_capsule_pts):
        ip_next = (ip + 1) % n_capsule_pts
        f = bm.faces.new((v_top, levels[-1][ip], levels[-1][ip_next]))
        f.material_index = 0

    bm.normal_update()

    me = bpy.data.meshes.new("WaterGauge")
    bm.to_mesh(me)
    bm.free()

    obj = bpy.data.objects.new("WaterGauge", me)
    bpy.context.scene.collection.objects.link(obj)

    for poly in me.polygons:
        poly.use_smooth = True

    # Materials
    mat_bezel = make_material("GaugeBezel", (0.15, 0.15, 0.16), roughness=0.35, metallic=0.1)
    mat_glass = make_material("GaugeGlass", (0.75, 0.88, 0.98), roughness=0.05, metallic=0.05, alpha=0.6, transmission_weight=0.8)

    obj.data.materials.append(mat_bezel)
    obj.data.materials.append(mat_glass)

    return obj
