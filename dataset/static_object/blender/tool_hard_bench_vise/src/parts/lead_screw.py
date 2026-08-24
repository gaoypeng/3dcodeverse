"""LeadScrew — acme threaded drive screw for clamping force.

Cylindrical steel shaft (diameter 24 mm) with Acme trapezoidal threading running from the front hub through the sliding jaw into the fixed body drive nut, ending in an enlarged front thrust collar (diameter 35 mm).
Material: zinc-plated threaded steel.
Plan bbox: center (0.000, -0.070, 0.115) extents (0.035, 0.260, 0.035)
  -> x in [-0.0175, 0.0175], y in [-0.200, 0.060], z in [0.0975, 0.1325]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_screw_material, link_object, apply_all_modifiers

LEAD_SCREW_CENTER = (0.000, -0.070, 0.115)
LEAD_SCREW_EXTENTS = (0.035, 0.260, 0.035)

def build_lead_screw() -> bpy.types.Object:
    bm = bmesh.new()
    
    collar_len = 0.045
    collar_r = 0.0175
    collar_y_local = -0.130 + collar_len / 2
    
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=28,
        radius1=collar_r,
        radius2=collar_r,
        depth=collar_len,
        matrix=Matrix.Translation((0.0, collar_y_local, 0.0)) @ Matrix.Rotation(math.pi / 2, 4, 'X')
    )

    shaft_len = 0.215
    shaft_r = 0.012
    shaft_y_local = -0.085 + shaft_len / 2
    
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=24,
        radius1=shaft_r,
        radius2=shaft_r,
        depth=shaft_len,
        matrix=Matrix.Translation((0.0, shaft_y_local, 0.0)) @ Matrix.Rotation(math.pi / 2, 4, 'X')
    )

    n_threads = 16
    for i in range(n_threads):
        ty = -0.075 + i * (0.190 / n_threads)
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            segments=16,
            radius1=shaft_r + 0.001,
            radius2=shaft_r + 0.001,
            depth=0.004,
            matrix=Matrix.Translation((0.0, ty, 0.0)) @ Matrix.Rotation(math.pi / 2, 4, 'X')
        )

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005)

    me = bpy.data.meshes.new("LeadScrew")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("LeadScrew", me)
    obj.location = LEAD_SCREW_CENTER
    obj.data.materials.append(get_screw_material())
    link_object(obj)

    # Cut tommy bar hole
    bm_hole = bmesh.new()
    bmesh.ops.create_cone(
        bm_hole,
        cap_ends=True,
        segments=16,
        radius1=0.0062,
        radius2=0.0062,
        depth=0.040,
        matrix=Matrix.Rotation(math.pi / 2, 4, 'Y')
    )
    me_hole = bpy.data.meshes.new("_temp_tommy_hole")
    bm_hole.to_mesh(me_hole)
    bm_hole.free()
    
    o_hole = bpy.data.objects.new("_temp_tommy_hole", me_hole)
    o_hole.location = (0.0, -0.175, 0.115)
    link_object(o_hole)

    mod = obj.modifiers.new("TommyHole", 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = o_hole

    apply_all_modifiers(obj)
    bpy.data.objects.remove(o_hole, do_unlink=True)

    return obj
