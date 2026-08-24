"""BlockBottom — largest foundation cube at base."""
import bpy
from parts._common import build_alphabet_block

# Plan constants
SIDE_LEN = 0.100
BEVEL_W = 0.003
CENTER_Z = 0.050
ROT_Z_DEG = 0.0
# Red color for A
RED = (0.85, 0.12, 0.12)

def build_block_bottom() -> bpy.types.Object:
    """BlockBottom — solid wooden cube 0.100m with 3mm edge bevels, recessed panels & embossed 'A'."""
    return build_alphabet_block(
        name="BlockBottom",
        side_len=SIDE_LEN,
        bevel_w=BEVEL_W,
        center_z=CENTER_Z,
        rot_z_deg=ROT_Z_DEG,
        letter='A',
        letter_rgb=RED
    )
