"""BicycleWheelStand — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

Planned bounding boxes and centers:
- RepairStand:  center=(0.000, 0.220, 0.550), extents=(0.550, 0.550, 1.100)
- ForkAssembly: center=(0.000, -0.010, 0.760), extents=(0.420, 0.140, 0.720)
- BicycleWheel: center=(0.000, 0.000, 0.400), extents=(0.100, 0.660, 0.660)
- BrakeArmLeft: center=(-0.022, -0.038, 0.730), extents=(0.035, 0.025, 0.100)
- BrakeArmRight: center=(0.022, -0.038, 0.730), extents=(0.035, 0.025, 0.100)

Joint Pivots:
- SteeringJoint: (0.000, 0.000, 0.950), axis=(0, 0, 1)
- WheelAxle: (0.000, 0.000, 0.400), axis=(1, 0, 0)
- LeftBrakePivot: (-0.025, -0.035, 0.770), axis=(0, -1, 0)
- RightBrakePivot: (0.025, -0.035, 0.770), axis=(0, 1, 0)
"""

import bpy
import bmesh
import math
from mathutils import Vector, Matrix

def clean_mesh_islands(ob, min_rel_size=0.06):
    # Merge loose geometry or remove tiny degenerate pieces
    # In our case, let's keep all functional geometry connected or ensure they are solid.
    pass
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def create_material(name, color, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat

mat_stand = create_material("Mat_StandBlue", (0.05, 0.25, 0.75, 1.0), roughness=0.3, metallic=0.4)
mat_stand_dark = create_material("Mat_DarkSteel", (0.1, 0.1, 0.12, 1.0), roughness=0.5, metallic=0.8)
mat_carbon = create_material("Mat_CarbonFork", (0.03, 0.03, 0.03, 1.0), roughness=0.4, metallic=0.1)
mat_silver = create_material("Mat_SilverAlloy", (0.8, 0.8, 0.82, 1.0), roughness=0.2, metallic=0.9)
mat_rubber = create_material("Mat_Rubber", (0.02, 0.02, 0.02, 1.0), roughness=0.8, metallic=0.0)
mat_brake = create_material("Mat_BrakeArm", (0.15, 0.15, 0.16, 1.0), roughness=0.3, metallic=0.6)

def bmesh_add_cylinder(bm, radius, length, axis='Z', center=(0,0,0), segs=16):
    rot = Matrix.Identity(4)
    if axis == 'X':
        rot = Matrix.Rotation(math.pi/2, 4, 'Y')
    elif axis == 'Y':
        rot = Matrix.Rotation(math.pi/2, 4, 'X')
    
    res = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segs,
        radius1=radius,
        radius2=radius,
        depth=length
    )
    mat = Matrix.Translation(center) @ rot
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])

def bmesh_add_box(bm, center, size):
    res = bmesh.ops.create_cube(bm, size=1.0)
    mat = Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0))
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])

def bmesh_add_tube(bm, r_out, r_in, length, axis='Z', center=(0,0,0), segs=32):
    rot = Matrix.Identity(4)
    if axis == 'X':
        rot = Matrix.Rotation(math.pi/2, 4, 'Y')
    elif axis == 'Y':
        rot = Matrix.Rotation(math.pi/2, 4, 'X')
    
    verts_top_out, verts_top_in = [], []
    verts_bot_out, verts_bot_in = [], []
    z_top, z_bot = length / 2.0, -length / 2.0
    
    for i in range(segs):
        ang = 2 * math.pi * i / segs
        ca, sa = math.cos(ang), math.sin(ang)
        verts_top_out.append(bm.verts.new((r_out * ca, r_out * sa, z_top)))
        verts_top_in.append(bm.verts.new((r_in * ca, r_in * sa, z_top)))
        verts_bot_out.append(bm.verts.new((r_out * ca, r_out * sa, z_bot)))
        verts_bot_in.append(bm.verts.new((r_in * ca, r_in * sa, z_bot)))
    
    new_verts = verts_top_out + verts_top_in + verts_bot_out + verts_bot_in
    
    for i in range(segs):
        i_next = (i + 1) % segs
        bm.faces.new((verts_top_out[i], verts_top_out[i_next], verts_bot_out[i_next], verts_bot_out[i]))
        bm.faces.new((verts_top_in[i_next], verts_top_in[i], verts_bot_in[i], verts_bot_in[i_next]))
        bm.faces.new((verts_top_out[i], verts_top_in[i], verts_top_in[i_next], verts_top_out[i_next]))
        bm.faces.new((verts_bot_out[i_next], verts_bot_in[i_next], verts_bot_in[i], verts_bot_out[i]))
    
    mat = Matrix.Translation(center) @ rot
    bmesh.ops.transform(bm, matrix=mat, verts=new_verts)

