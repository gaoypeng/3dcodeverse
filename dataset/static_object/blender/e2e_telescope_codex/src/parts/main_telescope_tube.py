"""Eight-degree elevated polished brass refractor barrel and pivot trunnions."""
import math

import bpy
import bmesh
from mathutils import Vector

from parts._common import add_cone, add_lathe, finish, material

MAIN_TELESCOPE_TUBE_CENTER = (0.000, -0.020, 0.380)
MAIN_TELESCOPE_TUBE_EXTENTS = (0.050, 0.240, 0.060)
TILT = math.radians(8.0)
AXIS = Vector((0.0, math.cos(TILT), -math.sin(TILT)))


def build_main_telescope_tube():
    brass = material("TubeMirrorBrass", (0.90, 0.64, 0.22), roughness=0.16, metallic=0.98)
    antique = material("TubeAntiqueBands", (0.58, 0.33, 0.085), roughness=0.32, metallic=0.90)
    bm = bmesh.new()
    # Classic stepped barrel profile; negative s is the raised forward end.
    profile = [(-0.120, 0.0165), (-0.112, 0.0180), (-0.103, 0.0180),
               (-0.098, 0.0168), (-0.056, 0.0168), (-0.051, 0.0205),
               (-0.043, 0.0205), (-0.038, 0.0170), (0.055, 0.0170),
               (0.060, 0.0200), (0.068, 0.0200), (0.073, 0.0165),
               (0.112, 0.0165), (0.120, 0.0150)]
    add_lathe(bm, MAIN_TELESCOPE_TUBE_CENTER, AXIS, profile, 0, 64)
    # Dark narrow retaining grooves contrast the two raised barrel rings.
    for s, radius in ((-0.050, 0.0210), (0.061, 0.0205)):
        center = Vector(MAIN_TELESCOPE_TUBE_CENTER) + AXIS * s
        add_lathe(bm, center, AXIS, [(-0.0022, radius), (0.0022, radius)], 1, 48)
    # Side trunnion passes through the barrel and visibly seats in the fork.
    add_cone(bm, MAIN_TELESCOPE_TUBE_CENTER, (1, 0, 0), 0.050, 0.007, 0.007, 1, 32)
    for x in (-0.021, 0.021):
        add_lathe(bm, (x, -0.020, 0.380), (1, 0, 0), [(-0.004, 0.009), (0.004, 0.009)], 0, 32)
    return finish("MainTelescopeTube", bm, [brass, antique], bevel=0.00045)
