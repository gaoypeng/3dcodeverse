"""Three-armed brass tripod spreader joined into one measured plan part."""
import math

import bpy
import bmesh

from parts._common import add_box_between, add_cone, add_lathe, finish, material

TRIPOD_SPREADER_CENTER = (0.000, 0.000, 0.100)
TRIPOD_SPREADER_EXTENTS = (0.180, 0.180, 0.020)
ANGLES_DEG = (30.0, 150.0, 270.0)


def build_tripod_spreader():
    brass = material("SpreaderBrass", (0.78, 0.50, 0.16), roughness=0.26, metallic=0.94)
    patina = material("SpreaderPatina", (0.20, 0.16, 0.065), roughness=0.46, metallic=0.72)
    bm = bmesh.new()
    add_lathe(bm, (0, 0, 0.100), (0, 0, 1), [(-0.007, 0.016), (0.007, 0.016)], 0, 40)
    add_lathe(bm, (0, 0, 0.100), (0, 0, 1), [(-0.010, 0.008), (0.010, 0.008)], 1, 32, ribbed=True)
    for degrees in ANGLES_DEG:
        angle = math.radians(degrees)
        inner = (0.010 * math.cos(angle), 0.010 * math.sin(angle), 0.100)
        outer = (0.084 * math.cos(angle), 0.084 * math.sin(angle), 0.100)
        add_box_between(bm, inner, outer, 0.010, 0.006, 0)
        add_cone(bm, outer, (0, 0, 1), 0.014, 0.007, 0.007, 0, 24)
        add_cone(bm, outer, (0, 0, 1), 0.020, 0.0026, 0.0026, 1, 20)
    # A period-correct shallow V brace ties the rear pair and completes the 0.18 m spread.
    rear_left = (0.084 * math.cos(math.radians(30)), 0.084 * math.sin(math.radians(30)), 0.100)
    rear_right = (0.084 * math.cos(math.radians(150)), 0.084 * math.sin(math.radians(150)), 0.100)
    apex = (0.0, 0.090, 0.100)
    add_box_between(bm, rear_left, apex, 0.006, 0.0045, 0)
    add_box_between(bm, apex, rear_right, 0.006, 0.0045, 0)
    return finish("TripodSpreader", bm, [brass, patina], bevel=0.0007)
