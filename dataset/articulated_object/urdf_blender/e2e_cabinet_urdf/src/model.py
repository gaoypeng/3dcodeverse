import bpy
import bmesh
import math
from mathutils import Vector

# Clear existing objects in the scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def create_material(name, color, roughness=0.4):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs['Base Color'].default_value = color
            bsdf.inputs['Roughness'].default_value = roughness
    return mat

mat_oak = create_material("OakWood", (0.76, 0.60, 0.42, 1.0), roughness=0.45)
mat_birch = create_material("BirchPlywood", (0.85, 0.75, 0.58, 1.0), roughness=0.5)
mat_knob = create_material("TurnedOak", (0.65, 0.48, 0.32, 1.0), roughness=0.35)

def make_box_mesh(bm, center, size, mat_index=0):
    cx, cy, cz = center
    sx, sy, sz = size
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    
    verts = [
        bm.verts.new((cx - hx, cy - hy, cz - hz)),
        bm.verts.new((cx + hx, cy - hy, cz - hz)),
        bm.verts.new((cx + hx, cy + hy, cz - hz)),
        bm.verts.new((cx - hx, cy + hy, cz - hz)),
        bm.verts.new((cx - hx, cy - hy, cz + hz)),
        bm.verts.new((cx + hx, cy - hy, cz + hz)),
        bm.verts.new((cx + hx, cy + hy, cz + hz)),
        bm.verts.new((cx - hx, cy + hy, cz + hz)),
    ]
    
    faces_idx = [
        (0, 1, 2, 3), # bottom (-Z)
        (4, 7, 6, 5), # top (+Z)
        (0, 4, 5, 1), # front (-Y)
        (2, 6, 7, 3), # back (+Y)
        (0, 3, 7, 4), # left (-X)
        (1, 5, 6, 2), # right (+X)
    ]
    for f_idx in faces_idx:
        f = bm.faces.new([verts[i] for i in f_idx])
        f.material_index = mat_index

def make_tapered_cylinder(bm, center_bottom, r_bot, r_top, height, segments=16, mat_index=0):
    cbx, cby, cbz = center_bottom
    bot_verts = []
    top_verts = []
    for i in range(segments):
        theta = 2.0 * math.pi * i / segments
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        bot_verts.append(bm.verts.new((cbx + r_bot * cos_t, cby + r_bot * sin_t, cbz)))
        top_verts.append(bm.verts.new((cbx + r_top * cos_t, cby + r_top * sin_t, cbz + height)))
    
    for i in range(segments):
        i_next = (i + 1) % segments
        f = bm.faces.new([bot_verts[i], bot_verts[i_next], top_verts[i_next], top_verts[i]])
        f.material_index = mat_index
    
    f_bot = bm.faces.new(list(reversed(bot_verts)))
    f_bot.material_index = mat_index
    f_top = bm.faces.new(top_verts)
    f_top.material_index = mat_index

# ==========================================
# 1. Carcass
# ==========================================
# Planned bbox: centre (0.000, 0.000, 0.300), extents (0.460, 0.420, 0.600)
# Overall: X [-0.230, 0.230], Y [-0.210, 0.210], Z [0.000, 0.600]

bm_carcass = bmesh.new()

# Tabletop: 0.460 x 0.420 x 0.020, center (0, 0, 0.590) -> Y in [-0.210, 0.210], X in [-0.230, 0.230], Z in [0.580, 0.600]
make_box_mesh(bm_carcass, center=(0.0, 0.0, 0.590), size=(0.460, 0.420, 0.020), mat_index=0)

# Side panels (left & right): X = [-0.230, -0.200] and [0.200, 0.230] (thickness 0.030)
# Y in [-0.180, 0.210], Z in [0.080, 0.580]
make_box_mesh(bm_carcass, center=(-0.215, 0.015, 0.330), size=(0.030, 0.390, 0.500), mat_index=0)
make_box_mesh(bm_carcass, center=(0.215, 0.015, 0.330), size=(0.030, 0.390, 0.500), mat_index=0)

# Back panel: X in [-0.200, 0.200], Y in [0.200, 0.210], Z in [0.080, 0.580]
make_box_mesh(bm_carcass, center=(0.0, 0.205, 0.330), size=(0.400, 0.010, 0.500), mat_index=0)

# Bottom base panel: X in [-0.200, 0.200], Y in [-0.180, 0.200], Z in [0.080, 0.092]
make_box_mesh(bm_carcass, center=(0.0, 0.010, 0.086), size=(0.400, 0.380, 0.012), mat_index=0)

# Shelf divider: X in [-0.200, 0.200], Y in [-0.180, 0.200], Z in [0.430, 0.443]
make_box_mesh(bm_carcass, center=(0.0, 0.010, 0.4365), size=(0.400, 0.380, 0.013), mat_index=0)

# 4 Legs: from Z=0 to Z=0.082 (embed 2mm into bottom panel)
leg_positions = [
    (-0.180, -0.140),
    (0.180, -0.140),
    (-0.180, 0.160),
    (0.180, 0.160),
]
for lx, ly in leg_positions:
    make_tapered_cylinder(bm_carcass, center_bottom=(lx, ly, 0.0), r_bot=0.011, r_top=0.016, height=0.082, segments=16, mat_index=0)

