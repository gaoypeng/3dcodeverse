"""Turned central brass tripod head with three integral hinge lugs."""
import math

import bpy
import bmesh

from parts._common import add_box_between, add_cone, add_lathe, finish, material

TRIPOD_HUB_CENTER = (0.000, 0.000, 0.280)
TRIPOD_HUB_EXTENTS = (0.060, 0.060, 0.035)


def build_tripod_hub():
    brass = material("HubBrass", (0.80, 0.53, 0.18), roughness=0.22, metallic=0.95)
    patina = material("HubRecessPatina", (0.20, 0.14, 0.05), roughness=0.43, metallic=0.70)
    bm = bmesh.new()
    add_lathe(bm, (0, 0, 0.280), (0, 0, 1),
              [(-0.0175, 0.025), (-0.014, 0.030), (0.008, 0.030),
               (0.013, 0.025), (0.0175, 0.021)], 0, 48)
    add_lathe(bm, (0, 0, 0.294), (0, 0, 1), [(-0.002, 0.013), (0.002, 0.013)], 1, 32, ribbed=True)
    for degrees in (30.0, 150.0, 270.0):
        angle = math.radians(degrees)
        p0 = (0.016 * math.cos(angle), 0.016 * math.sin(angle), 0.269)
        p1 = (0.024 * math.cos(angle), 0.024 * math.sin(angle), 0.269)
        add_box_between(bm, p0, p1, 0.018, 0.014, 0)
        tangent = (-math.sin(angle), math.cos(angle), 0.0)
        add_cone(bm, p1, tangent, 0.016, 0.0044, 0.0044, 1, 24)
    return finish("TripodHub", bm, [brass, patina], bevel=0.0008)
