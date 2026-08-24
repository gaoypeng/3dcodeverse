"""Stepped front bell, projecting sunshade and convex optical glass objective."""
import math

import bpy
import bmesh
from mathutils import Vector

from parts._common import add_ellipsoid, add_lathe, finish, material

OBJECTIVE_LENS_ASSEMBLY_CENTER = (0.000, -0.170, 0.400)
OBJECTIVE_LENS_ASSEMBLY_EXTENTS = (0.046, 0.070, 0.050)
TILT = math.radians(8.0)
AXIS = Vector((0.0, math.cos(TILT), -math.sin(TILT)))


def build_objective_lens_assembly():
    brass = material("ObjectiveAntiqueBrass", (0.82, 0.53, 0.16), roughness=0.22, metallic=0.96)
    shadow = material("ObjectiveInnerShadow", (0.055, 0.045, 0.028), roughness=0.38, metallic=0.52)
    glass = material("ObjectiveOpticalGlass", (0.17, 0.48, 0.62), roughness=0.06, metallic=0.05, alpha=0.42)
    bm = bmesh.new()
    # s increases rearward, so the hood lip and glass are on the negative/front end.
    profile = [(-0.035, 0.0220), (-0.029, 0.0230), (-0.021, 0.0230),
               (-0.016, 0.0215), (0.008, 0.0215), (0.014, 0.0225),
               (0.022, 0.0225), (0.027, 0.0190), (0.032, 0.0180)]
    add_lathe(bm, OBJECTIVE_LENS_ASSEMBLY_CENTER, AXIS, profile, 0, 64)
    front = Vector(OBJECTIVE_LENS_ASSEMBLY_CENTER) + AXIS * -0.030
    add_lathe(bm, front, AXIS, [(-0.0022, 0.0198), (0.0022, 0.0198)], 1, 56)
    # Shallow ellipsoid gives the front a visibly convex blue-green lens surface.
    lens_center = Vector(OBJECTIVE_LENS_ASSEMBLY_CENTER) + AXIS * -0.0365
    add_ellipsoid(bm, lens_center, AXIS, (0.0186, 0.0186, 0.0023), 2, 48, 16)
    return finish("ObjectiveLensAssembly", bm, [brass, shadow, glass], bevel=0.00035)
