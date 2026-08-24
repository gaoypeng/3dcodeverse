"""ArticulatedHouseDoor — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

Links:
- frame
- door_leaf
- lever_handle
- latch_bolt
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear existing objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

def make_material(name, diffuse_color, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = diffuse_color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat

# Distinct materials: off-white frame, warm white semi-gloss door, polished satin nickel hardware
mat_frame = make_material("MatFrame", (0.85, 0.83, 0.80, 1.0), roughness=0.45, metallic=0.0)
mat_door = make_material("MatDoor", (0.96, 0.95, 0.92, 1.0), roughness=0.25, metallic=0.0)
mat_metal = make_material("MatNickel", (0.60, 0.62, 0.64, 1.0), roughness=0.18, metallic=0.92)

def add_cylinder(bm, radius, depth, matrix=Matrix.Identity(4), segments=16):
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=depth,
        matrix=matrix
    )

def add_box(bm, size, matrix=Matrix.Identity(4)):
    bmesh.ops.create_cube(
        bm,
        size=1.0,
        matrix=matrix @ Matrix.Diagonal((size[0], size[1], size[2], 1.0))
    )

# -------------------------------------------------------------
# 1. FRAME
# Plan bbox: center (0.000, 0.000, 1.060), extents (0.960, 0.120, 2.120)
# Frame bounds: x in [-0.480, 0.480], y in [-0.060, 0.060], z in [0.000, 2.120]
# Inner jamb opening: x in [-0.413, 0.413], z in [0.007, 2.033]
# DoorLeaf extents: (0.820, 0.040, 2.020) -> x in [-0.410, 0.410], z in [0.010, 2.030]
# -------------------------------------------------------------

def build_frame():
    bm = bmesh.new()
    
    # Left Jamb: x in [-0.480, -0.413], y in [-0.060, 0.060], z in [0.0, 2.120]
    add_box(bm, (0.067, 0.120, 2.120), Matrix.Translation((-0.4465, 0.0, 1.060)))
    
    # Left door stop rebate on back side (y in [0.024, 0.040], x in [-0.428, -0.413], z in [0.007, 2.033])
    add_box(bm, (0.015, 0.016, 2.026), Matrix.Translation((-0.4205, 0.032, 1.020)))

    # Right Jamb: x in [0.413, 0.480], y in [-0.060, 0.060], z in [0.0, 2.120]
    # Lower section: z in [0.0, 0.960]
    add_box(bm, (0.067, 0.120, 0.960), Matrix.Translation((0.4465, 0.0, 0.480)))
    # Upper section: z in [1.040, 2.120]
    add_box(bm, (0.067, 0.120, 1.080), Matrix.Translation((0.4465, 0.0, 1.580)))
    # Back of strike box: x in [0.443, 0.480], z in [0.960, 1.040]
    add_box(bm, (0.037, 0.120, 0.080), Matrix.Translation((0.4615, 0.0, 1.000)))
    # Strike plate lips above/below latch strike hole (x in [0.413, 0.443], y in [-0.060, -0.025] and [0.025, 0.060])
    add_box(bm, (0.030, 0.035, 0.080), Matrix.Translation((0.428, -0.0425, 1.000)))
    add_box(bm, (0.030, 0.035, 0.080), Matrix.Translation((0.428, 0.0425, 1.000)))
    
    # Right door stop (on inner face x in [0.413, 0.428], y in [0.024, 0.040], z in [0.007, 2.033] outside strike hole)
    add_box(bm, (0.015, 0.016, 0.953), Matrix.Translation((0.4205, 0.032, 0.4835)))
    add_box(bm, (0.015, 0.016, 0.993), Matrix.Translation((0.4205, 0.032, 1.5365)))

    # Top Header: x in [-0.480, 0.480], y in [-0.060, 0.060], z in [2.033, 2.120]
    add_box(bm, (0.960, 0.120, 0.087), Matrix.Translation((0.0, 0.0, 2.0765)))
    # Top door stop: x in [-0.413, 0.413], y in [0.024, 0.040], z in [2.033, 2.048]
    add_box(bm, (0.826, 0.016, 0.015), Matrix.Translation((0.0, 0.032, 2.0405)))

    # Bottom sill / threshold touching ground z=0 to 0.007, x in [-0.413, 0.413], y in [-0.060, 0.060]
    add_box(bm, (0.826, 0.120, 0.007), Matrix.Translation((0.0, 0.0, 0.0035)))

    # Frame architrave / decorative casing trims (flush with overall extents y in [-0.060, 0.060]):
    # Front casing left & right:
    add_box(bm, (0.067, 0.005, 2.120), Matrix.Translation((-0.4465, -0.0575, 1.060)))
    add_box(bm, (0.067, 0.005, 2.120), Matrix.Translation((0.4465, -0.0575, 1.060)))
    # Front casing top:
    add_box(bm, (0.960, 0.005, 0.067), Matrix.Translation((0.0, -0.0575, 2.0865)))

    # Back casing left & right:
    add_box(bm, (0.067, 0.005, 2.120), Matrix.Translation((-0.4465, 0.0575, 1.060)))
    add_box(bm, (0.067, 0.005, 2.120), Matrix.Translation((0.4465, 0.0575, 1.060)))
    # Back casing top:
    add_box(bm, (0.960, 0.005, 0.067), Matrix.Translation((0.0, 0.0575, 2.0865)))

    # Hinge knuckles fixed to frame on left side:
    for hz in [0.30, 1.02, 1.74]:
        add_cylinder(bm, radius=0.004, depth=0.040, matrix=Matrix.Translation((-0.410, -0.024, hz)))
        add_box(bm, (0.018, 0.003, 0.036), Matrix.Translation((-0.419, -0.0225, hz)))

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new('frame')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('frame', me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat_frame)
    return obj

# -------------------------------------------------------------
# 2. DOOR LEAF
# Plan bbox: center (0.000, 0.000, 1.020), extents: (0.820, 0.040, 2.020)
# Leaf bounds: x in [-0.410, 0.410], y in [-0.020, 0.020], z in [0.010, 2.030]
# -------------------------------------------------------------

def build_door_leaf():
    bm = bmesh.new()
    
    # Left stile: x in [-0.410, -0.280], y in [-0.020, 0.020], z in [0.010, 2.030]
    add_box(bm, (0.130, 0.040, 2.020), Matrix.Translation((-0.345, 0.0, 1.020)))
    
    # Right stile:
    # Bottom portion: z in [0.010, 0.940]
    add_box(bm, (0.130, 0.040, 0.930), Matrix.Translation((0.345, 0.0, 0.475)))
    # Top portion: z in [1.060, 2.030]
    add_box(bm, (0.130, 0.040, 0.970), Matrix.Translation((0.345, 0.0, 1.545)))
    
    # Lock block structure:
    # Upper lock block: z in [1.025, 1.060], x in [0.280, 0.410]
    add_box(bm, (0.130, 0.040, 0.035), Matrix.Translation((0.345, 0.0, 1.0425)))
    # Lower lock block: z in [0.940, 0.975], x in [0.280, 0.410]
    add_box(bm, (0.130, 0.040, 0.035), Matrix.Translation((0.345, 0.0, 0.9575)))
    # Inner bridge: x in [0.280, 0.310], z in [0.975, 1.025]
    add_box(bm, (0.030, 0.040, 0.050), Matrix.Translation((0.295, 0.0, 1.000)))
    # Outer bridge: x in [0.350, 0.380], z in [0.975, 1.025]
    add_box(bm, (0.030, 0.040, 0.050), Matrix.Translation((0.365, 0.0, 1.000)))
    
    # Mortise faceplate cheeks (front and back walls around latch pocket):
    # Front cheek: y in [-0.020, -0.010], x in [0.380, 0.410], z in [0.975, 1.025]
    add_box(bm, (0.030, 0.010, 0.050), Matrix.Translation((0.395, -0.015, 1.000)))
    # Back cheek: y in [0.010, 0.020], x in [0.380, 0.410], z in [0.975, 1.025]
    add_box(bm, (0.030, 0.010, 0.050), Matrix.Translation((0.395, 0.015, 1.000)))

    # Rails (top, mid, bottom) between x in [-0.280, 0.280]
    # Top rail: z in [1.900, 2.030]
    add_box(bm, (0.560, 0.040, 0.130), Matrix.Translation((0.0, 0.0, 1.965)))
    # Mid rail (lock rail): z in [0.920, 1.080]
    add_box(bm, (0.560, 0.040, 0.160), Matrix.Translation((0.0, 0.0, 1.000)))
    # Bottom rail (kick rail): z in [0.010, 0.180]
    add_box(bm, (0.560, 0.040, 0.170), Matrix.Translation((0.0, 0.0, 0.095)))

    # Upper panel (recessed thin panel): x in [-0.280, 0.280], y in [-0.008, 0.008], z in [1.080, 1.900]
    add_box(bm, (0.560, 0.016, 0.820), Matrix.Translation((0.0, 0.0, 1.490)))
    # Upper panel moulding / bevel border
    add_box(bm, (0.540, 0.026, 0.800), Matrix.Translation((0.0, 0.0, 1.490)))

    # Lower panel (recessed thin panel): x in [-0.280, 0.280], y in [-0.008, 0.008], z in [0.180, 0.920]
    add_box(bm, (0.560, 0.016, 0.740), Matrix.Translation((0.0, 0.0, 0.550)))
    add_box(bm, (0.540, 0.026, 0.720), Matrix.Translation((0.0, 0.0, 0.550)))

    # Door side hinge plates (overlapping with door leaf stile on left):
    for hz in [0.30, 1.02, 1.74]:
        add_box(bm, (0.018, 0.003, 0.036), Matrix.Translation((-0.401, -0.0225, hz)))

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new('door_leaf')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('door_leaf', me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat_door)
    return obj

# -------------------------------------------------------------
# 3. LEVER HANDLE
# Plan bbox: center (0.330, -0.035, 1.000), extents (0.140, 0.110, 0.080)
# Bounds: x: [0.260, 0.400], y: [-0.090, 0.020], z: [0.960, 1.040]
# -------------------------------------------------------------

def build_lever_handle():
    bm = bmesh.new()
    rot_x = Matrix.Rotation(math.radians(90), 4, 'X')
    
    # 1. Spindle: Y-cylinder from y=-0.020 to y=0.018, radius 0.0035
    add_cylinder(bm, radius=0.0035, depth=0.038, matrix=Matrix.Translation((0.330, -0.001, 1.000)) @ rot_x)
    
    # 2. Front Escutcheon Plate (rosette): center (0.330, -0.0225, 1.000), size (0.046, 0.005, 0.080)
    # y in [-0.025, -0.020] -> sits flush against front face at y = -0.020
    add_box(bm, (0.046, 0.005, 0.080), Matrix.Translation((0.330, -0.0225, 1.000)))
    
    # 3. Front Collar Hub (protrudes forward from rose): y in [-0.075, -0.025], radius 0.008
    add_cylinder(bm, radius=0.008, depth=0.050, matrix=Matrix.Translation((0.330, -0.050, 1.000)) @ rot_x)
    
    # 4. Front Lever Grip: extends to the right (x in [0.260, 0.400]), y centered at -0.083, z in [0.993, 1.007]
    add_box(bm, (0.136, 0.014, 0.014), Matrix.Translation((0.330, -0.083, 1.000)))
    add_cylinder(bm, radius=0.007, depth=0.014, matrix=Matrix.Translation((0.262, -0.083, 1.000)) @ rot_x)
    add_cylinder(bm, radius=0.007, depth=0.014, matrix=Matrix.Translation((0.398, -0.083, 1.000)) @ rot_x)
    
    # 5. Back Escutcheon Plate (rosette on inside of door):
    # center (0.330, 0.0225, 1.000), size (0.046, 0.005, 0.080), y in [0.020, 0.025] -> flush against back face at y=0.020
    add_box(bm, (0.046, 0.005, 0.080), Matrix.Translation((0.330, 0.0225, 1.000)))
    
    # 6. Keyhole detail:
    add_cylinder(bm, radius=0.003, depth=0.005, matrix=Matrix.Translation((0.330, -0.024, 0.970)) @ rot_x)

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new('lever_handle')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('lever_handle', me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat_metal)
    return obj

# -------------------------------------------------------------
# 4. LATCH BOLT
# Plan bbox: center (0.415, 0.000, 1.000), extents: (0.030, 0.020, 0.025)
# Bounds: x: [0.400, 0.430], y: [-0.010, 0.010], z: [0.9875, 1.0125]
# Latch bolt extends to x = 0.426.
# Sits flush against mortise cheeks at y = +-0.010 to satisfy contact/connectivity.
# -------------------------------------------------------------

def build_latch_bolt():
    bm = bmesh.new()
    
    # Main Bolt Body (x in [0.400, 0.420], y in [-0.010, 0.010], z in [0.988, 1.012])
    add_box(bm, (0.020, 0.020, 0.024), Matrix.Translation((0.410, 0.0, 1.000)))
    
    # Beveled Latch Nose (x in [0.420, 0.426]):
    add_box(bm, (0.006, 0.014, 0.024), Matrix.Translation((0.423, -0.002, 1.000)))

    # Auxiliary anti-friction pin / plunger
    add_box(bm, (0.014, 0.003, 0.014), Matrix.Translation((0.413, 0.007, 1.000)))

    # Retraction guide tail (x in [0.395, 0.400], y in [-0.008, 0.008], z in [0.990, 1.010])
    add_box(bm, (0.005, 0.016, 0.020), Matrix.Translation((0.3975, 0.0, 1.000)))

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)

    me = bpy.data.meshes.new('latch_bolt')
    bm.to_mesh(me)
    bm.free()
    
    obj = bpy.data.objects.new('latch_bolt', me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat_metal)
    return obj

# Build all links
build_frame()
build_door_leaf()
build_lever_handle()
build_latch_bolt()

for _n in ['frame', 'door_leaf', 'lever_handle', 'latch_bolt']:
    assert _n in bpy.data.objects, _n
