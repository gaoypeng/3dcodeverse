import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear all existing objects in scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def create_material(name, diffuse_color, roughness=0.5, metallic=0.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = diffuse_color
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
    return mat

mat_black_nylon = create_material("BlackNylon", (0.08, 0.08, 0.08, 1.0), roughness=0.6, metallic=0.1)
mat_chrome = create_material("Chrome", (0.85, 0.85, 0.88, 1.0), roughness=0.15, metallic=0.95)
mat_fabric = create_material("CharcoalFabric", (0.12, 0.13, 0.14, 1.0), roughness=0.85, metallic=0.0)
mat_mesh = create_material("DarkMesh", (0.05, 0.05, 0.06, 1.0), roughness=0.7, metallic=0.05)

def finalize_object(name, bm, material):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(material)
    bpy.context.scene.collection.objects.link(ob)
    return ob

# -------------------------------------------------------------------------
# 1. BASE: Five-star base with caster wheels and central hub
# Target bbox: center [0, 0, 0.10], extents [0.66, 0.66, 0.20]
# -------------------------------------------------------------------------
bm_base = bmesh.new()

# Central hub cylinder (z from 0.06 to 0.20, radius 0.045)
bmesh.ops.create_cone(
    bm_base,
    cap_ends=True,
    segments=24,
    radius1=0.045,
    radius2=0.042,
    depth=0.14,
    matrix=Matrix.Translation((0, 0, 0.13))
)

# 5 Star legs and casters
num_legs = 5
leg_radius = 0.315
for i in range(num_legs):
    angle = i * (2 * math.pi / num_legs)
    ca, sa = math.cos(angle), math.sin(angle)
    
    # Caster socket housing at the tip (r = 0.315)
    tip_x = leg_radius * ca
    tip_y = leg_radius * sa
    
    bmesh.ops.create_cone(
        bm_base,
        cap_ends=True,
        segments=12,
        radius1=0.018,
        radius2=0.018,
        depth=0.035,
        matrix=Matrix.Translation((tip_x, tip_y, 0.045))
    )
    
    # Dual caster wheels (diameter 50mm, radius 0.025, resting on z=0)
    wheel_rot = Matrix.Rotation(angle, 4, 'Z') @ Matrix.Rotation(math.pi / 2, 4, 'Y')
    
    # Left wheel
    w_offset_l = Vector((-sa * 0.014, ca * 0.014, 0))
    bmesh.ops.create_cone(
        bm_base,
        cap_ends=True,
        segments=16,
        radius1=0.025,
        radius2=0.025,
        depth=0.012,
        matrix=Matrix.Translation(Vector((tip_x, tip_y, 0.025)) + w_offset_l) @ wheel_rot
    )
    # Right wheel
    w_offset_r = Vector((sa * 0.014, -ca * 0.014, 0))
    bmesh.ops.create_cone(
        bm_base,
        cap_ends=True,
        segments=16,
        radius1=0.025,
        radius2=0.025,
        depth=0.012,
        matrix=Matrix.Translation(Vector((tip_x, tip_y, 0.025)) + w_offset_r) @ wheel_rot
    )
    
    # Radial leg body
    v1 = Vector((0.025 * -sa, 0.025 * ca, 0.11))
    v2 = Vector((-0.025 * -sa, -0.025 * ca, 0.11))
    v3 = Vector((-0.025 * -sa, -0.025 * ca, 0.07))
    v4 = Vector((0.025 * -sa, 0.025 * ca, 0.07))
    
    t_center = Vector((tip_x, tip_y, 0.045))
    v5 = t_center + Vector((0.016 * -sa, 0.016 * ca, 0.02))
    v6 = t_center + Vector((-0.016 * -sa, -0.016 * ca, 0.02))
    v7 = t_center + Vector((-0.016 * -sa, -0.016 * ca, -0.015))
    v8 = t_center + Vector((0.016 * -sa, 0.016 * ca, -0.015))
    
    verts = [bm_base.verts.new(v) for v in (v1, v2, v3, v4, v5, v6, v7, v8)]
    bm_base.faces.new((verts[0], verts[1], verts[2], verts[3]))
    bm_base.faces.new((verts[4], verts[7], verts[6], verts[5]))
    bm_base.faces.new((verts[0], verts[4], verts[5], verts[1]))
    bm_base.faces.new((verts[2], verts[6], verts[7], verts[3]))
    bm_base.faces.new((verts[1], verts[5], verts[6], verts[2]))
    bm_base.faces.new((verts[0], verts[3], verts[7], verts[4]))

base_ob = finalize_object("base", bm_base, mat_black_nylon)

# -------------------------------------------------------------------------
# 2. PISTON: Telescoping pneumatic gas-lift cylinder (z from 0.20 to 0.41)
# Target bbox: center [0, 0, 0.305], extents [0.06, 0.06, 0.21]
# -------------------------------------------------------------------------
bm_piston = bmesh.new()

# Outer sleeve (lower half, z from 0.20 to 0.30, radius 0.03 -> extents 0.06 x 0.06)
bmesh.ops.create_cone(
    bm_piston,
    cap_ends=True,
    segments=24,
    radius1=0.030,
    radius2=0.030,
    depth=0.10,
    matrix=Matrix.Translation((0, 0, 0.25))
)
# Inner chrome cylinder shaft (upper half, z from 0.29 to 0.41)
bmesh.ops.create_cone(
    bm_piston,
    cap_ends=True,
    segments=24,
    radius1=0.022,
    radius2=0.022,
    depth=0.12,
    matrix=Matrix.Translation((0, 0, 0.35))
)

piston_ob = finalize_object("piston", bm_piston, mat_chrome)

# -------------------------------------------------------------------------
# 3. SEAT: Mechanism, cushion, armrests (z from 0.38 to 0.70)
# Target bbox: center [0, -0.03, 0.54], extents [0.64, 0.52, 0.32]
# -------------------------------------------------------------------------
bm_seat = bmesh.new()

# Under-seat height control lever extending down to z=0.38
bmesh.ops.create_cone(
    bm_seat,
    cap_ends=True,
    segments=12,
    radius1=0.007,
    radius2=0.007,
    depth=0.10,
    matrix=Matrix.Translation((0.14, -0.03, 0.40)) @ Matrix.Rotation(math.radians(25), 4, 'Y')
)
# Lever paddle handle (z at 0.38)
bmesh.ops.create_cube(
    bm_seat,
    size=1.0,
    matrix=Matrix.Translation((0.18, -0.03, 0.388)) @ Matrix.Scale(0.03, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.05, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.015, 4, Vector((0, 0, 1)))
)

# Under-seat mechanism housing (z from 0.41 to 0.44)
bmesh.ops.create_cone(
    bm_seat,
    cap_ends=True,
    segments=20,
    radius1=0.065,
    radius2=0.065,
    depth=0.03,
    matrix=Matrix.Translation((0, 0, 0.425))
)
bmesh.ops.create_cube(
    bm_seat,
    size=1.0,
    matrix=Matrix.Translation((0, -0.02, 0.435)) @ Matrix.Scale(0.24, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.24, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.02, 4, Vector((0, 0, 1)))
)

# Rear bracket reaching back toward pivot (z from 0.43 to 0.47, y from 0.08 to 0.23)
bmesh.ops.create_cube(
    bm_seat,
    size=1.0,
    matrix=Matrix.Translation((0, 0.155, 0.455)) @ Matrix.Scale(0.09, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.15, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.03, 4, Vector((0, 0, 1)))
)

# Seat hinge outer tabs at (x = +/-0.032, y = 0.16, z = 0.48)
for side in [-1, 1]:
    bmesh.ops.create_cone(
        bm_seat,
        cap_ends=True,
        segments=16,
        radius1=0.012,
        radius2=0.012,
        depth=0.015,
        matrix=Matrix.Translation((side * 0.032, 0.16, 0.48)) @ Matrix.Rotation(math.pi / 2, 4, 'Y')
    )

# Seat cushion (y from -0.29 to +0.13 -> center -0.08, extent 0.42; x from -0.25 to +0.25; z from 0.445 to 0.515)
bmesh.ops.create_cube(
    bm_seat,
    size=1.0,
    matrix=Matrix.Translation((0, -0.08, 0.48)) @ Matrix.Scale(0.50, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.42, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.07, 4, Vector((0, 0, 1)))
)

# Waterfall front cushion edge (y from -0.29 to -0.25, rounded downwards)
bmesh.ops.create_cone(
    bm_seat,
    cap_ends=True,
    segments=16,
    radius1=0.035,
    radius2=0.035,
    depth=0.50,
    matrix=Matrix.Translation((0, -0.255, 0.48)) @ Matrix.Rotation(math.pi / 2, 4, 'Y')
)

# Left and Right Armrests (x to +/-0.32, z to 0.70, y from -0.16 to +0.10)
for side in [-1, 1]:
    x_pos = side * 0.28
    # Structural loop / vertical stalk
    bmesh.ops.create_cube(
        bm_seat,
        size=1.0,
        matrix=Matrix.Translation((x_pos, -0.03, 0.56)) @ Matrix.Scale(0.035, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.045, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.22, 4, Vector((0, 0, 1)))
    )
    # Armrest pad on top (x from +/-0.24 to +/-0.32, y from -0.16 to +0.10, z from 0.67 to 0.70)
    bmesh.ops.create_cube(
        bm_seat,
        size=1.0,
        matrix=Matrix.Translation((x_pos, -0.03, 0.685)) @ Matrix.Scale(0.08, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.26, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.03, 4, Vector((0, 0, 1)))
    )

seat_ob = finalize_object("seat", bm_seat, mat_fabric)

# -------------------------------------------------------------------------
# 4. BACKREST: Tilting backrest assembly (z from 0.48 to 1.02, pivot at 0, 0.16, 0.48)
# Target bbox: center [0, 0.16, 0.75], extents [0.46, 0.18, 0.54] (x: [-0.23, 0.23], y: [0.07, 0.25], z: [0.48, 1.02])
# -------------------------------------------------------------------------
bm_back = bmesh.new()

# Central pivot knuckle (x from -0.018 to +0.018, sits between seat tabs at 0, 0.16, 0.48)
bmesh.ops.create_cone(
    bm_back,
    cap_ends=True,
    segments=16,
    radius1=0.012,
    radius2=0.012,
    depth=0.036,
    matrix=Matrix.Translation((0, 0.16, 0.48)) @ Matrix.Rotation(math.pi / 2, 4, 'Y')
)

# Rear structural spine curving back to y=0.25 and up to z=0.98
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((0, 0.21, 0.60)) @ Matrix.Scale(0.045, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.07, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.22, 4, Vector((0, 0, 1)))
)
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((0, 0.23, 0.80)) @ Matrix.Scale(0.045, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.04, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.24, 4, Vector((0, 0, 1)))
)

