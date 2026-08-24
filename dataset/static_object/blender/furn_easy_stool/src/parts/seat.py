"""Seat — circular top sitting surface (part module; imported by src/model.py).

Solid round wooden seat slab, diameter 0.340 m and thickness 0.035 m, top edge smoothed with a 6 mm bevel, flat underside mortised for leg tenons.
Material: natural light oak, smooth matte varnish.  Instances: 1.

Exports `build_seat() -> bpy.types.Object`.
"""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix

random.seed(0)

# Plan numbers (metres) for Seat
SEAT_CENTER = (0.000, 0.000, 0.4325)
SEAT_DIAMETER = 0.340
SEAT_THICKNESS = 0.035
SEAT_TOP_Z = 0.450
SEAT_BOTTOM_Z = SEAT_TOP_Z - SEAT_THICKNESS  # 0.415


def make_material(name, rgb, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def build_seat() -> bpy.types.Object:
    """Seat — solid round wooden slab, bevelled top and bottom edges."""
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=64,
        radius1=SEAT_DIAMETER / 2.0,
        radius2=SEAT_DIAMETER / 2.0,
        depth=SEAT_THICKNESS
    )

    me = bpy.data.meshes.new("Seat")
    bm.to_mesh(me)
    bm.free()
    me.update()

    seat = bpy.data.objects.new("Seat", me)
    seat.location = (0.0, 0.0, SEAT_BOTTOM_Z + SEAT_THICKNESS / 2.0)
    bpy.context.scene.collection.objects.link(seat)

    # Bevel modifier for rounded edges (6 mm bevel)
    bev = seat.modifiers.new("Bevel", "BEVEL")
    bev.width = 0.006
    bev.segments = 4
    bev.limit_method = "ANGLE"
    bev.angle_limit = math.radians(60)

    seat.data.shade_smooth()

    # Warm natural light oak colour (sRGB)
    mat = make_material("SeatWood", (0.68, 0.50, 0.32), roughness=0.45, metallic=0.0)
    seat.data.materials.append(mat)

    return seat
