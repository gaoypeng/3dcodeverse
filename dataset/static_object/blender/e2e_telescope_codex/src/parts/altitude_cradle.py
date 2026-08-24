"""Cast brass U-shaped altitude fork with paired ribbed knobs."""
import bpy
import bmesh

from parts._common import add_box_between, add_cone, add_lathe, finish, material

ALTITUDE_CRADLE_CENTER = (0.000, 0.000, 0.365)
ALTITUDE_CRADLE_EXTENTS = (0.080, 0.040, 0.050)


def build_altitude_cradle():
    brass = material("CradleBrass", (0.76, 0.48, 0.14), roughness=0.25, metallic=0.94)
    dark = material("CradleKnurlDark", (0.14, 0.095, 0.038), roughness=0.44, metallic=0.70)
    bm = bmesh.new()
    add_box_between(bm, (-0.032, 0, 0.358), (0.032, 0, 0.358), 0.018, 0.010, 0)
    for x in (-0.030, 0.030):
        add_box_between(bm, (x, 0, 0.344), (x, 0, 0.380), 0.016, 0.012, 0)
    # Two short bearing pins meet the tube trunnion without running through its barrel.
    add_cone(bm, (-0.029, 0, 0.378), (1, 0, 0), 0.012, 0.0065, 0.0065, 0, 32)
    add_cone(bm, (0.029, 0, 0.378), (1, 0, 0), 0.012, 0.0065, 0.0065, 0, 32)
    for x in (-0.036, 0.036):
        add_lathe(bm, (x, 0, 0.378), (1, 0, 0), [(-0.004, 0.010), (0.004, 0.010)], 1, 24, ribbed=True)
    add_box_between(bm, (-0.030, -0.020, 0.352), (-0.030, 0.020, 0.352), 0.008, 0.008, 0)
    add_box_between(bm, (0.030, -0.020, 0.352), (0.030, 0.020, 0.352), 0.008, 0.008, 0)
    return finish("AltitudeCradle", bm, [brass, dark], bevel=0.0010)
