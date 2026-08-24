"""TreasureChest — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

CONTRACT: build ONE mesh object per URDF link, named EXACTLY like the link, placed
at its REST-POSE WORLD position (= URDF q=0, the pose the plan's bboxes describe).
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear default scene objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def get_or_create_material(name, diffuse_color, roughness=0.6, metallic=0.0):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        bsdf = nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs['Base Color'].default_value = diffuse_color
            bsdf.inputs['Roughness'].default_value = roughness
            bsdf.inputs['Metallic'].default_value = metallic
    return mat

mat_wood = get_or_create_material("DarkOak", (0.22, 0.12, 0.06, 1.0), roughness=0.75, metallic=0.0)
mat_iron = get_or_create_material("ForgedIron", (0.04, 0.04, 0.05, 1.0), roughness=0.35, metallic=0.85)
mat_tray = get_or_create_material("PineWood", (0.75, 0.55, 0.30, 1.0), roughness=0.55, metallic=0.0)
mat_gold = get_or_create_material("GoldHardware", (0.85, 0.65, 0.15, 1.0), roughness=0.3, metallic=0.9)


# =========================================================================
# 1. CHEST BODY
# BBox: center (0, 0, 0.15), size (0.60, 0.40, 0.30)
# Wall thickness = 0.03 m, floor thickness = 0.03 m
# Outer: X [-0.30, +0.30], Y [-0.20, +0.20], Z [0.00, 0.30]
# Inner cavity: X [-0.27, +0.27], Y [-0.17, +0.17], Z [0.03, 0.30]
# Ledges for tray: at Z = 0.22, Y from -0.17 to -0.155 and +0.155 to +0.17
# Staple loop on front face: center (0, -0.204, 0.26)
# =========================================================================
def build_chest_body():
    bm = bmesh.new()

    def add_box(center, size, mat_idx=0):
        start_idx = len(bm.faces)
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0)))
        bm.faces.ensure_lookup_table()
        for idx in range(start_idx, len(bm.faces)):
            bm.faces[idx].material_index = mat_idx

    # Wood body (material index 0)
    # Floor:
    add_box((0, 0, 0.015), (0.60, 0.40, 0.03), mat_idx=0)
    # Front wall:
    add_box((0, -0.185, 0.165), (0.60, 0.03, 0.27), mat_idx=0)
    # Back wall (top at z=0.290, y: [0.170, 0.194]):
    add_box((0, 0.182, 0.160), (0.60, 0.024, 0.260), mat_idx=0)
    # Left wall:
    add_box((-0.285, 0, 0.165), (0.03, 0.34, 0.27), mat_idx=0)
    # Right wall:
    add_box((0.285, 0, 0.165), (0.03, 0.34, 0.27), mat_idx=0)

    # Tray support rails inside cavity (front & back ledges):
    # Ledge top at z = 0.220, y spans [-0.170, -0.156] and [0.156, 0.170]
    add_box((0, -0.163, 0.215), (0.54, 0.014, 0.010), mat_idx=0)
    add_box((0, 0.163, 0.215), (0.54, 0.014, 0.010), mat_idx=0)

    # Iron hardware (material index 1)
    # Bottom rim horizontal band:
    add_box((0, -0.200, 0.025), (0.602, 0.005, 0.040), mat_idx=1)
    add_box((0, 0.200, 0.025), (0.602, 0.005, 0.040), mat_idx=1)
    add_box((-0.300, 0, 0.025), (0.005, 0.402, 0.040), mat_idx=1)
    add_box((0.300, 0, 0.025), (0.005, 0.402, 0.040), mat_idx=1)

    # Top rim horizontal band:
    add_box((0, -0.200, 0.285), (0.602, 0.005, 0.028), mat_idx=1)
    add_box((0, 0.194, 0.280), (0.602, 0.005, 0.020), mat_idx=1)
    add_box((-0.300, 0, 0.285), (0.005, 0.402, 0.028), mat_idx=1)
    add_box((0.300, 0, 0.285), (0.005, 0.402, 0.028), mat_idx=1)

    # Corner brackets (4 vertical corner irons):
    for cx in [-0.298, 0.298]:
        for cy in [-0.198, 0.198]:
            add_box((cx, cy, 0.15), (0.030, 0.030, 0.30), mat_idx=1)

    # Intermediate vertical iron bands: at x = -0.18, x = 0.0, x = +0.18
    for bx in [-0.18, 0.0, 0.18]:
        # front band
        add_box((bx, -0.200, 0.15), (0.035, 0.006, 0.30), mat_idx=1)
        # back band
        add_box((bx, 0.200, 0.15), (0.035, 0.006, 0.30), mat_idx=1)

    # Side drop-ring carrying handles (left and right):
    for side, sx in [(-1, -0.300), (1, 0.300)]:
        # combined plate + ring bracket into 1 piece
        add_box((sx + side*0.004, 0, 0.165), (0.016, 0.07, 0.09), mat_idx=1)

    # Staple loop on front face for hasp: center (0, -0.202, 0.26)
    # Single merged solid box protruding from body to form staple block
    add_box((0, -0.201, 0.26), (0.040, 0.010, 0.040), mat_idx=1)

    # Rear hinge bottom leaves at y=0.198, z=0.285 (staying inside body y <= 0.200):
    for hx in [-0.18, 0.18]:
        add_box((hx, 0.194, 0.280), (0.040, 0.014, 0.040), mat_idx=1)

    # Merge nearby vertices if any
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new("chest_body_mesh")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("chest_body", me)
    ob.data.materials.append(mat_wood)
    ob.data.materials.append(mat_iron)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# =========================================================================
# 2. DOMED LID
# BBox: center (0, 0, 0.375), extents (0.61, 0.41, 0.15)
# Planned: X: [-0.305, +0.305], Y: [-0.205, +0.205], Z: [0.300, 0.450]
# Hinge pivot: (0, 0.200, 0.300)
# Outer arch: radius R = 0.205, height = 0.150 -> z = 0.300 + 0.150 * sin(u), y = -0.205 * cos(u)
# Smooth barrel vault mesh with wood material, iron straps embedded into same continuous mesh
# =========================================================================
def build_domed_lid():
    bm = bmesh.new()

    n_segs = 32
    nx_segs = 12
    x_min, x_max = -0.305, 0.305

    # Coordinates along X
    x_coords = [x_min + (x_max - x_min) * (i / nx_segs) for i in range(nx_segs + 1)]

    # We want wood body, and iron straps at x=[-0.1975, -0.1625], [-0.0175, 0.0175], [0.1625, 0.1975], and ends
    # Let's build outer and inner quad mesh
    outer_verts = []
    inner_verts = []

    for i, x in enumerate(x_coords):
        row_out = []
        row_in = []
        for j in range(n_segs + 1):
            theta = math.pi * (j / n_segs) # 0 to pi -> y: -0.205 to +0.205
            yo = -0.205 * math.cos(theta)
            # chamfer at rear edge (theta near pi) so it clears rear wall when fully open at 1.85 rad
            if j == n_segs:
                yo = 0.194
            elif j == n_segs - 1:
                yo = min(yo, 0.196)
            zo = 0.300 + 0.150 * math.sin(theta)
            yi = -0.185 * math.cos(theta)
            if j == n_segs:
                yi = 0.182
            elif j == n_segs - 1:
                yi = min(yi, 0.184)
            zi = 0.300 + 0.130 * math.sin(theta)

            vo = bm.verts.new((x, yo, zo))
            vi = bm.verts.new((x, yi, zi))
            row_out.append(vo)
            row_in.append(vi)
        outer_verts.append(row_out)
        inner_verts.append(row_in)

    bm.verts.ensure_lookup_table()

    def is_iron_band_x(x_mid):
        # 3 main bands and 2 end bands
        for bx in [-0.18, 0.0, 0.18]:
            if abs(x_mid - bx) < 0.03:
                return True
        if abs(x_mid - x_min) < 0.025 or abs(x_mid - x_max) < 0.025:
            return True
        return False

    # Faces for outer surface
    for i in range(nx_segs):
        x_mid = (x_coords[i] + x_coords[i+1]) / 2.0
        mat_idx = 1 if is_iron_band_x(x_mid) else 0
        for j in range(n_segs):
            f = bm.faces.new([
                outer_verts[i][j],
                outer_verts[i+1][j],
                outer_verts[i+1][j+1],
                outer_verts[i][j+1]
            ])
            f.material_index = mat_idx

    # Faces for inner surface (wood)
    for i in range(nx_segs):
        for j in range(n_segs):
            f = bm.faces.new([
                inner_verts[i][j+1],
                inner_verts[i+1][j+1],
                inner_verts[i+1][j],
                inner_verts[i][j]
            ])
            f.material_index = 0

    # Bottom front rim faces (theta = 0)
    for i in range(nx_segs):
        f = bm.faces.new([
            outer_verts[i][0],
            inner_verts[i][0],
            inner_verts[i+1][0],
            outer_verts[i+1][0]
        ])
        f.material_index = 0

    # Bottom rear rim faces (theta = pi)
    for i in range(nx_segs):
        f = bm.faces.new([
            outer_verts[i+1][n_segs],
            inner_verts[i+1][n_segs],
            inner_verts[i][n_segs],
            outer_verts[i][n_segs]
        ])
        f.material_index = 0

    # Left and Right end cap solid semicircular walls (theta from 0 to pi)
    # 1. Outer curved rim faces
    for j in range(n_segs):
        f1 = bm.faces.new([
            outer_verts[0][j+1],
            inner_verts[0][j+1],
            inner_verts[0][j],
            outer_verts[0][j]
        ])
        f1.material_index = 1
        f2 = bm.faces.new([
            outer_verts[nx_segs][j],
            inner_verts[nx_segs][j],
            inner_verts[nx_segs][j+1],
            outer_verts[nx_segs][j+1]
        ])
        f2.material_index = 1

    # 2. Solid wood end panels closing the dome ends
    v_center_left = bm.verts.new((x_min, 0.0, 0.300))
    v_center_right = bm.verts.new((x_max, 0.0, 0.300))
    for j in range(n_segs):
        # Left end cap solid fan (normal pointing -X)
        f_left = bm.faces.new([
            v_center_left,
            inner_verts[0][j],
            inner_verts[0][j+1]
        ])
        f_left.material_index = 0
        # Right end cap solid fan (normal pointing +X)
        f_right = bm.faces.new([
            v_center_right,
            inner_verts[nx_segs][j+1],
            inner_verts[nx_segs][j]
        ])
        f_right.material_index = 0

    # Add hardware on domed lid:
    # 1. Hasp upper mounting plate / hinge knuckle on the front of lid:
    # Hinge knuckle at (0, -0.203, 0.316), size: X=0.048, Y=0.003, Z=0.016
    start_idx = len(bm.faces)
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((0, -0.202, 0.318)) @ Matrix.Diagonal((0.044, 0.002, 0.012, 1.0)))
    bm.faces.ensure_lookup_table()
    for idx in range(start_idx, len(bm.faces)):
        bm.faces[idx].material_index = 1

    # 2. Rear hinge upper leaves on lid:
    for hx in [-0.18, 0.18]:
        start_idx = len(bm.faces)
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((hx, 0.198, 0.308)) @ Matrix.Diagonal((0.035, 0.003, 0.016, 1.0)))
        bm.faces.ensure_lookup_table()
        for idx in range(start_idx, len(bm.faces)):
            bm.faces[idx].material_index = 1

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new("domed_lid_mesh")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("domed_lid", me)
    ob.data.materials.append(mat_wood)
    ob.data.materials.append(mat_iron)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# =========================================================================
# 3. HASP
# BBox: center (0.000, -0.207, 0.275), extents (0.060, 0.015, 0.090)
# Joint hasp_hinge pivot: (0.000, -0.205, 0.315)
# Hasp plate: hangs down from z=0.315 to z=0.230
# Y at rest: centered at y = -0.207, thickness 0.004 (from -0.205 to -0.209)
# Width X = 0.058 (from -0.029 to +0.029)
# Cutout slot: center (0, -0.207, 0.260), width 0.024, height 0.024
# At rest, staple loop (0, -0.205, 0.26) passes through this cutout slot cleanly!
# Hasp is entirely iron (material 0 on this object).
# =========================================================================
def build_hasp():
    bm = bmesh.new()

    def add_box(center, size):
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0)))

    # Top hinge barrel around pivot (0, -0.205, 0.315):
    # Length X 0.042, diameter 0.004 (Y from -0.207 to -0.203, Z from 0.313 to 0.317)
    add_box((0, -0.205, 0.315), (0.042, 0.003, 0.004))

    # Upper solid plate (Z from 0.276 to 0.312, height 0.036):
    add_box((0, -0.207, 0.294), (0.058, 0.003, 0.036))

    # Left frame around slot (X from -0.029 to -0.014, Z from 0.244 to 0.276):
    add_box((-0.0215, -0.207, 0.260), (0.015, 0.003, 0.032))

    # Right frame around slot (X from 0.014 to 0.029, Z from 0.244 to 0.276):
    add_box((0.0215, -0.207, 0.260), (0.015, 0.003, 0.032))

    # Lower solid plate (Z from 0.230 to 0.244, height 0.014):
    add_box((0, -0.207, 0.237), (0.058, 0.003, 0.014))

    # Decorative bottom point tip (Z from 0.222 to 0.230):
    add_box((0, -0.207, 0.226), (0.030, 0.003, 0.008))

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new("hasp_mesh")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("hasp", me)
    ob.data.materials.append(mat_iron)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# =========================================================================
# 4. INNER TRAY
# BBox: center (-0.140, 0.000, 0.255), extents (0.240, 0.330, 0.070)
# Rest bounds:
# X: [-0.260, -0.020]
# Y: [-0.165, 0.165]
# Z: [0.220, 0.290]
# Sits on ledges (ledges at Z=0.220, Y=±0.163) with 0.5 mm clearance/touch.
# Tray bottom floor at Z = 0.2205 to 0.2285
# Left/right travel is 0.28m, cavity is X [-0.27, +0.27] -> tray at upper limit reaches X = +0.02 to +0.26 (clears walls).
# =========================================================================
def build_inner_tray():
    bm = bmesh.new()

    def add_box(center, size, mat_idx=0):
        start_idx = len(bm.faces)
        bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0)))
        bm.faces.ensure_lookup_table()
        for idx in range(start_idx, len(bm.faces)):
            bm.faces[idx].material_index = mat_idx

    # Bottom floor: Z [0.221, 0.230], X [-0.258, -0.022] (width 0.236), Y [-0.162, 0.162] (depth 0.324)
    add_box((-0.140, 0.0, 0.2255), (0.236, 0.324, 0.009), mat_idx=0)

    # Front wall: Y [-0.162, -0.154], Z [0.230, 0.288]
    add_box((-0.140, -0.158, 0.259), (0.236, 0.008, 0.058), mat_idx=0)

    # Back wall: Y [0.154, 0.162], Z [0.230, 0.288]
    add_box((-0.140, 0.158, 0.259), (0.236, 0.008, 0.058), mat_idx=0)

    # Left wall: X [-0.258, -0.250], Z [0.230, 0.288]
    add_box((-0.254, 0.0, 0.259), (0.008, 0.308, 0.058), mat_idx=0)

    # Right wall: X [-0.030, -0.022], Z [0.230, 0.288]
    add_box((-0.026, 0.0, 0.259), (0.008, 0.308, 0.058), mat_idx=0)

    # Internal dividers:
    # Divider along X:
    add_box((-0.140, 0.0, 0.256), (0.220, 0.006, 0.052), mat_idx=0)
    # Divider along Y:
    add_box((-0.140, 0.077, 0.256), (0.006, 0.148, 0.052), mat_idx=0)

    # Brass finger grips / handles on top of front and back walls:
    add_box((-0.140, -0.158, 0.289), (0.045, 0.010, 0.004), mat_idx=1)
    add_box((-0.140, 0.158, 0.289), (0.045, 0.010, 0.004), mat_idx=1)

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new("inner_tray_mesh")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("inner_tray", me)
    ob.data.materials.append(mat_tray)
    ob.data.materials.append(mat_gold)
    bpy.context.scene.collection.objects.link(ob)
    return ob


# Build all 4 links
chest_body = build_chest_body()
domed_lid = build_domed_lid()
hasp = build_hasp()
inner_tray = build_inner_tray()

# Sanity check
for _n in ['chest_body', 'domed_lid', 'hasp', 'inner_tray']:
    assert _n in bpy.data.objects, _n
