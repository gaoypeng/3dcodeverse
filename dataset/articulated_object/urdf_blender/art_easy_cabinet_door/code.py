"""KitchenWallCabinet — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

Detailed geometry:
- Carcass: open front box with top, bottom, left side, right side, back panel (recessed), and a middle shelf.
- Door: slab door panel with chamfered edges, slightly offset so European hinge rotation avoids collision.
- Handle: vertical D-shaped bar handle with two standoffs attached to door face.
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear any existing objects if needed
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

# ---------------------------------------------------------
# Materials
# ---------------------------------------------------------
def create_material(name, color, roughness=0.3, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat

mat_carcass = create_material("MatCarcass", (0.92, 0.92, 0.90, 1.0), roughness=0.4, metallic=0.0)
mat_door = create_material("MatDoor", (0.95, 0.95, 0.94, 1.0), roughness=0.25, metallic=0.0)
mat_handle = create_material("MatHandle", (0.75, 0.76, 0.78, 1.0), roughness=0.2, metallic=0.9)

# ---------------------------------------------------------
# 1. Carcass
# ---------------------------------------------------------
# Carcass bbox: centre (0.000, 0.010, 0.350), extents (0.500, 0.340, 0.700)
# X: [-0.25, +0.25]
# Y: [-0.16, +0.18]
# Z: [0.00, +0.70]
# Panel thickness = 0.018 m (18 mm), Back panel = 0.008 m (8 mm)
# Shelf at half height: Z ~ 0.350, thickness 0.018 m

bm_carcass = bmesh.new()

def add_box(bm, center, size):
    matrix = Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0))
    bmesh.ops.create_cube(bm, size=1.0, matrix=matrix)

# Left panel: X in [-0.250, -0.232], Y in [-0.160, 0.180], Z in [0.000, 0.700]
add_box(bm_carcass, (-0.250 + 0.009, 0.010, 0.350), (0.018, 0.340, 0.700))

# Right panel: X in [0.232, 0.250], Y in [-0.160, 0.180], Z in [0.000, 0.700]
add_box(bm_carcass, (0.250 - 0.009, 0.010, 0.350), (0.018, 0.340, 0.700))

# Bottom panel: X in [-0.232, 0.232], Y in [-0.160, 0.180], Z in [0.000, 0.018]
add_box(bm_carcass, (0.000, 0.010, 0.009), (0.464, 0.340, 0.018))

# Top panel: X in [-0.232, 0.232], Y in [-0.160, 0.180], Z in [0.682, 0.700]
add_box(bm_carcass, (0.000, 0.010, 0.700 - 0.009), (0.464, 0.340, 0.018))

# Back panel: recessed by 12 mm from back (or flush at back edge Y=0.180)
# Back panel: X in [-0.232, 0.232], Y in [0.162, 0.170], Z in [0.018, 0.682]
add_box(bm_carcass, (0.000, 0.166, 0.350), (0.464, 0.008, 0.664))

# Fixed middle shelf: X in [-0.232, 0.232], Y in [-0.155, 0.160], Z in [0.341, 0.359] (depth slightly recessed from front)
add_box(bm_carcass, (0.000, 0.0025, 0.350), (0.464, 0.315, 0.018))

me_carcass = bpy.data.meshes.new("carcass")
bm_carcass.to_mesh(me_carcass)
bm_carcass.free()
ob_carcass = bpy.data.objects.new("carcass", me_carcass)
ob_carcass.data.materials.append(mat_carcass)
bpy.context.scene.collection.objects.link(ob_carcass)


# ---------------------------------------------------------
# 2. Door
# ---------------------------------------------------------
# Door bbox: centre (0.000, -0.169, 0.350), extents (0.496, 0.018, 0.696)
# Front face at Y = -0.178, Back face at Y = -0.160
# X: [-0.248, +0.248], Z: [0.002, 0.698]
# Hinge pivot is at (-0.245, -0.160, 0.350).
# When rotating 110 deg (1.92 rad), the door's outer edge/back corner near the hinge
# needs clearance from the carcass flank (X = -0.250).
# In standard cabinetry, full overlay doors have a 2mm bevel or the hinge cup offset.
# We bevel the door edge or adjust width slightly so X in [-0.246, 0.248] or chamfer the back left corner.

bm_door = bmesh.new()

# Create door base slab:
# We can create it with 8 vertices and bevel or construct cleanly.
door_w = 0.496
door_t = 0.018
door_h = 0.696
door_cx = 0.000
door_cy = -0.169
door_cz = 0.350

matrix_door = Matrix.Translation((door_cx, door_cy, door_cz)) @ Matrix.Diagonal((door_w, door_t, door_h, 1.0))
bmesh.ops.create_cube(bm_door, size=1.0, matrix=matrix_door)

# Chamfer the vertical edges (especially the back left edge at X=-0.248, Y=-0.160)
bmesh.ops.bevel(
    bm_door,
    geom=bm_door.edges[:],
    offset=0.0025,
    segments=1,
    profile=0.5,
    affect='EDGES'
)

# For the back-left corner (near hinge pivot -0.245, -0.160), bevel further to ensure 0 collision when fully swung to 110 deg.
# At 110 deg rotation around (-0.245, -0.160), points with X < -0.245 swing backwards into Y > -0.160.
# The carcass flank is at X <= -0.232, Y >= -0.160.
# If door left edge is at X = -0.245 (or cut back 3mm at the back edge), it will never penetrate carcass.
for v in bm_door.verts:
    if v.co.x < -0.244 and v.co.y > -0.165:
        # Move back-left vertices slightly inwards / forward to clear the carcass corner
        v.co.x = max(v.co.x, -0.2445)
        v.co.y = min(v.co.y, -0.162)

me_door = bpy.data.meshes.new("door")
bm_door.to_mesh(me_door)
bm_door.free()
ob_door = bpy.data.objects.new("door", me_door)
ob_door.data.materials.append(mat_door)
bpy.context.scene.collection.objects.link(ob_door)


# ---------------------------------------------------------
# 3. Handle
# ---------------------------------------------------------
# Handle bbox: centre (0.200, -0.198, 0.350), extents (0.020, 0.040, 0.180)
# X: [0.190, 0.210], Y: [-0.218, -0.178], Z: [0.260, 0.440]
# Attached to door front face at Y = -0.178.
# Vertical bar handle (cylinder/round bar of diameter 12mm) + 2 standoffs (cylinders/cubes of diameter 10mm from Y=-0.178 to Y=-0.210).

bm_handle = bmesh.new()

# Main vertical bar:
# Z: [0.260, 0.440] -> height 0.180, center Z = 0.350
# Bar center at X = 0.200, Y = -0.212, radius = 0.006 (dia 12 mm)
# Let's create a cylinder for the vertical bar
bmesh.ops.create_cone(
    bm_handle,
    cap_ends=True,
    cap_tris=False,
    segments=16,
    radius1=0.006,
    radius2=0.006,
    depth=0.180,
    matrix=Matrix.Translation((0.200, -0.212, 0.350))
)

# Two standoffs: connecting from door face (Y = -0.178) to bar (Y = -0.212)
# Standoff length = 0.034, center Y = -0.195
# Standoff positions: Z = 0.290 and Z = 0.410
for sz in (0.290, 0.410):
    # Cylinder rotated along Y axis
    rot_y = Matrix.Rotation(math.radians(90.0), 4, 'X')
    mat_standoff = Matrix.Translation((0.200, -0.195, sz)) @ rot_y
    bmesh.ops.create_cone(
        bm_handle,
        cap_ends=True,
        cap_tris=False,
        segments=12,
        radius1=0.005,
        radius2=0.005,
        depth=0.034,
        matrix=mat_standoff
    )

me_handle = bpy.data.meshes.new("handle")
bm_handle.to_mesh(me_handle)
bm_handle.free()
ob_handle = bpy.data.objects.new("handle", me_handle)
ob_handle.data.materials.append(mat_handle)
bpy.context.scene.collection.objects.link(ob_handle)

# Sanity check
for _n in ['carcass', 'door', 'handle']:
    assert _n in bpy.data.objects, f"Object {_n} missing"
