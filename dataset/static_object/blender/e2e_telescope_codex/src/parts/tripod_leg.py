"""Three radial, tapered mahogany tripod legs with integral brass fittings."""
import math

import bpy
import bmesh
from mathutils import Vector

from parts._common import add_box_between, add_cone, add_tapered_rect, finish, material

TRIPOD_LEG_CENTER = (0.090, 0.050, 0.140)
TRIPOD_LEG_EXTENTS = (0.220, 0.220, 0.280)
TRIPOD_LEG_INSTANCES = 3
ANGLES_DEG = (30.0, 150.0, 270.0)


def _radial(angle, radius, z):
    return Vector((radius * math.cos(angle), radius * math.sin(angle), z))


def build_tripod_leg():
    wood = material("Mahogany", (0.24, 0.075, 0.028), roughness=0.42, metallic=0.0)
    brass = material("LegAntiqueBrass", (0.72, 0.43, 0.12), roughness=0.24, metallic=0.92)
    dark = material("LegPatina", (0.11, 0.07, 0.035), roughness=0.5, metallic=0.55)
    objects = []
    for i, degrees in enumerate(ANGLES_DEG):
        angle = math.radians(degrees)
        tangent = Vector((-math.sin(angle), math.cos(angle), 0.0))
        bm = bmesh.new()
        foot = _radial(angle, 0.170, 0.016)
        knee = _radial(angle, 0.092, 0.104)
        crown = _radial(angle, 0.026, 0.273)
        add_tapered_rect(bm, foot, knee + Vector((0, 0, 0.004)), (0.024, 0.026), (0.021, 0.023), 0)
        add_tapered_rect(bm, knee - Vector((0, 0, 0.004)), crown, (0.022, 0.024), (0.018, 0.021), 0)
        tip_center = _radial(angle, 0.170, 0.015)
        add_cone(bm, tip_center, (0, 0, 1), 0.030, 0.006, 0.0125, 1, 32)
        add_cone(bm, _radial(angle, 0.170, 0.031), (0, 0, 1), 0.008, 0.013, 0.013, 1, 32)
        add_tapered_rect(bm, _radial(angle, 0.030, 0.257), _radial(angle, 0.025, 0.279),
                         (0.022, 0.024), (0.019, 0.022), 1)
        add_cone(bm, _radial(angle, 0.027, 0.271), tangent, 0.029, 0.0052, 0.0052, 2, 24)
        for z, radius in ((0.046, 0.158), (0.056, 0.151)):
            center = _radial(angle, radius, z)
            add_box_between(bm, center - tangent * 0.014, center + tangent * 0.014, 0.006, 0.007, 1)
        objects.append(finish(f"TripodLeg_{i}", bm, [wood, brass, dark], bevel=0.0012, smooth=False))
    return objects