# -----------------------------------------------------------------------------
# 1. RepairStand
# Target bbox: center=(0.000, 0.220, 0.550), extents=(0.550, 0.550, 1.100)
# X: [-0.275, 0.275], Y: [-0.055, 0.495], Z: [0.000, 1.100]
# -----------------------------------------------------------------------------
def build_repair_stand():
    bm = bmesh.new()
    
    # Base center hub at (0, 0.22, 0.035)
    # Tripod / H-base spanning exactly x in [-0.275, 0.275], y in [-0.055, 0.495], z in [0.0, 0.06]
    # Center junction at (0, 0.25, 0.03)
    bmesh_add_box(bm, center=(0.0, 0.25, 0.03), size=(0.10, 0.10, 0.06))
    
    # Rear leg: from (0, 0.25, 0.03) to (0, 0.48, 0.015)
    p_hub = Vector((0.0, 0.25, 0.03))
    p_rear = Vector((0.0, 0.48, 0.015))
    vec = p_rear - p_hub
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=12, radius1=0.018, radius2=0.018, depth=vec.length)
    rot_q = Vector((0,0,1)).rotation_difference(vec.normalized())
    mat = Matrix.Translation((p_hub + p_rear)*0.5) @ rot_q.to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
    # Rear rubber foot reaching y = 0.495, z = 0.000
    bmesh_add_box(bm, center=(0.0, 0.475, 0.015), size=(0.06, 0.04, 0.03))
    
    # Front-left and Front-right legs
    # Splayed outward at wide X (x = ±0.26) so they pass well outside the wheel (wheel is x in [-0.05, 0.05])
    for side in (-1, 1):
        p_front = Vector((side * 0.26, -0.04, 0.015))
        vec = p_front - p_hub
        res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=12, radius1=0.018, radius2=0.018, depth=vec.length)
        rot_q = Vector((0,0,1)).rotation_difference(vec.normalized())
        mat = Matrix.Translation((p_hub + p_front)*0.5) @ rot_q.to_matrix().to_4x4()
        bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
        # Rubber foot extending to x = ±0.275, y = -0.055, z = 0.000
        bmesh_add_box(bm, center=(side * 0.26, -0.04, 0.015), size=(0.05, 0.05, 0.03))

    # Upright post at (0, 0.35, z) from z=0.05 to z=1.08 (reaching max Z = 1.10)
    bmesh_add_cylinder(bm, radius=0.022, length=1.03, axis='Z', center=(0.0, 0.35, 0.565), segs=16)
    # Stand top cap at z = 1.09
    bmesh_add_cylinder(bm, radius=0.025, length=0.02, axis='Z', center=(0.0, 0.35, 1.09), segs=16)
    
    # Quick release clamp collars on upright
    bmesh_add_cylinder(bm, radius=0.030, length=0.035, axis='Z', center=(0.0, 0.35, 0.70), segs=16)
    bmesh_add_box(bm, center=(0.035, 0.35, 0.70), size=(0.04, 0.015, 0.02))
    
    # Horizontal arm from (0, 0.35, 0.95) forward to (0, 0.035, 0.95)
    arm_len = 0.315
    arm_y_center = 0.35 - arm_len/2.0
    bmesh_add_cylinder(bm, radius=0.020, length=arm_len, axis='Y', center=(0.0, arm_y_center, 0.95), segs=16)
    
    # Clamping head body at (0, 0.045, 0.95)
    bmesh_add_box(bm, center=(0.0, 0.045, 0.95), size=(0.055, 0.045, 0.055))
    
    # Outer clamp collar around steerer tube at (0, 0, 0.95)
    # Inner radius = 0.0165, outer radius = 0.024, length = 0.045 (z in [0.9275, 0.9725])
    bmesh_add_tube(bm, r_out=0.024, r_in=0.0165, length=0.045, axis='Z', center=(0.0, 0.0, 0.95), segs=20)
    # Tightening knob
    bmesh_add_cylinder(bm, radius=0.014, length=0.05, axis='X', center=(0.045, 0.02, 0.95), segs=12)

    me = bpy.data.meshes.new("repair_stand")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("repair_stand", me)
    ob.data.materials.append(mat_stand)
    bpy.context.scene.collection.objects.link(ob)
    return ob

