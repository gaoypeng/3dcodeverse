"""FixedBody — rear stationary vise housing and fixed jaw support.

Massive cast-iron rear body featuring a lower cylindrical collar mounted on the base, a rectangular internal guide tunnel (45×45 mm) for the sliding bar, an internal threaded lead-screw hub, and an upward rising rear jaw tower.
Material: industrial blue hammered cast iron.
Plan bbox: center (0.000, 0.060, 0.100) extents (0.130, 0.220, 0.150)
  -> x in [-0.065, 0.065], y in [-0.050, 0.170], z in [0.025, 0.175]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_hammered_blue_material, link_object, apply_all_modifiers

FIXED_BODY_CENTER = (0.000, 0.060, 0.100)
FIXED_BODY_EXTENTS = (0.130, 0.220, 0.150)

def build_fixed_body() -> bpy.types.Object:
    bm = bmesh.new()
    
    # 1. Lower Mounting Swivel Base / Base Collar:
    # World Z in [0.025, 0.060] (local z in [-0.075, -0.040], height 0.035, center = -0.0575)
    # World Y centered around 0.040 (local y = -0.020)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=32,
        radius1=0.055,
        radius2=0.055,
        depth=0.035,
        matrix=Matrix.Translation((0.0, -0.020, -0.0575))
    )

    # 2. Main Guide Housing Side Rails:
    # Guide beam is at world z in [0.055, 0.095], width 0.060 (x in [-0.030, 0.030]).
    # Channel inside fixed body has 1 mm clearance on each side: x in [-0.031, 0.031]
    # Side walls: x in [0.031, 0.046] and [-0.046, -0.031], depth 0.220 (local y in [-0.110, 0.110])
    # Z in [0.045, 0.138] (local z in [-0.055, 0.038], height 0.093, center = -0.0085)
    for sign in [-1, 1]:
        cw = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.015, 0.220, 0.093), verts=cw["verts"])
        bmesh.ops.transform(bm, matrix=Matrix.Translation((sign * 0.0385, 0.0, -0.0085)), verts=cw["verts"])

    # Bottom plate of housing: z in [0.025, 0.053] (local z in [-0.075, -0.047], height 0.014, center = -0.061)
    cb = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.092, 0.220, 0.014), verts=cb["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.0, -0.061)), verts=cb["verts"])

    # Rear cap / drive nut bridge at back of housing: local y in [0.090, 0.110]
    cr = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.092, 0.020, 0.093), verts=cr["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.100, -0.0085)), verts=cr["verts"])

    # 3. Upward Rising Fixed Jaw Tower (front top portion of the fixed body):
    # World Y in [-0.050, 0.020] (local y in [-0.110, -0.040], depth 0.070, center = -0.075)
    # World Z in [0.110, 0.175] (local z in [0.010, 0.075], height 0.065, center = 0.0425)
    # Width = 0.130 (local x in [-0.065, 0.065])
    for sign in [-1, 1]:
        ctw = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.025, 0.070, 0.065), verts=ctw["verts"])
        bmesh.ops.transform(bm, matrix=Matrix.Translation((sign * 0.0525, -0.075, 0.0425)), verts=ctw["verts"])

    # Tower rear backplate: x in [-0.065, 0.065], depth 0.025 (local y in [-0.065, -0.040], center = -0.0525)
    ctb = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.130, 0.025, 0.065), verts=ctb["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, -0.0525, 0.0425)), verts=ctb["verts"])

    # 4. Rear Anvil Support Platform:
    # World Y in [0.020, 0.160] (local y in [-0.040, 0.100], depth 0.140, center = 0.030)
    # World Z in [0.110, 0.138] (local z in [0.010, 0.038], height 0.028, center = 0.024)
    # Width = 0.080 (local x in [-0.040, 0.040])
    c4 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.080, 0.140, 0.028), verts=c4["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.030, 0.024)), verts=c4["verts"])

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005)

    me = bpy.data.meshes.new("FixedBody")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("FixedBody", me)
    obj.location = FIXED_BODY_CENTER
    obj.data.materials.append(get_hammered_blue_material())
    link_object(obj)

    # Cast iron bevel for smooth rounded transitions
    bev = obj.modifiers.new("CastBevel", "BEVEL")
    bev.width = 0.002
    bev.segments = 2
    bev.limit_method = "ANGLE"
    bev.angle_limit = math.radians(35)
    apply_all_modifiers(obj)

    return obj
