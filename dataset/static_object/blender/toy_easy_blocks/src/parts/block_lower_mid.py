"""BlockLowerMid — second alphabet block from bottom."""
import bpy
from parts._common import build_alphabet_block

# Plan constants
SIDE_LEN = 0.085
BEVEL_W = 0.0025
CENTER_Z = 0.141
ROT_Z_DEG = 14.0
# Cobalt blue color for B
BLUE = (0.12, 0.28, 0.85)

def build_block_lower_mid() -> bpy.types.Object:
    """BlockLowerMid — solid wooden cube 0.085m, beveled, rotated +14 deg, embossed 'B'."""
    return build_alphabet_block(
        name="BlockLowerMid",
        side_len=SIDE_LEN,
        bevel_w=BEVEL_W,
        center_z=CENTER_Z,
        rot_z_deg=ROT_Z_DEG,
        letter='B',
        letter_rgb=BLUE
    )