# -----------------------------------------------------------------------------
# 2. ForkAssembly
# Target bbox: center=(0.000, -0.010, 0.760), extents=(0.420, 0.140, 0.720)
# X: [-0.210, 0.210], Y: [-0.080, 0.060], Z: [0.400, 1.120]
# -----------------------------------------------------------------------------
def build_fork_assembly():
    bm = bmesh.new()
    
    # Steerer tube: runs along Z through clamp (radius 0.014, z=0.78 to 1.08)
    bmesh_add_cylinder(bm, radius=0.014, length=0.30, axis='Z', center=(0.0, 0.0, 0.93), segs=16)
    
    # Stem at z=1.08: clamp + forward extension to (0, -0.065, 1.10)
    bmesh_add_cylinder(bm, radius=0.017, length=0.04, axis='Z', center=(0.0, 0.0, 1.08), segs=16)
    
    stem_vec = Vector((0.0, -0.065, 0.02))
    stem_len = stem_vec.length
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=12, radius1=0.014, radius2=0.014, depth=stem_len)
    rot_q = Vector((0,0,1)).rotation_difference(stem_vec.normalized())
    mat = Matrix.Translation(Vector((0.0, -0.0325, 1.09))) @ rot_q.to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
    
    # Handlebar clamp at (0, -0.065, 1.10)
    bmesh_add_cylinder(bm, radius=0.016, length=0.035, axis='X', center=(0.0, -0.065, 1.10), segs=16)
    
    # Flat handlebar: width 0.42m, radius 0.011, centered at (0, -0.065, 1.10)
    # Spans X in [-0.210, 0.210], Z top = 1.111 -> with grips reaches 1.120
    bmesh_add_cylinder(bm, radius=0.011, length=0.42, axis='X', center=(0.0, -0.065, 1.10), segs=16)
    # Grips
    bmesh_add_cylinder(bm, radius=0.014, length=0.08, axis='X', center=(-0.17, -0.065, 1.10), segs=16)
    bmesh_add_cylinder(bm, radius=0.014, length=0.08, axis='X', center=(0.17, -0.065, 1.10), segs=16)
    # Grip end plugs reaching Y/Z bounds
    bmesh_add_box(bm, center=(0.0, 0.045, 1.08), size=(0.02, 0.025, 0.02)) # rear stem clamp bolt (reaches +Y ~ 0.058)
    
    # Fork crown at z=0.78: width 0.09, depth 0.032, height 0.025
    bmesh_add_box(bm, center=(0.0, 0.0, 0.78), size=(0.09, 0.032, 0.025))
    
    # Brake mounting studs on front of crown at (±0.025, -0.025, 0.77)
    # Studs end at y = -0.029, leaving clearance to brake arms centered at y = -0.035
    bmesh_add_cylinder(bm, radius=0.004, length=0.012, axis='Y', center=(-0.025, -0.022, 0.77), segs=10)
    bmesh_add_cylinder(bm, radius=0.004, length=0.012, axis='Y', center=(0.025, -0.022, 0.77), segs=10)
    
    # Fork blades: left (x = -0.045 to -0.052) and right (x = 0.045 to 0.052)
    # Descend to dropouts at (±0.052, -0.012, 0.405)
    for side in (-1, 1):
        x_top = side * 0.042
        x_bot = side * 0.052
        p_top = Vector((x_top, 0.0, 0.77))
        p_bot = Vector((x_bot, -0.012, 0.405))
        blade_vec = p_bot - p_top
        L = blade_vec.length
        p_mid = (p_top + p_bot) * 0.5
        res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=12, radius1=0.012, radius2=0.008, depth=L)
        rot_q = Vector((0,0,1)).rotation_difference(blade_vec.normalized())
        mat = Matrix.Translation(p_mid) @ rot_q.to_matrix().to_4x4()
        bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
        
        # Dropouts: plate at bottom reaching z=0.400
        bmesh_add_box(bm, center=(side * 0.052, -0.012, 0.41), size=(0.006, 0.024, 0.02))

    me = bpy.data.meshes.new("fork_assembly")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("fork_assembly", me)
    ob.data.materials.append(mat_carbon)
    bpy.context.scene.collection.objects.link(ob)
    return ob

