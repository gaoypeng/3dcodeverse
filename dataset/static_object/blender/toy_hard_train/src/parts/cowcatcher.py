"""Cowcatcher — wedge-shaped front track clearing grill."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_matte_black

random.seed(0)

# Plan numbers:
# center (0.000, -0.395, 0.055) extents (0.160, 0.070, 0.070)
# x in [-0.080, 0.080], y in [-0.430, -0.360], z in [0.020, 0.090]
# LocomotiveChassis is at y in [-0.380, 0.100], z in [0.060, 0.090]

def build_cowcatcher() -> bpy.types.Object:
    bm = bmesh.new()

    # Slat bars: front at -0.430, back at -0.380 (where chassis starts).
    # Overlap chassis front at y = -0.380 by exactly 1 mm (y in [-0.430, -0.379])
    z_min = 0.022
    z_max = 0.088
    n_slats = 5
    bar_h = 0.008
    bar_thick = 0.004

    for i in range(n_slats):
        t = i / (n_slats - 1)
        z_curr = z_min + t * (z_max - z_min - bar_h)
        y_apex = -0.430 + t * 0.040
        y_back = -0.379

        for side in [-1, 1]:
            bm_bar = bmesh.new()
            p0 = Vector((0.0, y_apex, z_curr))
            p1 = Vector((side * 0.080, y_back, z_curr))
            p2 = Vector((side * 0.080, y_back + bar_thick, z_curr))
            p3 = Vector((0.0, y_apex + bar_thick, z_curr))

            v0 = bm_bar.verts.new(p0)
            v1 = bm_bar.verts.new(p1)
            v2 = bm_bar.verts.new(p2)
            v3 = bm_bar.verts.new(p3)

            v4 = bm_bar.verts.new(p0 + Vector((0, 0, bar_h)))
            v5 = bm_bar.verts.new(p1 + Vector((0, 0, bar_h)))
            v6 = bm_bar.verts.new(p2 + Vector((0, 0, bar_h)))
            v7 = bm_bar.verts.new(p3 + Vector((0, 0, bar_h)))

            bm_bar.faces.new([v0, v1, v2, v3])
            bm_bar.faces.new([v7, v6, v5, v4])
            bm_bar.faces.new([v0, v4, v5, v1])
            bm_bar.faces.new([v1, v5, v6, v2])
            bm_bar.faces.new([v2, v6, v7, v3])
            bm_bar.faces.new([v3, v7, v4, v0])

            m_mesh = bpy.data.meshes.new("_tmp_bar")
            bm_bar.to_mesh(m_mesh)
            bm.from_mesh(m_mesh)
            bpy.data.meshes.remove(m_mesh)
            bm_bar.free()

    # Central nose rib
    bm_rib = bmesh.new()
    bmesh.ops.create_cube(bm_rib, size=1.0)
    bmesh.ops.scale(bm_rib, vec=(0.010, 0.010, 0.068), verts=bm_rib.verts)
    bmesh.ops.rotate(bm_rib, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(-25.0), 3, 'X'), verts=bm_rib.verts)
    bmesh.ops.translate(bm_rib, vec=(0.0, -0.410, 0.055), verts=bm_rib.verts)
    m_mesh = bpy.data.meshes.new("_tmp_rib")
    bm_rib.to_mesh(m_mesh)
    bm.from_mesh(m_mesh)
    bpy.data.meshes.remove(m_mesh)
    bm_rib.free()

    # End spacer to ensure Cowcatcher spans Y = [-0.430, -0.360]:
    # 2 side bottom skid plates from Y = -0.379 to -0.360 at Z = 0.022 (under the chassis, touching at z=0.060)
    for side in [-1, 1]:
        bm_skid = bmesh.new()
        bmesh.ops.create_cube(bm_skid, size=1.0)
        bmesh.ops.scale(bm_skid, vec=(0.012, 0.020, 0.005), verts=bm_skid.verts)
        bmesh.ops.translate(bm_skid, vec=(side * 0.074, -0.370, 0.0245), verts=bm_skid.verts)
        m_mesh = bpy.data.meshes.new("_tmp_skid")
        bm_skid.to_mesh(m_mesh)
        bm.from_mesh(m_mesh)
        bpy.data.meshes.remove(m_mesh)
        bm_skid.free()

    obj = obj_from_bmesh("Cowcatcher", bm)
    obj.data.materials.append(mat_matte_black())
    return obj
