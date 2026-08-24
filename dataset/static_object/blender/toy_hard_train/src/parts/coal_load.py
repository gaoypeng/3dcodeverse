"""CoalLoad — mounded coal pile inside the tender."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_matte_coal

random.seed(0)

# Plan numbers:
# center (0.000, 0.285, 0.165) extents (0.130, 0.210, 0.050)
# x in [-0.065, 0.065], y in [0.180, 0.390], z in [0.140, 0.190]
# Sits inside top opening of TenderChassis (cavity extends down to z=0.080, top rim at z=0.180)

def build_coal_load() -> bpy.types.Object:
    bm = bmesh.new()

    # Create a base grid / faceted mound that fits exactly within:
    # X: 0.130 (-0.065 to 0.065)
    # Y: 0.210 (0.180 to 0.390)
    # Z: 0.050 (0.140 to 0.190)

    # Subdivided grid displaced into a faceted pyramid/dome mound
    bmesh.ops.create_grid(bm, x_segments=8, y_segments=12, size=1.0)
    # Grid is 1x1 at z=0. Scale X to 0.130, Y to 0.210
    bmesh.ops.scale(bm, vec=(0.130, 0.210, 1.0), verts=bm.verts)

    # Shape vertices into a mounded coal pile
    for v in bm.verts:
        # Normalized coords in [-1, 1]
        nx = v.co.x / 0.065
        ny = (v.co.y) / 0.105 # in [-1, 1]
        dist_sq = nx*nx + ny*ny
        # Mound height profile
        height_norm = max(0.0, 1.0 - dist_sq * 0.75)
        # Add random faceting
        noise = (random.random() - 0.5) * 0.15 * height_norm
        v.co.z = 0.140 + height_norm * 0.045 + noise
        # Clamp to plan bounds
        v.co.z = min(0.190, max(0.140, v.co.z))

    # Extrude grid downward to form a solid bottom plug at z = 0.140
    edges_boundary = [e for e in bm.edges if e.is_boundary]
    res = bmesh.ops.extrude_edge_only(bm, edges=edges_boundary)
    extruded_verts = [v for v in res['geom'] if isinstance(v, bmesh.types.BMVert)]
    for v in extruded_verts:
        v.co.z = 0.140

    # Cap bottom face
    bmesh.ops.edgeloop_fill(bm, edges=[e for e in bm.edges if e.is_boundary])

    # Offset to Y center 0.285
    bmesh.ops.translate(bm, vec=(0.0, 0.285, 0.0), verts=bm.verts)

    # Triangulate to make faceted coal chunks appearance
    bmesh.ops.triangulate(bm, faces=bm.faces[:])

    # Ensure bounds strictly match plan
    # Measure and scale if needed
    min_x = min(v.co.x for v in bm.verts)
    max_x = max(v.co.x for v in bm.verts)
    min_y = min(v.co.y for v in bm.verts)
    max_y = max(v.co.y for v in bm.verts)
    min_z = min(v.co.z for v in bm.verts)
    max_z = max(v.co.z for v in bm.verts)

    # Precise fitting to plan extents
    target_sx = 0.130 / (max_x - min_x) if max_x > min_x else 1.0
    target_sy = 0.210 / (max_y - min_y) if max_y > min_y else 1.0
    target_sz = 0.050 / (max_z - min_z) if max_z > min_z else 1.0

    bmesh.ops.scale(bm, vec=(target_sx, target_sy, target_sz), verts=bm.verts)

    # Re-center
    cur_cx = (min(v.co.x for v in bm.verts) + max(v.co.x for v in bm.verts)) / 2.0
    cur_cy = (min(v.co.y for v in bm.verts) + max(v.co.y for v in bm.verts)) / 2.0
    cur_cz = (min(v.co.z for v in bm.verts) + max(v.co.z for v in bm.verts)) / 2.0

    bmesh.ops.translate(bm, vec=(0.0 - cur_cx, 0.285 - cur_cy, 0.165 - cur_cz), verts=bm.verts)

    obj = obj_from_bmesh("CoalLoad", bm)
    obj.data.materials.append(mat_matte_coal())
    return obj
