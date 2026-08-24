"""DrumStickSecondary — second drumstick resting across the first stick on top of the drum."""
import bpy
import bmesh
import math
from mathutils import Matrix
from parts._common import make_material, obj_from_bmesh
from parts.drum_stick_primary import create_drumstick_mesh

# Plan numbers: center (0.015, 0.010, 0.186), extents (0.240, 0.360, 0.022)
# Bounds: x in [-0.105, 0.135], y in [-0.170, 0.190], z in [0.175, 0.197]
# Matching 5A hickory drumstick resting angled across the primary drumstick.

def build_drum_stick_secondary() -> bpy.types.Object:
    mat = make_material("DrumStickSecondaryMat", (0.86, 0.72, 0.52), roughness=0.45, metallic=0.0)
    bm = create_drumstick_mesh()
    
    # Secondary stick is angled across the first stick
    # Delta X = 0.240, Delta Y = 0.360 -> Angle theta = atan2(0.360, 0.240) = 0.9828 rad (~56.3 deg)
    # Let's orient it from (+X, -Y) towards (-X, +Y) or (-X, -Y) towards (+X, +Y)
    # atan2(0.360, -0.240) = 2.1588 rad (~123.7 deg) so it crosses the first stick
    theta = math.atan2(0.360, -0.240)
    rot = Matrix.Rotation(theta, 4, 'Z')
    bmesh.ops.transform(bm, matrix=rot, verts=bm.verts)
    
    # Add slight tilt in Z to rest naturally over the primary stick
    rot_pitch = Matrix.Rotation(0.04, 4, 'X')
    bmesh.ops.transform(bm, matrix=rot_pitch, verts=bm.verts)
    
    # Ensure exact extents (0.240, 0.360, 0.022)
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    curr_dx = max(xs) - min(xs)
    curr_dy = max(ys) - min(ys)
    curr_dz = max(zs) - min(zs)
    
    bmesh.ops.scale(bm, vec=(0.240 / curr_dx, 0.360 / curr_dy, 0.022 / curr_dz), verts=bm.verts)
    
    obj = obj_from_bmesh("DrumStickSecondary", bm, location=(0.015, 0.010, 0.186), material=mat)
    return obj