# -----------------------------------------------------------------------------
# 3. BicycleWheel
# Target bbox: center=(0.000, 0.000, 0.400), extents=(0.100, 0.660, 0.660)
# X: [-0.050, 0.050], Y: [-0.330, 0.330], Z: [0.070, 0.730]
# -----------------------------------------------------------------------------
def build_bicycle_wheel():
    bm = bmesh.new()
    
    # Axle along X-axis from x=-0.050 to x=0.050 (total width 0.100)
    bmesh_add_cylinder(bm, radius=0.0045, length=0.10, axis='X', center=(0.0, 0.0, 0.40), segs=12)
    
    # Hub center shell (radius 0.012, length 0.055)
    bmesh_add_cylinder(bm, radius=0.012, length=0.055, axis='X', center=(0.0, 0.0, 0.40), segs=16)
    # Hub flanges at x = ±0.030 (radius 0.020, thickness 0.003)
    bmesh_add_cylinder(bm, radius=0.020, length=0.003, axis='X', center=(-0.030, 0.0, 0.40), segs=16)
    bmesh_add_cylinder(bm, radius=0.020, length=0.003, axis='X', center=(0.030, 0.0, 0.40), segs=16)
    # Axle nuts at x = ±0.046
    bmesh_add_cylinder(bm, radius=0.008, length=0.008, axis='X', center=(-0.046, 0.0, 0.40), segs=12)
    bmesh_add_cylinder(bm, radius=0.008, length=0.008, axis='X', center=(0.046, 0.0, 0.40), segs=12)
    
    # Rim: 700c aluminum rim (outer radius = 0.31m, inner radius = 0.29m, width in X = 0.020m)
    bmesh_add_tube(bm, r_out=0.31, r_in=0.29, length=0.020, axis='X', center=(0.0, 0.0, 0.40), segs=48)
    
    # Tire: road tire (outer radius = 0.33m, inner radius = 0.308m, width in X = 0.024m)
    bmesh_add_tube(bm, r_out=0.33, r_in=0.308, length=0.024, axis='X', center=(0.0, 0.0, 0.40), segs=48)
    
    # 32 Radial spokes: thin cylinders from hub flanges (r=0.018) to rim inner wall (r=0.29)
    num_spokes_per_side = 16
    spoke_r = 0.001
    for side, fx in [(-1, -0.030), (1, 0.030)]:
        for i in range(num_spokes_per_side):
            ang = 2 * math.pi * (i + (0.5 if side == 1 else 0.0)) / num_spokes_per_side
            cy, cz = math.sin(ang), math.cos(ang)
            p_hub = Vector((fx, cy * 0.018, 0.40 + cz * 0.018))
            p_rim = Vector((0.0, cy * 0.29, 0.40 + cz * 0.29))
            
            vec = p_rim - p_hub
            L = vec.length
            p_mid = (p_hub + p_rim) * 0.5
            res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=6, radius1=spoke_r, radius2=spoke_r, depth=L)
            rot_q = Vector((0,0,1)).rotation_difference(vec.normalized())
            mat = Matrix.Translation(p_mid) @ rot_q.to_matrix().to_4x4()
            bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])

    me = bpy.data.meshes.new("bicycle_wheel")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("bicycle_wheel", me)
    ob.data.materials.append(mat_silver)
    bpy.context.scene.collection.objects.link(ob)
    return ob

# -----------------------------------------------------------------------------
# 4. BrakeArmLeft & BrakeArmRight
# Target bbox for Left: center=(-0.022, -0.038, 0.730), extents=(0.035, 0.025, 0.100)
# Target bbox for Right: center=(0.022, -0.038, 0.730), extents=(0.035, 0.025, 0.100)
# Left pivot: (-0.025, -0.035, 0.770)
# Right pivot: (0.025, -0.035, 0.770)
# Rim braking track: radius ~0.300 -> z = 0.700. Rim half-width in X is 0.010 (x = ±0.010).
# Brake pad at rest: x = ±0.016, y = -0.038, z = 0.690. Clearance to rim is 6mm!
# When rotated by 0.12 rad (6.88 deg):
# Left arm (axis 0 -1 0): pad rotates around -Y -> dx = (0.77 - 0.69) * sin(0.12) = 0.08 * 0.1197 = 0.0096m -> x moves to -0.016 + 0.0096 = -0.0064m.
# Rim is at x = -0.010, so pad penetrates by ~3.6mm if not careful.
# To prevent penetration at upper limit (0.12 rad), pad inner face at rest should be at x = ±0.020!
# At x = ±0.020, rotation by 0.12 rad moves pad by ~0.0095m to x = ±0.0105m (just touches rim at x=±0.010 without interpenetration!).
# -----------------------------------------------------------------------------

