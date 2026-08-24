"""SlidingJaw — movable front clamping jaw.

Heavy cast-iron movable front jaw with an arched reinforcement back, an upper jaw support matching the fixed jaw width (125 mm), a front collar for the lead-screw thrust bearing, and a bottom mount for the guide beam.
Material: industrial blue hammered cast iron.
Plan bbox: center (0.000, -0.105, 0.110) extents (0.130, 0.110, 0.160)
  -> x in [-0.065, 0.065], y in [-0.160, -0.050], z in [0.030, 0.190]
"""
import math
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import get_hammered_blue_material, link_object, apply_all_modifiers

SLIDING_JAW_CENTER = (0.000, -0.105, 0.110)
SLIDING_JAW_EXTENTS = (0.130, 0.110, 0.160)

def build_sliding_jaw() -> bpy.types.Object:
    bm = bmesh.new()
    
    # In local coordinates relative to SLIDING_JAW_CENTER = (0.000, -0.105, 0.110):
    # 1. Upper Jaw Tower:
    # World Y in [-0.095, -0.050] (local y in [0.010, 0.055]), World Z in [0.120, 0.190] (local z in [0.010, 0.080])
    c1 = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.130, 0.045, 0.070), verts=c1["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.0325, 0.045)), verts=c1["verts"])

    # 2. Main Central Body Cheeks:
    # Side cheek +X & -X: outer width 0.130 (x in [-0.065, 0.065]), inner width leaves tunnel
    # Depth in world y [-0.145, -0.070] (local y in [-0.040, 0.035], depth 0.075, center = -0.0025)
    # Z in [0.055, 0.145] (local z in [-0.055, 0.035], height 0.090, center = -0.010)
    for sign in [-1, 1]:
        c2 = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.025, 0.075, 0.090), verts=c2["verts"])
        bmesh.ops.transform(bm, matrix=Matrix.Translation((sign * 0.0525, -0.0025, -0.010)), verts=c2["verts"])

    # Top bridge between cheeks
    c2_t = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.080, 0.075, 0.020), verts=c2_t["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, -0.0025, 0.025)), verts=c2_t["verts"])

    # 3. Front Thrust Hub Ring (reaches world y = -0.160 -> local y = -0.055)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=24,
        radius1=0.026,
        radius2=0.024,
        depth=0.038,
        matrix=Matrix.Translation((0.0, -0.036, 0.005)) @ Matrix.Rotation(math.pi / 2, 4, 'X')
    )

    # 4. Front End-Plate / Mounting Face for Guide Beam:
    # GuideBeam starts at world y = -0.130 (local y = -0.025).
    # This solid front plate (local y in [-0.055, -0.024], depth 0.031) sits in FRONT of the guide beam (welded face-to-face with 1 mm overlap).
    # Z in [0.030, 0.100] (local z in [-0.080, -0.010])
    c_fplate = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.080, 0.031, 0.070), verts=c_fplate["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, -0.0395, -0.045)), verts=c_fplate["verts"])

    # 5. Lower Skirt / Bottom Rail Support:
    # Sits below the guide beam: world z in [0.030, 0.055] (local z in [-0.080, -0.055], height 0.025, center = -0.0675)
    # Y in world [-0.145, -0.050] (local y in [-0.040, 0.055], depth 0.095, center = 0.0075)
    c_bot = bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(0.080, 0.095, 0.025), verts=c_bot["verts"])
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0.0, 0.0075, -0.0675)), verts=c_bot["verts"])

    # Side ribs outside the guide beam: x in [0.031, 0.045] and [-0.045, -0.031], z in [0.055, 0.095]
    for sign in [-1, 1]:
        cw = bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(0.014, 0.080, 0.040), verts=cw["verts"])
        bmesh.ops.transform(bm, matrix=Matrix.Translation((sign * 0.038, 0.015, -0.035)), verts=cw["verts"])

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005)

    me = bpy.data.meshes.new("SlidingJaw")
    bm.to_mesh(me)
    bm.free()
    me.update()

    obj = bpy.data.objects.new("SlidingJaw", me)
    obj.location = SLIDING_JAW_CENTER
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
