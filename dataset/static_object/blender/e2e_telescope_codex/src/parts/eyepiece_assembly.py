"""Narrow draw tube, knurled focus wheel, and flared rear eye cup."""
import math

import bpy
import bmesh
from mathutils import Vector

from parts._common import add_lathe, finish, material

EYEPIECE_ASSEMBLY_CENTER = (0.000, 0.130, 0.360)
EYEPIECE_ASSEMBLY_EXTENTS = (0.032, 0.090, 0.036)
TILT = math.radians(8.0)
AXIS = Vector((0.0, math.cos(TILT), -math.sin(TILT)))


def build_eyepiece_assembly():
    brass = material("EyepieceBrass", (0.78, 0.48, 0.13), roughness=0.25, metallic=0.94)
    dark = material("EyepieceDarkKnurl", (0.075, 0.055, 0.030), roughness=0.48, metallic=0.62)
    bm = bmesh.new()
    profile = [(-0.031, 0.0120), (-0.028, 0.0120), (-0.026, 0.0145),
               (-0.020, 0.0145), (-0.016, 0.0105), (0.015, 0.0105),
               (0.021, 0.0130), (0.030, 0.0160), (0.041, 0.0160),
               (0.045, 0.0125)]
    add_lathe(bm, EYEPIECE_ASSEMBLY_CENTER, AXIS, profile, 0, 48)
    focus_center = Vector(EYEPIECE_ASSEMBLY_CENTER) + AXIS * -0.021
    add_lathe(bm, focus_center, AXIS, [(-0.005, 0.0152), (0.005, 0.0152)], 1, 32, ribbed=True)
    cup_center = Vector(EYEPIECE_ASSEMBLY_CENTER) + AXIS * 0.036
    add_lathe(bm, cup_center, AXIS, [(-0.005, 0.0156), (0.005, 0.0156)], 1, 40, ribbed=True)
    return finish("EyepieceAssembly", bm, [brass, dark], bevel=0.00035)