def build_brake_arm_left():
    bm = bmesh.new()
    pivot = Vector((-0.025, -0.035, 0.770))
    
    # Pivot boss ring
    bmesh_add_cylinder(bm, radius=0.006, length=0.012, axis='Y', center=(-0.025, -0.035, 0.770), segs=12)
    
    # Upper cable anchor arm extending upward/inward
    p_top = pivot + Vector((0.016, -0.003, 0.010))  # top reaches z = 0.780
    vec_up = p_top - pivot
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=8, radius1=0.0045, radius2=0.0035, depth=vec_up.length)
    rot_q = Vector((0,0,1)).rotation_difference(vec_up.normalized())
    mat = Matrix.Translation((pivot + p_top)*0.5) @ rot_q.to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
    # Cable anchor bolt
    bmesh_add_cylinder(bm, radius=0.0035, length=0.008, axis='Y', center=(p_top.x, p_top.y - 0.004, p_top.z))
    
    # Lower arm curving down to brake pad mount at (-0.024, -0.038, 0.690)
    p_pad_mount = Vector((-0.024, -0.038, 0.690))
    vec_down = p_pad_mount - pivot
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=10, radius1=0.005, radius2=0.004, depth=vec_down.length)
    rot_q = Vector((0,0,1)).rotation_difference(vec_down.normalized())
    mat = Matrix.Translation((pivot + p_pad_mount)*0.5) @ rot_q.to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
    
    # Brake shoe holder & rubber pad at z=0.685 to 0.695 (bottom reaches z=0.680)
    # Holder metal shoe (x from -0.038 to -0.024)
    bmesh_add_box(bm, center=(-0.029, -0.038, 0.687), size=(0.014, 0.020, 0.012))
    # Rubber pad facing the rim (x from -0.024 to -0.020)
    bmesh_add_box(bm, center=(-0.022, -0.038, 0.687), size=(0.004, 0.018, 0.010))

    me = bpy.data.meshes.new("brake_arm_left")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("brake_arm_left", me)
    ob.data.materials.append(mat_brake)
    bpy.context.scene.collection.objects.link(ob)
    return ob

def build_brake_arm_right():
    bm = bmesh.new()
    pivot = Vector((0.025, -0.035, 0.770))
    
    # Pivot boss ring
    bmesh_add_cylinder(bm, radius=0.006, length=0.012, axis='Y', center=(0.025, -0.035, 0.770), segs=12)
    
    # Upper cable stop arm extending upward/inward
    p_top = pivot + Vector((-0.016, -0.003, 0.010)) # top reaches z = 0.780
    vec_up = p_top - pivot
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=8, radius1=0.0045, radius2=0.0035, depth=vec_up.length)
    rot_q = Vector((0,0,1)).rotation_difference(vec_up.normalized())
    mat = Matrix.Translation((pivot + p_top)*0.5) @ rot_q.to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
    # Barrel adjuster
    bmesh_add_cylinder(bm, radius=0.0035, length=0.008, axis='Z', center=(p_top.x, p_top.y, p_top.z + 0.002))
    
    # Lower arm curving down to brake pad mount at (0.024, -0.038, 0.690)
    p_pad_mount = Vector((0.024, -0.038, 0.690))
    vec_down = p_pad_mount - pivot
    res = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=10, radius1=0.005, radius2=0.004, depth=vec_down.length)
    rot_q = Vector((0,0,1)).rotation_difference(vec_down.normalized())
    mat = Matrix.Translation((pivot + p_pad_mount)*0.5) @ rot_q.to_matrix().to_4x4()
    bmesh.ops.transform(bm, matrix=mat, verts=res['verts'])
    
    # Brake shoe holder & rubber pad at z=0.685 to 0.695 (bottom reaches z=0.680)
    # Holder metal shoe (x from 0.024 to 0.038)
    bmesh_add_box(bm, center=(0.029, -0.038, 0.687), size=(0.014, 0.020, 0.012))
    # Rubber pad facing the rim (x from 0.020 to 0.024)
    bmesh_add_box(bm, center=(0.022, -0.038, 0.687), size=(0.004, 0.018, 0.010))

    me = bpy.data.meshes.new("brake_arm_right")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("brake_arm_right", me)
    ob.data.materials.append(mat_brake)
    bpy.context.scene.collection.objects.link(ob)
    return ob

# Build all links
build_repair_stand()
build_fork_assembly()
build_bicycle_wheel()
build_brake_arm_left()
build_brake_arm_right()

# Sanity check
for _n in ['repair_stand', 'fork_assembly', 'bicycle_wheel', 'brake_arm_left', 'brake_arm_right']:
    assert _n in bpy.data.objects, _n
