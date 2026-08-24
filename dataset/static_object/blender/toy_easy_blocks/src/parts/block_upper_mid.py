"""BlockUpperMid — fourth alphabet block from bottom."""
import bpy
from parts._common import build_alphabet_block

# Plan constants
SIDE_LEN = 0.055
BEVEL_W = 0.0018
CENTER_Z = 0.277
ROT_Z_DEG = 42.0
# Forest green color for D
GREEN = (0.12, 0.55, 0.22)

def build_block_upper_mid() -> bpy.types.Object:
    """BlockUpperMid — solid wooden cube 0.055m, beveled, rotated +42 deg, embossed 'D'."""
    return build_alphabet_block(
        name="BlockUpperMid",
        side_len=SIDE_LEN,
        bevel_w=BEVEL_W,
        center_z=CENTER_Z,
        rot_z_deg=ROT_Z_DEG,
        letter='D',
        letter_rgb=GREEN
    )