me_carcass = bpy.data.meshes.new("Carcass")
bm_carcass.to_mesh(me_carcass)
bm_carcass.free()
obj_carcass = bpy.data.objects.new("Carcass", me_carcass)
obj_carcass.data.materials.append(mat_oak)
bpy.context.scene.collection.objects.link(obj_carcass)

# ==========================================
# 2. Drawer
# ==========================================
# Planned bbox: centre (0.000, -0.015, 0.505), extents (0.396, 0.340, 0.120)
# Front face panel: X [-0.198, 0.198], Y [-0.190, -0.172], Z [0.445, 0.565]
bm_drawer = bmesh.new()

# Front face panel
make_box_mesh(bm_drawer, center=(0.0, -0.181, 0.505), size=(0.396, 0.018, 0.120), mat_index=0)

# Drawer box sides (birch plywood):
# Width 0.360 -> X [-0.180, 0.180], Depth 0.320 -> Y [-0.170, 0.150]
# Bottom of box: Z [0.445, 0.455]
make_box_mesh(bm_drawer, center=(0.0, -0.010, 0.450), size=(0.360, 0.320, 0.010), mat_index=1)
# Left side wall
make_box_mesh(bm_drawer, center=(-0.175, -0.010, 0.505), size=(0.010, 0.320, 0.100), mat_index=1)
# Right side wall
make_box_mesh(bm_drawer, center=(0.175, -0.010, 0.505), size=(0.010, 0.320, 0.100), mat_index=1)
# Back wall
make_box_mesh(bm_drawer, center=(0.0, 0.145, 0.505), size=(0.340, 0.010, 0.100), mat_index=1)

me_drawer = bpy.data.meshes.new("Drawer")
bm_drawer.to_mesh(me_drawer)
bm_drawer.free()
obj_drawer = bpy.data.objects.new("Drawer", me_drawer)
obj_drawer.data.materials.append(mat_oak)
obj_drawer.data.materials.append(mat_birch)
bpy.context.scene.collection.objects.link(obj_drawer)

# ==========================================
# 3. DrawerKnob & 5. DoorKnob
# ==========================================
# Planned bbox: centre (0.000, -0.209, 0.505), extents (0.028, 0.024, 0.028)
def make_turned_knob(center, base_y=-0.190):
    bm = bmesh.new()
    cx, cy, cz = center
    
    # Knob head: UV sphere
    # Planned sphere bounds: X [cx - 0.014, cx + 0.014], Z [cz - 0.014, cz + 0.014]
    # Y bounds: [-0.221, -0.197]
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=12, radius=0.014)
    for v in bm.verts:
        v.co.y *= 0.6
        v.co += Vector((cx, cy - 0.002, cz))
    
    # Knob neck/stem: solid cylinder extending into the panel (base_y + 0.002) for 2mm overlap
    neck_y0 = cy
    neck_y1 = base_y + 0.002
    neck_len = neck_y1 - neck_y0
    neck_mid_y = (neck_y0 + neck_y1) / 2.0
    
    neck_res = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=12,
        radius1=0.006,
        radius2=0.006,
        depth=neck_len
    )
    # Rotate cylinder from Z axis to Y axis
    for v in neck_res['verts']:
        z_orig = v.co.z
        y_orig = v.co.y
        v.co.x += cx
        v.co.y = neck_mid_y + z_orig
        v.co.z = cz + y_orig
    
    # Merge double vertices so knob is a single solid mesh island
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    
    return bm

bm_dk = make_turned_knob(center=(0.0, -0.209, 0.505), base_y=-0.190)
me_dk = bpy.data.meshes.new("DrawerKnob")
bm_dk.to_mesh(me_dk)
bm_dk.free()
obj_dk = bpy.data.objects.new("DrawerKnob", me_dk)
obj_dk.data.materials.append(mat_knob)
bpy.context.scene.collection.objects.link(obj_dk)

# ==========================================
# 4. Door
# ==========================================
# Planned bbox: centre (0.000, -0.190, 0.260), extents (0.396, 0.018, 0.336)
bm_door = bmesh.new()
make_box_mesh(bm_door, center=(0.0, -0.190, 0.260), size=(0.396, 0.018, 0.336), mat_index=0)

me_door = bpy.data.meshes.new("Door")
bm_door.to_mesh(me_door)
bm_door.free()
obj_door = bpy.data.objects.new("Door", me_door)
obj_door.data.materials.append(mat_oak)
bpy.context.scene.collection.objects.link(obj_door)

# ==========================================
# 5. DoorKnob
# ==========================================
# Planned bbox: centre (0.160, -0.209, 0.260), extents (0.028, 0.024, 0.028)
bm_doorknob = make_turned_knob(center=(0.160, -0.209, 0.260), base_y=-0.190)
me_doorknob = bpy.data.meshes.new("DoorKnob")
bm_doorknob.to_mesh(me_doorknob)
bm_doorknob.free()
obj_doorknob = bpy.data.objects.new("DoorKnob", me_doorknob)
obj_doorknob.data.materials.append(mat_knob)
bpy.context.scene.collection.objects.link(obj_doorknob)
