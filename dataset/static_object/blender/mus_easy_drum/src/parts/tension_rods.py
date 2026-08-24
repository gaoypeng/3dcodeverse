"""TensionRods — threaded tuning bolts tensioning the top rim."""
import bpy
import bmesh
import math
from mathutils import Matrix
from parts._common import make_material, obj_from_bmesh

# Plan: 8 radial instances
# Base single-instance bbox for TensionRods at (0.000, -0.188, 0.138), extents (0.012, 0.012, 0.052)
# Z range [0.112, 0.164].
# Radial positions: angle = -pi/2 + i * (2*pi/8)

def build_tension_rods() -> list[bpy.types.Object]:
    mat = make_material("TensionRodsMat", (0.9, 0.9, 0.92), roughness=0.15, metallic=0.98)
    objs = []
    
    r_center = 0.188
    rod_h = 0.052
    rod_r = 0.003
    head_r = 0.0055
    head_h = 0.008
    
    for i in range(8):
        angle = -math.pi / 2.0 + i * (2 * math.pi / 8.0)
        cx = r_center * math.cos(angle)
        cy = r_center * math.sin(angle)
        cz = 0.138
        
        bm = bmesh.new()
        
        # Single seamless mesh for bolt + rod (ensuring 1 island per part)
        # Create profile of rod and bolt head along Z:
        # z from -rod_h/2 to rod_h/2
        z_bot = -rod_h / 2.0
        z_neck = rod_h / 2.0 - head_h
        z_top = rod_h / 2.0
        
        # Create cylinder for rod and cylinder/prism for head
        bmesh.ops.create_cone(
            bm,
            cap_ends=True,
            segments=16,
            radius1=rod_r,
            radius2=rod_r,
            depth=rod_h - head_h
        )
        bmesh.ops.translate(bm, vec=(0, 0, (z_bot + z_neck) / 2.0), verts=bm.verts)
        
        head_bm = bmesh.new()
        bmesh.ops.create_cone(
            head_bm,
            cap_ends=True,
            segments=16,
            radius1=head_r,
            radius2=head_r,
            depth=head_h
        )
        bmesh.ops.translate(head_bm, vec=(0, 0, (z_neck + z_top) / 2.0), verts=head_bm.verts)
        for v in head_bm.verts:
            bm.verts.new(v.co)
        bm.verts.ensure_lookup_table()
        offset = len(bm.verts) - len(head_bm.verts)
        for f in head_bm.faces:
            bm.faces.new([bm.verts[offset + v.index] for v in f.verts])
        head_bm.free()
        
        # Exact extents scale check (0.012, 0.012, 0.052)
        xs = [v.co.x for v in bm.verts]
        ys = [v.co.y for v in bm.verts]
        zs = [v.co.z for v in bm.verts]
        curr_dx = max(xs) - min(xs)
        curr_dy = max(ys) - min(ys)
        curr_dz = max(zs) - min(zs)
        bmesh.ops.scale(bm, vec=(0.012 / curr_dx, 0.012 / curr_dy, 0.052 / curr_dz), verts=bm.verts)
        
        rot_mat = Matrix.Rotation(i * (2 * math.pi / 8.0), 4, 'Z')
        bmesh.ops.transform(bm, matrix=rot_mat, verts=bm.verts)
        
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        
        obj = obj_from_bmesh(f"TensionRods_{i}", bm, location=(cx, cy, cz), material=mat)
        objs.append(obj)
        
    return objs
