"""FoldingChair — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

Classic wooden folding chair measuring 0.46 × 0.52 × 0.80 m with an articulating seat pan
and pivoting front leg assembly that fold flat against the rear frame.

Links:
- main_frame: stationary rear legs + backrest slats + cross rungs + pivot connections
- seat: pivoting slatted seat pan hinged at (0, 0.09, 0.44)
- front_leg_frame: pivoting front legs + rungs hinged at (0, -0.04, 0.42)
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear default scene objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def create_material(name, base_color, roughness=0.35, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = base_color
        bsdf.inputs['Roughness'].default_value = roughness
        bsdf.inputs['Metallic'].default_value = metallic
    return mat

mat_wood = create_material("BeechWood", (0.82, 0.64, 0.44, 1.0), roughness=0.35, metallic=0.0)
mat_wood_dark = create_material("BeechWoodAccent", (0.76, 0.55, 0.36, 1.0), roughness=0.35, metallic=0.0)

def assign_material(obj, mat):
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

def make_cylinder_mesh(bm, p1, p2, radius, segments=16):
    """Adds a cylinder between two 3D points p1 and p2 into bmesh."""
    v1 = Vector(p1)
    v2 = Vector(p2)
    axis = v2 - v1
    length = axis.length
    if length < 1e-6:
        return
    center = (v1 + v2) / 2.0
    dir_vec = axis.normalized()
    
    rot = Vector((0, 0, 1)).rotation_difference(dir_vec).to_matrix().to_4x4()
    trans = Matrix.Translation(center)
    mat = trans @ rot
    
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=length,
        matrix=mat
    )

def make_box_mesh(bm, center, size, rot_rpy=(0,0,0)):
    """Adds an oriented box into bmesh."""
    mat_rot = Matrix.Rotation(rot_rpy[0], 4, 'X') @ Matrix.Rotation(rot_rpy[1], 4, 'Y') @ Matrix.Rotation(rot_rpy[2], 4, 'Z')
    mat_trans = Matrix.Translation(Vector(center))
    mat = mat_trans @ mat_rot
    bmesh.ops.create_cube(bm, size=1.0, matrix=mat @ Matrix.Diagonal((*size, 1.0)))

def make_slanted_beam(bm, p_bot, p_top, width_x, thickness_normal):
    """Creates a beam between p_bot and p_top in YZ plane with given X width and normal thickness."""
    v_bot = Vector(p_bot)
    v_top = Vector(p_top)
    delta = v_top - v_bot
    length = delta.length
    center = (v_bot + v_top) / 2.0
    angle_x = -math.atan2(delta.y, delta.z)
    make_box_mesh(bm, center, (width_x, thickness_normal, length), rot_rpy=(angle_x, 0, 0))


# ==============================================================================
# 1. MAIN FRAME
# Target bbox: centre (0.000, 0.080, 0.400), extents (0.460, 0.220, 0.800)
# Rear uprights: ground at (y=0.180, z=0.000) up to top at (y=0.000, z=0.800)
# X bounds: ±0.230. Leg outer X = ±0.230, inner X = ±0.206 (leg_w = 0.024)
# ==============================================================================
bm_main = bmesh.new()

main_outer_x = 0.230
main_leg_w = 0.024
main_leg_th = 0.024

p_rear_bot = Vector((0, 0.180, 0.000))
p_rear_top = Vector((0, 0.000, 0.800))
delta_rear = p_rear_top - p_rear_bot
angle_rear = -math.atan2(delta_rear.y, delta_rear.z)

def get_rear_y(z):
    return p_rear_bot.y + (z - p_rear_bot.z) / (p_rear_top.z - p_rear_bot.z) * (p_rear_top.y - p_rear_bot.y)

# Rear upright side beams
for side in [-1, 1]:
    x_c = side * (main_outer_x - main_leg_w / 2)
    p_b = Vector((x_c, p_rear_bot.y, p_rear_bot.z))
    p_t = Vector((x_c, p_rear_top.y, p_rear_top.z))
    make_slanted_beam(bm_main, p_b, p_t, main_leg_w, main_leg_th)

# Backrest slats spanning between rear uprights (z around 0.71 and 0.60)
# Mounted flush to the rear of the uprights at y_slat = get_rear_y + 0.024
for z_slat in [0.71, 0.60]:
    y_slat = get_rear_y(z_slat) + 0.030
    make_box_mesh(bm_main, (0.0, y_slat, z_slat), (main_outer_x * 2 - 0.01, 0.010, 0.065), rot_rpy=(angle_rear, 0, 0))

# Top connecting rail at z=0.795
y_top_rail = get_rear_y(0.795) + 0.020
make_box_mesh(bm_main, (0.0, y_top_rail, 0.795), (main_outer_x * 2 - 0.01, 0.018, 0.022), rot_rpy=(angle_rear, 0, 0))

# Rear lower ground rung at z=0.10
y_rung_low = get_rear_y(0.10)
make_cylinder_mesh(bm_main, (-main_outer_x + 0.01, y_rung_low, 0.10), (main_outer_x - 0.01, y_rung_low, 0.10), radius=0.009)

# Rear middle rung at z=0.35 (behind pivot at y=get_rear_y(0.35)+0.016)
y_rung_mid = get_rear_y(0.35) + 0.025
make_cylinder_mesh(bm_main, (-main_outer_x + 0.01, y_rung_mid, 0.35), (main_outer_x - 0.01, y_rung_mid, 0.35), radius=0.007)

# Side support struts connecting rear uprights to front pivot at (0, -0.040, 0.420)
for side in [-1, 1]:
    x_c = side * (main_outer_x - main_leg_w / 2)
    # Pivot on main_frame for front leg pivot at (y=-0.040, z=0.420)
    make_cylinder_mesh(bm_main, (x_c, -0.040, 0.420), (side * 0.198, -0.040, 0.420), radius=0.006)

bmesh.ops.remove_doubles(bm_main, verts=bm_main.verts, dist=0.0005)
me_main = bpy.data.meshes.new('main_frame')
bm_main.to_mesh(me_main)
bm_main.free()
main_frame = bpy.data.objects.new('main_frame', me_main)
bpy.context.scene.collection.objects.link(main_frame)
assign_material(main_frame, mat_wood)


# ==============================================================================
# 2. FRONT LEG FRAME
# Target bbox: centre (0.000, -0.130, 0.220), extents (0.420, 0.200, 0.440)
# Joint 'front_leg_pivot' pivot: (0.000, -0.040, 0.420)
# ==============================================================================
bm_fleg = bmesh.new()

fleg_outer_x = 0.198
fleg_w = 0.018
fleg_th = 0.020

p_fleg_bot = Vector((0, -0.235, 0.000))
p_fleg_top = Vector((0, -0.040, 0.420))
delta_fleg = p_fleg_top - p_fleg_bot

def get_fleg_y(z):
    return p_fleg_bot.y + (z - p_fleg_bot.z) / (p_fleg_top.z - p_fleg_bot.z) * (p_fleg_top.y - p_fleg_bot.y)

for side in [-1, 1]:
    x_c = side * (fleg_outer_x - fleg_w / 2)
    p_b = Vector((x_c, p_fleg_bot.y, p_fleg_bot.z))
    p_t = Vector((x_c, p_fleg_top.y, p_fleg_top.z))
    make_slanted_beam(bm_fleg, p_b, p_t, fleg_w, fleg_th)
    make_cylinder_mesh(bm_fleg, (x_c - side*0.008, -0.040, 0.420), (x_c + side*0.008, -0.040, 0.420), radius=0.008)

fleg_inner_x = fleg_outer_x - fleg_w

# Upper support crossbar at top pivot (y=-0.040, z=0.420)
make_cylinder_mesh(bm_fleg, (-fleg_outer_x + 0.005, -0.040, 0.420), (fleg_outer_x - 0.005, -0.040, 0.420), radius=0.007)

# Lower front rung at z=0.12
y_f_low = get_fleg_y(0.12)
make_cylinder_mesh(bm_fleg, (-fleg_outer_x + 0.005, y_f_low, 0.12), (fleg_outer_x - 0.005, y_f_low, 0.12), radius=0.008)

# Mid front rung at z=0.25
y_f_mid = get_fleg_y(0.25)
make_cylinder_mesh(bm_fleg, (-fleg_outer_x + 0.005, y_f_mid, 0.25), (fleg_outer_x - 0.005, y_f_mid, 0.25), radius=0.008)

bmesh.ops.remove_doubles(bm_fleg, verts=bm_fleg.verts, dist=0.0005)
me_fleg = bpy.data.meshes.new('front_leg_frame')
bm_fleg.to_mesh(me_fleg)
bm_fleg.free()
front_leg_frame = bpy.data.objects.new('front_leg_frame', me_fleg)
bpy.context.scene.collection.objects.link(front_leg_frame)
assign_material(front_leg_frame, mat_wood)


# ==============================================================================
# 3. SEAT
# Target bbox: centre (0.000, -0.070, 0.440), extents (0.390, 0.360, 0.030)
# Joint 'seat_hinge' pivot: (0.000, 0.090, 0.440)
# Side rails and slats kept strictly under z=0.440 near the hinge line (y > 0.02)
# ==============================================================================
bm_seat = bmesh.new()

seat_w = 0.380
seat_side_w = 0.018
seat_z = 0.438
seat_th = 0.012

y_front_edge = -0.265
y_rear_hinge = 0.090
seat_len_y = y_rear_hinge - y_front_edge
y_c_seat = (y_front_edge + y_rear_hinge) / 2.0

for side in [-1, 1]:
    x_rail = side * (seat_w / 2 - seat_side_w / 2)
    # Main side rail
    make_box_mesh(bm_seat, (x_rail, (y_front_edge + 0.000)/2, seat_z), (seat_side_w, 0.000 - y_front_edge, seat_th))
    # Rear hinge lug sleeve around (0, 0.090, 0.440) reaching into rear upright
    make_cylinder_mesh(bm_seat, (x_rail - side*0.006, 0.090, 0.440), (side * 0.205, 0.090, 0.440), radius=0.004)

# Front cross rail of seat
make_box_mesh(bm_seat, (0.0, y_front_edge + 0.010, seat_z), (seat_w, 0.020, seat_th))

# Rear cross rail of seat
make_box_mesh(bm_seat, (0.0, 0.000, seat_z), (seat_w, 0.016, seat_th))

# 4 longitudinal slats
num_slats = 4
slat_w = 0.046
slat_len = 0.245
slat_y_c = (-0.250 + -0.005) / 2.0
slat_pitch = (seat_w - 2*seat_side_w - slat_w) / (num_slats - 1)
for i in range(num_slats):
    x_slat = -(seat_w - 2*seat_side_w - slat_w)/2 + i * slat_pitch
    make_box_mesh(bm_seat, (x_slat, slat_y_c, seat_z + 0.002), (slat_w, slat_len, 0.006))

bmesh.ops.remove_doubles(bm_seat, verts=bm_seat.verts, dist=0.0005)
me_seat = bpy.data.meshes.new('seat')
bm_seat.to_mesh(me_seat)
bm_seat.free()
seat = bpy.data.objects.new('seat', me_seat)
bpy.context.scene.collection.objects.link(seat)
assign_material(seat, mat_wood_dark)

# Sanity check: names must match URDF exactly
for _n in ['main_frame', 'seat', 'front_leg_frame']:
    assert _n in bpy.data.objects, _n
