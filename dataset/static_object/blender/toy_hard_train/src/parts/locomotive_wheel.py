"""LocomotiveWheel — large spoked driving wheels (6 wheels total)."""
import math
import random
import bpy
import bmesh
from mathutils import Vector, Matrix
from parts._common import obj_from_bmesh, mat_bright_red_wheel

random.seed(0)

# Plan numbers:
# center (0.080, -0.140, 0.050) extents (0.025, 0.100, 0.100)
# 6 wheels total: 3 on right (+X), 3 on left (-X)
# Instances: LocomotiveWheel_0..LocomotiveWheel_5

def make_single_spoked_wheel(name: str, center: tuple, side_sign: float) -> bpy.types.Object:
    bm = bmesh.new()
    
    # 1. Outer rim (cylinder along X)
    bm_rim = bmesh.new()
    bmesh.ops.create_cone(
        bm_rim, cap_ends=True, segments=32,
        radius1=0.050, radius2=0.050, depth=0.016
    )
    bmesh.ops.rotate(bm_rim, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_rim.verts)
    
    # 2. Central hub (radius 0.016, thickness 0.020)
    bm_hub = bmesh.new()
    bmesh.ops.create_cone(
        bm_hub, cap_ends=True, segments=24,
        radius1=0.016, radius2=0.016, depth=0.020
    )
    bmesh.ops.rotate(bm_hub, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_hub.verts)

    # 3. 8 spokes connecting hub to rim
    for i in range(8):
        angle = i * (2 * math.pi / 8)
        bm_spoke = bmesh.new()
        bmesh.ops.create_cube(bm_spoke, size=1.0)
        bmesh.ops.scale(bm_spoke, vec=(0.010, 0.005, 0.035), verts=bm_spoke.verts)
        bmesh.ops.translate(bm_spoke, vec=(0, 0, 0.030), verts=bm_spoke.verts)
        bmesh.ops.rotate(bm_spoke, cent=(0,0,0), matrix=Matrix.Rotation(angle, 3, 'X'), verts=bm_spoke.verts)
        
        m_mesh = bpy.data.meshes.new("_tmp_spoke")
        bm_spoke.to_mesh(m_mesh)
        bm.from_mesh(m_mesh)
        bpy.data.meshes.remove(m_mesh)
        bm_spoke.free()

    # 4. Eccentric crank pin for the connecting rod (on the outer face)
    pin_x = side_sign * 0.010
    bm_pin = bmesh.new()
    bmesh.ops.create_cone(
        bm_pin, cap_ends=True, segments=16,
        radius1=0.005, radius2=0.005, depth=0.008
    )
    bmesh.ops.rotate(bm_pin, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_pin.verts)
    bm_pin_y = 0.0
    bm_pin_z = 0.0
    bmesh.ops.translate(bm_pin, vec=(pin_x, bm_pin_y, bm_pin_z), verts=bm_pin.verts)

    # Axle stub connecting wheel inner face to chassis
    # Chassis is at X in [-0.075, 0.075], wheel center is at +/-0.080
    axle_stub_x = -side_sign * 0.007
    bm_axle = bmesh.new()
    bmesh.ops.create_cone(
        bm_axle, cap_ends=True, segments=16,
        radius1=0.008, radius2=0.008, depth=0.006
    )
    bmesh.ops.rotate(bm_axle, cent=(0,0,0), matrix=Matrix.Rotation(math.radians(90.0), 3, 'Y'), verts=bm_axle.verts)
    bmesh.ops.translate(bm_axle, vec=(axle_stub_x, 0, 0), verts=bm_axle.verts)

    # Combine all parts
    for m in [bm_rim, bm_hub, bm_pin, bm_axle]:
        m_mesh = bpy.data.meshes.new("_tmp_wheel_part")
        m.to_mesh(m_mesh)
        bm.from_mesh(m_mesh)
        bpy.data.meshes.remove(m_mesh)
        m.free()

    # Translate the entire wheel to its center location
    bmesh.ops.translate(bm, vec=center, verts=bm.verts)

    obj = obj_from_bmesh(name, bm)
    obj.data.materials.append(mat_bright_red_wheel())
    return obj

def build_locomotive_wheel() -> list[bpy.types.Object]:
    y_positions = [-0.250, -0.140, -0.030]
    z_center = 0.050

    # Return LocomotiveWheel objects
    wheels = []
    # Right wheels: LocomotiveWheel_0 .. 2
    for idx, y in enumerate(y_positions):
        part_name = f"LocomotiveWheel_{idx}"
        w = make_single_spoked_wheel(part_name, (0.080, y, z_center), side_sign=1.0)
        wheels.append(w)
    # Left wheels: LocomotiveWheel_3 .. 5
    for idx, y in enumerate(y_positions):
        part_name = f"LocomotiveWheel_{idx+3}"
        w = make_single_spoked_wheel(part_name, (-0.080, y, z_center), side_sign=-1.0)
        wheels.append(w)

    return wheels
