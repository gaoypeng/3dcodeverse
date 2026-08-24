"""MountingBase — flanged cast baseplate with bolt holes for workbench mounting.

Circular cast-iron base plate (diameter 0.14 m, thickness 0.025 m) resting on z=0 with 3 radial bolt ears featuring vertical bolt through-holes.
Constructed with clean topology (radial sector ring) so that through-holes are actual open cylindrical holes.
Material: industrial blue hammered cast iron.
Plan bbox: center (0.000, 0.040, 0.015) extents (0.180, 0.180, 0.030)
  -> x in [-0.090, 0.090], y in [-0.050, 0.130], z in [0.000, 0.030]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_hammered_blue_material, link_object

MOUNTING_BASE_CENTER = (0.000, 0.040, 0.015)
MOUNTING_BASE_EXTENTS = (0.180, 0.180, 0.030)

def _make_holed_ear_bm(r_inner=0.0065, r_outer=0.018, height=0.014, segs=24) -> bmesh.types.BMesh:
    """Create a vertical cylinder with a coaxial through-hole (annular tube with top and bottom rim faces)."""
    bm = bmesh.new()
    top_z = height / 2.0
    bot_z = -height / 2.0
    
    top_outer_verts = []
    top_inner_verts = []
    bot_outer_verts = []
    bot_inner_verts = []
    
    for i in range(segs):
        theta = 2.0 * math.pi * i / segs
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        
        top_outer_verts.append(bm.verts.new((r_outer * cos_t, r_outer * sin_t, top_z)))
        top_inner_verts.append(bm.verts.new((r_inner * cos_t, r_inner * sin_t, top_z)))
        bot_outer_verts.append(bm.verts.new((r_outer * cos_t, r_outer * sin_t, bot_z)))
        bot_inner_verts.append(bm.verts.new((r_inner * cos_t, r_inner * sin_t, bot_z)))
    
    bm.verts.ensure_lookup_table()
    
    for i in range(segs):
        i_next = (i + 1) % segs
        # Top ring face
        bm.faces.new([top_outer_verts[i], top_outer_verts[i_next], top_inner_verts[i_next], top_inner_verts[i]])
        # Bottom ring face
        bm.faces.new([bot_outer_verts[i_next], bot_outer_verts[i], bot_inner_verts[i], bot_inner_verts[i_next]])
        # Outer cylinder side face
        bm.faces.new([bot_outer_verts[i], bot_outer_verts[i_next], top_outer_verts[i_next], top_outer_verts[i]])
        # Inner hole cylinder side face
        bm.faces.new([top_inner_verts[i], top_inner_verts[i_next], bot_inner_verts[i_next], bot_inner_verts[i]])
        
    return bm

def build_mounting_base() -> bpy.types.Object:
    bm = bmesh.new()

    # In local space relative to MOUNTING_BASE_CENTER = (0.000, 0.040, 0.015):
    # Local Z ranges from -0.015 (z=0) to +0.015 (z=0.030).
    
    r_main = 0.065
    h_main = 0.025 # local z from -0.015 to +0.010 -> center local z = -0.0025
    eh = 0.014
    z_ear = -0.015 + eh / 2
    
    # 1. Main circular base
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=36,
        radius1=r_main,
        radius2=r_main,
        depth=h_main,
        matrix=Matrix.Translation((0.0, 0.0, -0.0025))
    )

    # 2. Three 120-degree spaced radial bolt ears (Front: 270°, Rear-Right: 30°, Rear-Left: 150°)
    # Reach exactly r=0.090 to match plan extents (0.180, 0.180)
    # ear_radius = 0.018 -> ear_dist = 0.090 - 0.018 = 0.072
    bolt_ears = [
        (0.0, -0.072, 0.018),                              # Front tab (270°)
        (0.072 * math.cos(math.radians(30)), 0.072 * math.sin(math.radians(30)), 0.018),   # Back-Right (30°: x=0.06235, y=0.036)
        (-0.072 * math.cos(math.radians(30)), 0.072 * math.sin(math.radians(30)), 0.018),  # Back-Left (150°: x=-0.06235, y=0.036)
    ]
    
    # Adjust rear ear positions so extent in X is exactly 0.090 (er=0.018 -> ex=0.072) and rear reaches local y = +0.090 smoothly
    bolt_ears = [
        (0.0, -0.072, 0.018),
        (0.072, 0.036, 0.018),
        (-0.072, 0.036, 0.018),
    ]
    
    for ex, ey, er in bolt_ears:
        ear_bm = _make_holed_ear_bm(r_inner=0.0065, r_outer=er, height=eh, segs=24)
        bmesh.ops.transform(ear_bm, matrix=Matrix.Translation((ex, ey, z_ear)), verts=ear_bm.verts)
        
        # Connect ear to main base body with a smooth tapered flange root
        dist = math.sqrt(ex*ex + ey*ey)
        ang = math.atan2(ey, ex)
        
        bridge_len = dist - er + 0.006
        c = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(er * 1.8, bridge_len, eh), verts=c["verts"])
        rot_m = Matrix.Rotation(ang - math.pi / 2, 4, 'Z')
        bx_c = (ex * (bridge_len / 2)) / dist
        by_c = (ey * (bridge_len / 2)) / dist
        trans_m = Matrix.Translation((bx_c, by_c, z_ear))
        bmesh.ops.transform(bm, matrix=trans_m @ rot_m, verts=c["verts"])
        
        # Add ear_bm geometry into bm
        tmp_me = bpy.data.meshes.new("_tmp")
        ear_bm.to_mesh(tmp_me)
        ear_bm.free()
        tmp_bm = bmesh.new()
        tmp_bm.from_mesh(tmp_me)
        bpy.data.meshes.remove(tmp_me)
        
        bm_ear_verts = []
        for v in tmp_bm.verts:
            bm_ear_verts.append(bm.verts.new(v.co))
        tmp_bm.verts.ensure_lookup_table()
        for f in tmp_bm.faces:
            bm.faces.new([bm_ear_verts[v.index] for v in f.verts])
        tmp_bm.free()

    # Back curved rim flange segment (smooth rounded arc reaching local y = +0.090)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=24,
        radius1=0.018,
        radius2=0.018,
        depth=eh,
        matrix=Matrix.Translation((0.0, 0.072, z_ear))
    )
    # Bridge to center for back flange
    c_back_bridge = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.030, 0.060, eh), verts=c_back_bridge["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.040, z_ear)), verts=c_back_bridge["verts"])

    # 3. Top collar / swivel ring (z from 0.020 to 0.030 -> local z from 0.005 to 0.015, center = 0.010)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=36,
        radius1=0.055,
        radius2=0.052,
        depth=0.010,
        matrix=Matrix.Translation((0.0, 0.0, 0.010))
    )

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0002)

    me = bpy.data.meshes.new("MountingBase")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("MountingBase", me)
    obj.location = MOUNTING_BASE_CENTER
    bpy.context.scene.collection.objects.link(obj)

    obj.data.materials.append(get_hammered_blue_material())
    return obj
