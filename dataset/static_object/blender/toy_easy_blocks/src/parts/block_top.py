"""BlockTop — smallest topmost alphabet block."""
import bpy
from parts._common import build_alphabet_block

# Plan constants
SIDE_LEN = 0.040
BEVEL_W = 0.0015
CENTER_Z = 0.322
ROT_Z_DEG = 56.0
# Vibrant orange color for E
ORANGE = (0.95, 0.40, 0.05)

def build_block_top() -> bpy.types.Object:
    """BlockTop — solid wooden cube 0.040m, beveled, rotated +56 deg, embossed 'E'."""
    return build_alphabet_block(
        name="BlockTop",
        side_len=SIDE_LEN,
        bevel_w=BEVEL_W,
        center_z=CENTER_Z,
        rot_z_deg=ROT_Z_DEG,
        letter='E',
        letter_rgb=ORANGE
    )
