"""BlockMiddle — middle alphabet block."""
import bpy
from parts._common import build_alphabet_block

# Plan constants
SIDE_LEN = 0.070
BEVEL_W = 0.002
CENTER_Z = 0.216
ROT_Z_DEG = 28.0
# Sunny golden yellow color for C
YELLOW = (0.92, 0.75, 0.10)

def build_block_middle() -> bpy.types.Object:
    """BlockMiddle — solid wooden cube 0.070m, beveled, rotated +28 deg, embossed 'C'."""
    return build_alphabet_block(
        name="BlockMiddle",
        side_len=SIDE_LEN,
        bevel_w=BEVEL_W,
        center_z=CENTER_Z,
        rot_z_deg=ROT_Z_DEG,
        letter='C',
        letter_rgb=YELLOW
    )