# Lumbar support forward curve reaching forward to y=0.07 at z=0.65
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((0, 0.10, 0.65)) @ Matrix.Scale(0.36, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.04, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.10, 4, Vector((0, 0, 1)))
)

# Backrest perimeter frame (z from 0.54 to 1.02, width 0.46 -> x from -0.23 to +0.23)
# Left upright
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((-0.2125, 0.16, 0.78)) @ Matrix.Scale(0.035, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.06, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.46, 4, Vector((0, 0, 1)))
)
# Right upright
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((0.2125, 0.16, 0.78)) @ Matrix.Scale(0.035, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.06, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.46, 4, Vector((0, 0, 1)))
)
# Top crossbar (z up to 1.02)
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((0, 0.17, 1.00)) @ Matrix.Scale(0.46, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.05, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.04, 4, Vector((0, 0, 1)))
)
# Bottom crossbar
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((0, 0.14, 0.56)) @ Matrix.Scale(0.46, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.05, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.04, 4, Vector((0, 0, 1)))
)
# Mesh inner panel (taut mesh back panel)
bmesh.ops.create_cube(
    bm_back,
    size=1.0,
    matrix=Matrix.Translation((0, 0.15, 0.78)) @ Matrix.Scale(0.40, 4, Vector((1, 0, 0))) @ Matrix.Scale(0.015, 4, Vector((0, 1, 0))) @ Matrix.Scale(0.42, 4, Vector((0, 0, 1)))
)

back_ob = finalize_object("backrest", bm_back, mat_mesh)
