"""Stepped and knurled brass azimuth riser."""
import bpy
import bmesh

from parts._common import add_lathe, finish, material

AZIMUTH_COLUMN_CENTER = (0.000, 0.000, 0.320)
AZIMUTH_COLUMN_EXTENTS = (0.035, 0.035, 0.060)


def build_azimuth_column():
    brass = material("AzimuthPolishedBrass", (0.84, 0.57, 0.20), roughness=0.20, metallic=0.97)
    patina = material("AzimuthKnurlPatina", (0.23, 0.16, 0.055), roughness=0.40, metallic=0.76)
    bm = bmesh.new()
    add_lathe(bm, (0, 0, 0.320), (0, 0, 1),
              [(-0.024, 0.0155), (-0.021, 0.0175), (-0.016, 0.0175),
               (-0.012, 0.012), (0.012, 0.012), (0.016, 0.015),
               (0.024, 0.015), (0.030, 0.0105)], 0, 48)
    add_lathe(bm, (0, 0, 0.304), (0, 0, 1), [(-0.0045, 0.0162), (0.0045, 0.0162)], 1, 32, ribbed=True)
    return finish("AzimuthColumn", bm, [brass, patina], bevel=0.0005)
