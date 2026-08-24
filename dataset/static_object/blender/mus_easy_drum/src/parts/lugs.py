"""Lugs — casing brackets mounted to shell to receive tension rods."""
import bpy
import bmesh
import math
from mathutils import Matrix
from parts._common import make_material, obj_from_bmesh

# Plan: 8 radial instances
# Base single-instance bbox for Lugs at (0.000, -0.186, 0.085), extents (0.024, 0.022, 0.060)
# Drum shell outer radius is 0.178.
# Lug radial center is 0.186. Depth in radial direction is 0.022 (from r=0.175 to r=0.197).
# Overlap with drum shell is from 0.175 to 0.178 (3 mm overlap, perfect weld!).
# Tangential width is 0.024 (±0.012). Height is 0.060 (z from 0.055 to 0.115).

def build_lugs() -> list[bpy.types.Object]:
    mat = make_material("LugsMat", (0.85, 0.85, 0.88), roughness=0.2, metallic=0.95)
    objs = []
    
    r_center = 0.186
    w_x = 0.024
    d_y = 0.022
    h_z = 0.060
    
    for i in range(8):
        angle = -math.pi / 2.0 + i * (2 * math.pi / 8.0)
        cx = r_center * math.cos(angle)
        cy = r_center * math.sin(angle)
        cz = 0.085
        
        bm = bmesh.new()
        
        # Local space: X is tangent, Y is radial, Z is vertical
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(w_x, d_y, h_z), verts=bm.verts)
        
        # Chamfer top and bottom
        for v in bm.verts:
            if abs(v.co.z) > 0.020:
                v.co.x *= 0.85
                
        # To align local Y with radial direction at angle:
        # At i=0 (angle = -pi/2), radial direction is (0, -1, 0).
        # In local space, -Y is inwards, +Y is outwards (or vice versa).
        # Rotate around Z by (angle - (-pi/2)) = i * (2*pi/8):
        rot_mat = Matrix.Rotation(i * (2 * math.pi / 8.0), 4, 'Z')
        bmesh.ops.transform(bm, matrix=rot_mat, verts=bm.verts)
        
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
        obj = obj_from_bmesh(f"Lugs_{i}", bm, location=(cx, cy, cz), material=mat)
        objs.append(obj)
        
    return objs
