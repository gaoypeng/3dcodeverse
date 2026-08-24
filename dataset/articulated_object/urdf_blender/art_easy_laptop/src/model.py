"""Laptop — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

CONTRACT: build ONE mesh object per URDF link, named EXACTLY like the link, placed
at its REST-POSE WORLD position (= URDF q=0, the pose the plan's bboxes describe).
Z is UP, -Y is FRONT, +X is RIGHT.
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear all existing mesh objects and materials
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for m in bpy.data.materials:
    bpy.data.materials.remove(m)
for me in bpy.data.meshes:
    bpy.data.meshes.remove(me)

# ---------------------------------------------------------
# Materials
# ---------------------------------------------------------
def create_mat(name, color, metallic=0.0, roughness=0.5, specular=0.5):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
        if "Specular" in bsdf.inputs:
            bsdf.inputs["Specular"].default_value = specular
        elif "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = specular
    return mat

mat_space_gray = create_mat("SpaceGray", (0.24, 0.25, 0.27, 1.0), metallic=0.88, roughness=0.30)
mat_dark_well = create_mat("DarkWell", (0.08, 0.08, 0.09, 1.0), metallic=0.70, roughness=0.60)
mat_black_key = create_mat("BlackKeycap", (0.015, 0.015, 0.015, 1.0), metallic=0.05, roughness=0.35)
mat_trackpad = create_mat("Trackpad", (0.28, 0.29, 0.31, 1.0), metallic=0.80, roughness=0.20)
mat_rubber = create_mat("RubberFoot", (0.01, 0.01, 0.01, 1.0), metallic=0.0, roughness=0.95)
mat_bezel = create_mat("Bezel", (0.02, 0.02, 0.02, 1.0), metallic=0.10, roughness=0.50)
mat_screen_display = create_mat("ScreenDisplay", (0.05, 0.12, 0.22, 1.0), metallic=0.10, roughness=0.02, specular=0.98)
mat_webcam = create_mat("WebcamGlass", (0.01, 0.02, 0.04, 1.0), metallic=0.95, roughness=0.02)
mat_logo = create_mat("PolishedLogo", (0.90, 0.91, 0.93, 1.0), metallic=0.98, roughness=0.08)
mat_port_inner = create_mat("PortBlack", (0.01, 0.01, 0.01, 1.0), metallic=0.5, roughness=0.7)

def create_mesh_obj(name, bm, mat=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    if mat:
        obj.data.materials.append(mat)
    return obj

def make_cylinder(bm, radius, depth, segments=16):
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments, radius1=radius, radius2=radius, depth=depth)


# =========================================================
# BUILD BASE LINK
# Base bbox centre = (0.000, 0.000, 0.006), extents = (0.320, 0.220, 0.012)
# X: -0.160 .. +0.160
# Y: -0.110 .. +0.110
# Z: 0.000 .. 0.012
#
# Base main body:
# - Front body / main body (Y: -0.110 to +0.102):
#   Bottom slab: Z=0.0005 to 0.0090
#   Palm rest & side rims: Z=0.0090 to 0.0110
#   Keyboard well cavity floor: Z=0.0090
#   Keycaps: Z=0.0090 to 0.0108 (height 1.8mm)
#   Trackpad: Z=0.0102 to 0.0109 (0.1mm inset into palm rest)
# - Rear hinge cutout / trough (Y: +0.102 to +0.110):
#   Height Z=0.0005 to 0.0085 (leaving space for hinge barrel Z=0.0092..0.0148)
#   Corner blocks (X: -0.160..-0.130 and +0.130..+0.160) up to Z=0.0110
# =========================================================
base_parts = []

# 1. Base Main Body (Y: -0.110 to +0.102 -> depth 0.212, center Y = -0.004)
# Bottom slab: Z=0.0005 to 0.0090 (height 0.0085, center Z = 0.00475)
bm_b_slab = bmesh.new()
bmesh.ops.create_cube(bm_b_slab, size=1.0)
bmesh.ops.scale(bm_b_slab, vec=(0.320, 0.212, 0.0085), verts=bm_b_slab.verts)
bmesh.ops.translate(bm_b_slab, vec=(0.0, -0.004, 0.00475), verts=bm_b_slab.verts)
base_parts.append(create_mesh_obj("BaseBottomSlab", bm_b_slab, mat_space_gray))

# Palm rest & side margins around keyboard well (Z=0.0090 to 0.0110, height 0.0020, center Z = 0.0100):
# Well bounds: X: -0.136 .. +0.136, Y: +0.010 .. +0.098

# (a) Front palm rest area: Y from -0.110 to +0.010 (depth 0.120, center Y = -0.050)
bm_b_front = bmesh.new()
bmesh.ops.create_cube(bm_b_front, size=1.0)
bmesh.ops.scale(bm_b_front, vec=(0.320, 0.120, 0.0020), verts=bm_b_front.verts)
bmesh.ops.translate(bm_b_front, vec=(0.0, -0.050, 0.0100), verts=bm_b_front.verts)
base_parts.append(create_mesh_obj("BasePalmRest", bm_b_front, mat_space_gray))

# (b) Rear margin: Y from +0.098 to +0.102 (depth 0.004, center Y = +0.100)
bm_b_rear = bmesh.new()
bmesh.ops.create_cube(bm_b_rear, size=1.0)
bmesh.ops.scale(bm_b_rear, vec=(0.320, 0.004, 0.0020), verts=bm_b_rear.verts)
bmesh.ops.translate(bm_b_rear, vec=(0.0, 0.100, 0.0100), verts=bm_b_rear.verts)
base_parts.append(create_mesh_obj("BaseRearMargin", bm_b_rear, mat_space_gray))

# (c) Left margin: X from -0.160 to -0.136 (width 0.024, center X = -0.148), Y from +0.010 to +0.098 (depth 0.088, center Y = 0.054)
bm_b_left = bmesh.new()
bmesh.ops.create_cube(bm_b_left, size=1.0)
bmesh.ops.scale(bm_b_left, vec=(0.024, 0.088, 0.0020), verts=bm_b_left.verts)
bmesh.ops.translate(bm_b_left, vec=(-0.148, 0.054, 0.0100), verts=bm_b_left.verts)
base_parts.append(create_mesh_obj("BaseLeftMargin", bm_b_left, mat_space_gray))

# (d) Right margin: X from +0.136 to +0.160 (width 0.024, center X = +0.148), Y from +0.010 to +0.098 (depth 0.088, center Y = 0.054)
bm_b_right = bmesh.new()
bmesh.ops.create_cube(bm_b_right, size=1.0)
bmesh.ops.scale(bm_b_right, vec=(0.024, 0.088, 0.0020), verts=bm_b_right.verts)
bmesh.ops.translate(bm_b_right, vec=(0.148, 0.054, 0.0100), verts=bm_b_right.verts)
base_parts.append(create_mesh_obj("BaseRightMargin", bm_b_right, mat_space_gray))

# 2. Rear hinge area (Y: +0.102 to +0.110 -> depth 0.008, center Y = +0.106)
# Central trough floor: Z=0.0005 to 0.0080 (height 0.0075, center Z = 0.00425)
bm_b_trough = bmesh.new()
bmesh.ops.create_cube(bm_b_trough, size=1.0)
bmesh.ops.scale(bm_b_trough, vec=(0.320, 0.008, 0.0075), verts=bm_b_trough.verts)
bmesh.ops.translate(bm_b_trough, vec=(0.0, 0.106, 0.00425), verts=bm_b_trough.verts)
base_parts.append(create_mesh_obj("BaseRearTrough", bm_b_trough, mat_space_gray))

# Rear corner side blocks (left/right of hinge barrel, up to Z=0.0110):
for sx in [-0.145, 0.145]:
    bm_cb = bmesh.new()
    bmesh.ops.create_cube(bm_cb, size=1.0)
    bmesh.ops.scale(bm_cb, vec=(0.030, 0.008, 0.0030), verts=bm_cb.verts)
    bmesh.ops.translate(bm_cb, vec=(sx, 0.106, 0.0095), verts=bm_cb.verts)
    base_parts.append(create_mesh_obj(f"BaseCornerBlock_{sx}", bm_cb, mat_space_gray))

# 3. Keyboard well floor plate (dark anodized lining, Z=0.0089 to 0.0091)
bm_kw = bmesh.new()
bmesh.ops.create_cube(bm_kw, size=1.0)
bmesh.ops.scale(bm_kw, vec=(0.272, 0.088, 0.0003), verts=bm_kw.verts)
bmesh.ops.translate(bm_kw, vec=(0.0, 0.054, 0.00905), verts=bm_kw.verts)
base_parts.append(create_mesh_obj("KeyboardWellFloor", bm_kw, mat_dark_well))

# 4. Rubber Feet at bottom (sitting on ground Z=0.0000 to Z=0.0016)
foot_r = 0.007
foot_h = 0.0016
for fx in [-0.135, 0.135]:
    for fy in [-0.085, 0.085]:
        bm_foot = bmesh.new()
        bmesh.ops.create_cone(bm_foot, cap_ends=True, cap_tris=False, segments=16, radius1=foot_r, radius2=foot_r*0.9, depth=foot_h)
        bmesh.ops.translate(bm_foot, vec=(fx, fy, foot_h / 2.0), verts=bm_foot.verts)
        base_parts.append(create_mesh_obj(f"Foot_{fx}_{fy}", bm_foot, mat_rubber))

# 5. Trackpad: inset rectangular touch pad on palm rest (X: 0.120, Y: 0.075)
# Inset slightly below palm rest Z=0.0110 -> trackpad top at Z=0.0108 (Z=0.0100 to 0.0108)
bm_tp = bmesh.new()
bmesh.ops.create_cube(bm_tp, size=1.0)
bmesh.ops.scale(bm_tp, vec=(0.120, 0.075, 0.0008), verts=bm_tp.verts)
bmesh.ops.translate(bm_tp, vec=(0.0, -0.055, 0.0104), verts=bm_tp.verts)
base_parts.append(create_mesh_obj("TrackpadPlate", bm_tp, mat_trackpad))

# Trackpad border / perimeter gap rim
bm_tp_border = bmesh.new()
bmesh.ops.create_cube(bm_tp_border, size=1.0)
bmesh.ops.scale(bm_tp_border, vec=(0.122, 0.077, 0.0008), verts=bm_tp_border.verts)
bmesh.ops.translate(bm_tp_border, vec=(0.0, -0.055, 0.01045), verts=bm_tp_border.verts)
base_parts.append(create_mesh_obj("TrackpadBorder", bm_tp_border, mat_dark_well))

# 6. 3D Keycaps in recessed keyboard well (Z=0.0090 to 0.0108, height 0.0018)
bm_keys = bmesh.new()
row_y = [0.091, 0.078, 0.065, 0.052, 0.039, 0.025]
key_h = 0.0018
key_z = 0.0090 + key_h / 2.0  # 0.0099

kw_std = 0.0145
kd_std = 0.0105
gap_x = 0.0030

# Row 0: Function row (14 keys)
fn_w = 0.0155
fn_d = 0.0065
fn_y = row_y[0]
n_fn = 14
start_x = -0.122
step_x = (0.244) / (n_fn - 1)
for i in range(n_fn):
    kx = start_x + i * step_x
    bmesh.ops.create_cube(bm_keys, size=1.0)
    new_verts = bm_keys.verts[-8:]
    bmesh.ops.scale(bm_keys, vec=(fn_w, fn_d, key_h), verts=new_verts)
    bmesh.ops.translate(bm_keys, vec=(kx, fn_y, key_z), verts=new_verts)

# Row 1: Number row (14 keys)
r1_y = row_y[1]
for i in range(14):
    if i == 13: # Backspace
        w = 0.024
        kx = 0.116
    else:
        w = kw_std
        kx = -0.122 + i * (kw_std + gap_x)
    bmesh.ops.create_cube(bm_keys, size=1.0)
    new_verts = bm_keys.verts[-8:]
    bmesh.ops.scale(bm_keys, vec=(w, kd_std, key_h), verts=new_verts)
    bmesh.ops.translate(bm_keys, vec=(kx, r1_y, key_z), verts=new_verts)

# Row 2: Tab, 12 keys, Backslash
r2_y = row_y[2]
bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.022, kd_std, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(-0.118, r2_y, key_z), verts=new_verts)
for i in range(12):
    kx = -0.096 + i * (kw_std + gap_x)
    bmesh.ops.create_cube(bm_keys, size=1.0)
    new_verts = bm_keys.verts[-8:]
    bmesh.ops.scale(bm_keys, vec=(kw_std, kd_std, key_h), verts=new_verts)
    bmesh.ops.translate(bm_keys, vec=(kx, r2_y, key_z), verts=new_verts)
bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.020, kd_std, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(0.118, r2_y, key_z), verts=new_verts)

# Row 3: Caps Lock, 11 keys, Enter
r3_y = row_y[3]
bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.025, kd_std, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(-0.1165, r3_y, key_z), verts=new_verts)
for i in range(11):
    kx = -0.091 + i * (kw_std + gap_x)
    bmesh.ops.create_cube(bm_keys, size=1.0)
    new_verts = bm_keys.verts[-8:]
    bmesh.ops.scale(bm_keys, vec=(kw_std, kd_std, key_h), verts=new_verts)
    bmesh.ops.translate(bm_keys, vec=(kx, r3_y, key_z), verts=new_verts)
bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.027, kd_std, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(0.1145, r3_y, key_z), verts=new_verts)

# Row 4: L-Shift, 10 keys, R-Shift
r4_y = row_y[4]
bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.030, kd_std, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(-0.114, r4_y, key_z), verts=new_verts)
for i in range(10):
    kx = -0.086 + i * (kw_std + gap_x)
    bmesh.ops.create_cube(bm_keys, size=1.0)
    new_verts = bm_keys.verts[-8:]
    bmesh.ops.scale(bm_keys, vec=(kw_std, kd_std, key_h), verts=new_verts)
    bmesh.ops.translate(bm_keys, vec=(kx, r4_y, key_z), verts=new_verts)
bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.035, kd_std, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(0.1105, r4_y, key_z), verts=new_verts)

# Row 5: Mods, Spacebar, Arrows
r5_y = row_y[5]
bottom_mods = [
    (-0.120, 0.016),
    (-0.101, 0.016),
    (-0.082, 0.016),
    (-0.060, 0.022),
    (0.000, 0.086),
    (0.060, 0.022),
    (0.082, 0.016),
    (0.100, 0.014),
    (0.126, 0.014),
]
for (bx, bw) in bottom_mods:
    bmesh.ops.create_cube(bm_keys, size=1.0)
    new_verts = bm_keys.verts[-8:]
    bmesh.ops.scale(bm_keys, vec=(bw, kd_std, key_h), verts=new_verts)
    bmesh.ops.translate(bm_keys, vec=(bx, r5_y, key_z), verts=new_verts)

# Up/Down arrows
bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.013, 0.0045, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(0.113, r5_y + 0.003, key_z), verts=new_verts)

bmesh.ops.create_cube(bm_keys, size=1.0)
new_verts = bm_keys.verts[-8:]
bmesh.ops.scale(bm_keys, vec=(0.013, 0.0045, key_h), verts=new_verts)
bmesh.ops.translate(bm_keys, vec=(0.113, r5_y - 0.003, key_z), verts=new_verts)

base_parts.append(create_mesh_obj("Keycaps", bm_keys, mat_black_key))

# 7. Side Ports
for py in [0.04, 0.06]:
    bm_port = bmesh.new()
    bmesh.ops.create_cube(bm_port, size=1.0)
    bmesh.ops.scale(bm_port, vec=(0.0016, 0.008, 0.003), verts=bm_port.verts)
    bmesh.ops.translate(bm_port, vec=(-0.1592, py, 0.0065), verts=bm_port.verts)
    base_parts.append(create_mesh_obj(f"Port_L_{py}", bm_port, mat_port_inner))

bm_port_r = bmesh.new()
bmesh.ops.create_cube(bm_port_r, size=1.0)
bmesh.ops.scale(bm_port_r, vec=(0.0016, 0.008, 0.003), verts=bm_port_r.verts)
bmesh.ops.translate(bm_port_r, vec=(0.1592, 0.06, 0.0065), verts=bm_port_r.verts)
base_parts.append(create_mesh_obj("Port_R_USBC", bm_port_r, mat_port_inner))

bm_jack = bmesh.new()
make_cylinder(bm_jack, radius=0.0018, depth=0.0016, segments=12)
bmesh.ops.rotate(bm_jack, matrix=Matrix.Rotation(math.radians(90), 3, 'Y'), verts=bm_jack.verts)
bmesh.ops.translate(bm_jack, vec=(0.1592, 0.04, 0.0065), verts=bm_jack.verts)
base_parts.append(create_mesh_obj("Port_R_Audio", bm_jack, mat_port_inner))

# 8. Front display thumb notch
bm_notch = bmesh.new()
bmesh.ops.create_cube(bm_notch, size=1.0)
bmesh.ops.scale(bm_notch, vec=(0.040, 0.003, 0.0012), verts=bm_notch.verts)
bmesh.ops.translate(bm_notch, vec=(0.0, -0.109, 0.0105), verts=bm_notch.verts)
base_parts.append(create_mesh_obj("FrontNotch", bm_notch, mat_dark_well))

# Join all base parts into single base object
bpy.ops.object.select_all(action='DESELECT')
for p in base_parts:
    p.select_set(True)
bpy.context.view_layer.objects.active = base_parts[0]
bpy.ops.object.join()
base_obj = bpy.context.active_object
base_obj.name = "base"


# =========================================================
# BUILD LID LINK
# Lid bbox centre = (0.000, 0.000, 0.015), extents = (0.320, 0.220, 0.006)
# X: -0.160 .. +0.160
# Y: -0.110 .. +0.110
# Z: 0.012 .. 0.018
# Joint pivot = (0.000, 0.110, 0.012)
# =========================================================
lid_parts = []

# 1. Main Lid Shell (Aluminum top / back cover)
# Lid outer shell from Z=0.0128 to Z=0.0180 (thickness 0.0052)
LW, LD = 0.320, 0.220
bm_lid_shell = bmesh.new()
bmesh.ops.create_cube(bm_lid_shell, size=1.0)
bmesh.ops.scale(bm_lid_shell, vec=(LW, LD, 0.0052), verts=bm_lid_shell.verts)
bmesh.ops.translate(bm_lid_shell, vec=(0.0, 0.0, 0.0154), verts=bm_lid_shell.verts)
lid_parts.append(create_mesh_obj("LidMain", bm_lid_shell, mat_space_gray))

# 2. Polished Logo on Lid Outer Top Face (Z=0.0178 to 0.0180)
bm_logo = bmesh.new()
make_cylinder(bm_logo, radius=0.015, depth=0.0004, segments=24)
bmesh.ops.translate(bm_logo, vec=(0.0, 0.0, 0.0178), verts=bm_logo.verts)
lid_parts.append(create_mesh_obj("LidLogo", bm_logo, mat_logo))

# 3. Inner Bezel (Matte black border covering inner face, Z=0.0124 to 0.0130)
bm_bezel = bmesh.new()
bmesh.ops.create_cube(bm_bezel, size=1.0)
bmesh.ops.scale(bm_bezel, vec=(0.316, 0.216, 0.0006), verts=bm_bezel.verts)
bmesh.ops.translate(bm_bezel, vec=(0.0, 0.0, 0.0127), verts=bm_bezel.verts)
lid_parts.append(create_mesh_obj("InnerBezel", bm_bezel, mat_bezel))

# 4. Display Screen Glass Panel (Active glossy screen, Z=0.0121 to 0.0126)
bm_screen = bmesh.new()
bmesh.ops.create_cube(bm_screen, size=1.0)
bmesh.ops.scale(bm_screen, vec=(0.298, 0.188, 0.0006), verts=bm_screen.verts)
bmesh.ops.translate(bm_screen, vec=(0.0, -0.003, 0.0124), verts=bm_screen.verts)
lid_parts.append(create_mesh_obj("DisplayScreen", bm_screen, mat_screen_display))

# 5. Webcam dot and microphone holes on top bezel (Z=0.0124)
bm_cam = bmesh.new()
make_cylinder(bm_cam, radius=0.0018, depth=0.0006, segments=16)
bmesh.ops.translate(bm_cam, vec=(0.0, -0.102, 0.0124), verts=bm_cam.verts)
lid_parts.append(create_mesh_obj("Webcam", bm_cam, mat_webcam))

for mx in [-0.012, 0.012]:
    bm_mic = bmesh.new()
    make_cylinder(bm_mic, radius=0.0006, depth=0.0006, segments=10)
    bmesh.ops.translate(bm_mic, vec=(mx, -0.102, 0.0124), verts=bm_mic.verts)
    lid_parts.append(create_mesh_obj(f"Mic_{mx}", bm_mic, mat_port_inner))

# 6. Integrated Barrel Hinge (cylindrical barrel along back edge)
# Cylinder along X-axis at pivot Y=0.107, Z=0.0120, radius=0.0028, length=0.250
bm_hinge = bmesh.new()
make_cylinder(bm_hinge, radius=0.0028, depth=0.250, segments=20)
bmesh.ops.rotate(bm_hinge, matrix=Matrix.Rotation(math.radians(90), 3, 'Y'), verts=bm_hinge.verts)
bmesh.ops.translate(bm_hinge, vec=(0.0, 0.107, 0.0120), verts=bm_hinge.verts)
lid_parts.append(create_mesh_obj("HingeBarrel", bm_hinge, mat_dark_well))

# Join all lid parts into single lid object
bpy.ops.object.select_all(action='DESELECT')
for p in lid_parts:
    p.select_set(True)
bpy.context.view_layer.objects.active = lid_parts[0]
bpy.ops.object.join()
lid_obj = bpy.context.active_object
lid_obj.name = "lid"

# Sanity assertions
assert "base" in bpy.data.objects
assert "lid" in bpy.data.objects
