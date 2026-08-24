import bpy
import bmesh
import math
from mathutils import Vector, Matrix

# Clear existing mesh objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

def create_material(name, base_color, metallic=0.0, roughness=0.5):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = base_color
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return mat

mat_dark_steel = create_material("DarkSteel", (0.12, 0.13, 0.14, 1.0), metallic=0.85, roughness=0.35)
mat_galv_steel = create_material("GalvanizedSteel", (0.75, 0.76, 0.78, 1.0), metallic=0.9, roughness=0.25)
mat_rubber = create_material("Rubber", (0.04, 0.04, 0.04, 1.0), metallic=0.0, roughness=0.8)
mat_grip = create_material("RubberGrip", (0.02, 0.02, 0.02, 1.0), metallic=0.0, roughness=0.7)
mat_tray = create_material("OliveGreenPowderCoat", (0.18, 0.25, 0.12, 1.0), metallic=0.15, roughness=0.38)

def add_smooth_pipe(bm, points, radius=0.012, segments=12):
    """Extrude a continuous smooth circular pipe along a 3D polyline."""
    n_pts = len(points)
    if n_pts < 2:
        return
    
    pts = [Vector(p) for p in points]
    
    # Compute tangents at each point
    tangents = []
    for i in range(n_pts):
        if i == 0:
            t = (pts[1] - pts[0]).normalized()
        elif i == n_pts - 1:
            t = (pts[-1] - pts[-2]).normalized()
        else:
            t1 = (pts[i] - pts[i-1]).normalized()
            t2 = (pts[i+1] - pts[i]).normalized()
            t = (t1 + t2).normalized()
            if t.length < 1e-4:
                t = t1
        tangents.append(t)
    
    # Coordinate frames along the path
    rings = []
    # Initial arbitrary normal for the first point
    t0 = tangents[0]
    up = Vector((0, 0, 1))
    if abs(t0.dot(up)) > 0.9:
        up = Vector((0, 1, 0))
    n0 = t0.cross(up).normalized()
    b0 = t0.cross(n0).normalized()
    
    cur_n = n0
    cur_b = b0
    
    for i in range(n_pts):
        p = pts[i]
        t = tangents[i]
        
        # Parallel transport normal to current tangent
        cur_n = (cur_n - t * cur_n.dot(t)).normalized()
        cur_b = t.cross(cur_n).normalized()
        
        # Miter correction factor if intermediate corner
        scale = 1.0
        if 0 < i < n_pts - 1:
            t_prev = (pts[i] - pts[i-1]).normalized()
            dot_val = max(-0.99, min(0.99, t_prev.dot(t)))
            angle = math.acos(dot_val)
            scale = min(1.3, 1.0 / max(0.5, math.cos(angle)))
        
        ring_verts = []
        for s in range(segments):
            theta = 2 * math.pi * s / segments
            offset = (cur_n * math.cos(theta) + cur_b * math.sin(theta)) * (radius * scale)
            ring_verts.append(bm.verts.new(p + offset))
        rings.append(ring_verts)
    
    # Cap start
    bm.faces.new(rings[0])
    
    # Bridge rings with quad faces
    for i in range(n_pts - 1):
        r1 = rings[i]
        r2 = rings[i+1]
        for s in range(segments):
            s_next = (s + 1) % segments
            bm.faces.new([r1[s], r2[s], r2[s_next], r1[s_next]])
            
    # Cap end
    bm.faces.new(reversed(rings[-1]))

def add_cylinder_between(bm, p1, p2, radius=0.012, segments=12):
    """Add a single cylinder tube with closed caps between p1 and p2."""
    add_smooth_pipe(bm, [p1, p2], radius=radius, segments=segments)


# -------------------------------------------------------------
# 1. CHASSIS LINK
# Target bbox: centre (0.000, 0.080, 0.310), extents (0.660, 1.440, 0.620)
# X: [-0.330, 0.330], Y: [-0.640, 0.800], Z: [0.000, 0.620]
# -------------------------------------------------------------
bm_chassis = bmesh.new()

for side in [-1, 1]:
    x_grip = side * 0.314
    x_rear = side * 0.27
    x_mid = side * 0.20
    x_front = side * 0.075
    
    # Continuous longitudinal rail
    rail_pts = [
        Vector((x_grip, 0.784, 0.604)),     # Rear handle tip
        Vector((x_grip, 0.62, 0.56)),      # Grip end
        Vector((x_rear, 0.40, 0.42)),      # Rear transition
        Vector((x_mid, 0.15, 0.27)),       # Under tray rear
        Vector((x_mid, -0.10, 0.26)),      # Under tray mid
        Vector((x_front, -0.35, 0.24)),    # Under tray front
        Vector((x_front, -0.55, 0.20)),    # Axle fork point
    ]
    add_smooth_pipe(bm_chassis, rail_pts, radius=0.012, segments=12)
    
    # Rubber grip sleeve
    add_cylinder_between(bm_chassis, Vector((x_grip, 0.784, 0.604)), Vector((x_grip, 0.62, 0.56)), radius=0.016, segments=12)
    
    # Continuous A-frame grounded support leg
    leg_x = side * 0.24
    leg_pts = [
        Vector((x_mid, 0.15, 0.27)),
        Vector((leg_x, 0.26, 0.009)),
        Vector((leg_x, 0.36, 0.009)),
        Vector((x_rear, 0.40, 0.42))
    ]
    add_smooth_pipe(bm_chassis, leg_pts, radius=0.010, segments=12)
    
    # Leg foot pad / skid shoe resting at z=0 (Z from 0.000 to 0.006)
    foot_box = bmesh.ops.create_cube(bm_chassis, size=1.0)
    bmesh.ops.scale(bm_chassis, vec=(0.034, 0.12, 0.006), verts=foot_box['verts'])
    bmesh.ops.translate(bm_chassis, vec=(leg_x, 0.31, 0.003), verts=foot_box['verts'])

    # Leg diagonal brace to crossbar
    add_cylinder_between(bm_chassis, Vector((leg_x, 0.26, 0.009)), Vector((0.0, 0.26, 0.13)), radius=0.008, segments=10)

    # Front bumper nose loop (curves ahead of axle, reaches Y = -0.640)
    bumper_pts = [
        Vector((x_front, -0.55, 0.20)),
        Vector((side * 0.09, -0.631, 0.20)),
        Vector((side * 0.09, -0.631, 0.31)),
        Vector((side * 0.075, -0.50, 0.32)),
    ]
    add_smooth_pipe(bm_chassis, bumper_pts, radius=0.009, segments=12)

    # Axle mounting plates at (side * 0.062, -0.55, 0.20)
    plate = bmesh.ops.create_cube(bm_chassis, size=1.0)
    bmesh.ops.scale(bm_chassis, vec=(0.004, 0.045, 0.045), verts=plate['verts'])
    bmesh.ops.translate(bm_chassis, vec=(side * 0.062, -0.55, 0.20), verts=plate['verts'])
    
    # Tray hinge brackets on chassis at (side * 0.15, -0.45, 0.35)
    hinge_chassis = bmesh.ops.create_cube(bm_chassis, size=1.0)
    bmesh.ops.scale(bm_chassis, vec=(0.006, 0.03, 0.04), verts=hinge_chassis['verts'])
    bmesh.ops.translate(bm_chassis, vec=(side * 0.15, -0.45, 0.35), verts=hinge_chassis['verts'])
    
    # Upright post supporting hinge bracket from chassis crossbar/rail
    add_cylinder_between(bm_chassis, Vector((side * 0.15, -0.45, 0.20)), Vector((side * 0.15, -0.45, 0.35)), radius=0.008, segments=10)
    add_cylinder_between(bm_chassis, Vector((x_front, -0.45, 0.20)), Vector((side * 0.15, -0.45, 0.20)), radius=0.008, segments=10)

# Crossbars between left and right
# Crossbar 1: rear between handles
add_cylinder_between(bm_chassis, Vector((-0.26, 0.42, 0.43)), Vector((0.26, 0.42, 0.43)), radius=0.010, segments=12)
# Crossbar 2: under tray mid (at Z = 0.26, well below tray bottom Z=0.30)
add_cylinder_between(bm_chassis, Vector((-0.20, 0.05, 0.26)), Vector((0.20, 0.05, 0.26)), radius=0.010, segments=12)
# Crossbar 3: between legs at z=0.13
add_cylinder_between(bm_chassis, Vector((-0.24, 0.26, 0.13)), Vector((0.24, 0.26, 0.13)), radius=0.008, segments=10)

me_chassis = bpy.data.meshes.new('chassis')
bm_chassis.to_mesh(me_chassis)
bm_chassis.free()
obj_chassis = bpy.data.objects.new('chassis', me_chassis)
bpy.context.scene.collection.objects.link(obj_chassis)
obj_chassis.data.materials.append(mat_dark_steel)


# -------------------------------------------------------------
# 2. WHEEL LINK
# Target bbox: centre (0.000, -0.550, 0.200), extents (0.120, 0.400, 0.400)
# X: [-0.060, 0.060], Y: [-0.750, -0.350], Z: [0.000, 0.400]
# -------------------------------------------------------------
bm_wheel = bmesh.new()

# Axle shaft through center (X from -0.060 to +0.060, touches chassis plate at ±0.060)
axle_cyl = bmesh.ops.create_cone(bm_wheel, cap_ends=True, segments=16, radius1=0.007, radius2=0.007, depth=0.120)
bmesh.ops.rotate(bm_wheel, matrix=Matrix.Rotation(math.pi/2, 4, 'Y'), verts=axle_cyl['verts'])
bmesh.ops.translate(bm_wheel, vec=(0.0, -0.55, 0.20), verts=axle_cyl['verts'])

# Central wheel hub / bearing
hub_cyl = bmesh.ops.create_cone(bm_wheel, cap_ends=True, segments=20, radius1=0.028, radius2=0.028, depth=0.075)
bmesh.ops.rotate(bm_wheel, matrix=Matrix.Rotation(math.pi/2, 4, 'Y'), verts=hub_cyl['verts'])
bmesh.ops.translate(bm_wheel, vec=(0.0, -0.55, 0.20), verts=hub_cyl['verts'])

# Wheel rim (inner steel rim)
rim_cyl = bmesh.ops.create_cone(bm_wheel, cap_ends=True, segments=24, radius1=0.125, radius2=0.125, depth=0.045)
bmesh.ops.rotate(bm_wheel, matrix=Matrix.Rotation(math.pi/2, 4, 'Y'), verts=rim_cyl['verts'])
bmesh.ops.translate(bm_wheel, vec=(0.0, -0.55, 0.20), verts=rim_cyl['verts'])

# Steel rim flange lip
for s in [-1, 1]:
    flange = bmesh.ops.create_cone(bm_wheel, cap_ends=True, segments=24, radius1=0.132, radius2=0.132, depth=0.004)
    bmesh.ops.rotate(bm_wheel, matrix=Matrix.Rotation(math.pi/2, 4, 'Y'), verts=flange['verts'])
    bmesh.ops.translate(bm_wheel, vec=(s * 0.024, -0.55, 0.20), verts=flange['verts'])

# Stamped steel spokes (4 spokes)
for i in range(4):
    angle = i * math.pi / 4
    spoke = bmesh.ops.create_cube(bm_wheel, size=1.0)
    bmesh.ops.scale(bm_wheel, vec=(0.008, 0.018, 0.22), verts=spoke['verts'])
    bmesh.ops.rotate(bm_wheel, matrix=Matrix.Rotation(angle, 4, 'X'), verts=spoke['verts'])
    bmesh.ops.translate(bm_wheel, vec=(0.0, -0.55, 0.20), verts=spoke['verts'])

# Rubber Tire: Torus profile
# Outer radius = 0.200 -> touching z=0 at lowest point (Z = 0.000)
# Width = 0.106 (X from -0.053 to +0.053)
tire_verts = []
u_segs = 32
v_segs = 16
r_major = 0.135
r_minor = 0.065

for u in range(u_segs):
    phi = 2 * math.pi * u / u_segs
    c_y = -0.55 + r_major * math.sin(phi)
    c_z = 0.20 + r_major * math.cos(phi)
    
    n_y = math.sin(phi)
    n_z = math.cos(phi)
    
    slice_verts = []
    for v in range(v_segs):
        psi = 2 * math.pi * v / v_segs
        vx = math.sin(psi) * (r_minor * 0.78) # max vx = ~0.050
        rad = math.cos(psi) * r_minor
        vy = c_y + n_y * rad
        vz = c_z + n_z * rad
        
        # Rib tread pattern
        if math.cos(psi) > 0.3:
            tread_rib = 0.0015 * math.sin(u * 16)
            vy += n_y * tread_rib
            vz += n_z * tread_rib
            
        slice_verts.append(bm_wheel.verts.new(Vector((vx, vy, vz))))
    tire_verts.append(slice_verts)

for u in range(u_segs):
    u_next = (u + 1) % u_segs
    for v in range(v_segs):
        v_next = (v + 1) % v_segs
        bm_wheel.faces.new([
            tire_verts[u][v],
            tire_verts[u_next][v],
            tire_verts[u_next][v_next],
            tire_verts[u][v_next]
        ])

me_wheel = bpy.data.meshes.new('wheel')
bm_wheel.to_mesh(me_wheel)
bm_wheel.free()
obj_wheel = bpy.data.objects.new('wheel', me_wheel)
bpy.context.scene.collection.objects.link(obj_wheel)
obj_wheel.data.materials.append(mat_rubber)


# -------------------------------------------------------------
# 3. TRAY LINK
# Target bbox: centre (0.000, -0.050, 0.480), extents (0.620, 0.880, 0.360)
# X: [-0.310, 0.310], Y: [-0.490, 0.390], Z: [0.300, 0.660]
# Pivot: (0.000, -0.450, 0.350)
# -------------------------------------------------------------
bm_tray = bmesh.new()

layers_def = [
    # (z, y_front, y_back, x_front, x_back)
    (0.30, -0.34, 0.24, 0.17, 0.19), # bottom outer (Z=0.30)
    (0.42, -0.40, 0.30, 0.23, 0.25), # lower-mid outer
    (0.55, -0.45, 0.35, 0.27, 0.28), # upper-mid outer
    (0.64, -0.48, 0.38, 0.30, 0.305), # top rim outer
    (0.66, -0.49, 0.39, 0.305, 0.31), # rolled lip peak (Z=0.66, Y=[-0.49, 0.39], X=[-0.31, 0.31])
    (0.64, -0.475, 0.375, 0.29, 0.295), # inner rim
    (0.55, -0.44, 0.34, 0.26, 0.27), # inner upper-mid
    (0.42, -0.39, 0.29, 0.22, 0.24), # inner lower-mid
    (0.31, -0.33, 0.23, 0.16, 0.18), # bottom inner
]

def make_tray_ring(z, yf, yb, xf, xb):
    pts = [
        Vector((-xf * 0.7, yf, z)),
        Vector((xf * 0.7, yf, z)),
        Vector((xf, yf + 0.05, z)),
        Vector((xb, yb - 0.05, z)),
        Vector((xb * 0.8, yb, z)),
        Vector((-xb * 0.8, yb, z)),
        Vector((-xb, yb - 0.05, z)),
        Vector((-xf, yf + 0.05, z)),
    ]
    return pts

rings = []
for z, yf, yb, xf, xb in layers_def:
    ring_pts = make_tray_ring(z, yf, yb, xf, xb)
    v_ring = [bm_tray.verts.new(p) for p in ring_pts]
    rings.append(v_ring)

# Bottom face (outer bottom)
bm_tray.faces.new(reversed(rings[0]))

# Connect rings into walls
for r in range(len(rings) - 1):
    r1 = rings[r]
    r2 = rings[r+1]
    n = len(r1)
    for i in range(n):
        i_next = (i + 1) % n
        bm_tray.faces.new([r1[i], r1[i_next], r2[i_next], r2[i]])

# Inner bottom face
bm_tray.faces.new(rings[-1])

# Hinge mounting lugs on tray at pivot (0, -0.45, 0.35)
for side in [-1, 1]:
    lug = bmesh.ops.create_cube(bm_tray, size=1.0)
    bmesh.ops.scale(bm_tray, vec=(0.006, 0.08, 0.03), verts=lug['verts'])
    bmesh.ops.translate(bm_tray, vec=(side * 0.11, -0.41, 0.365), verts=lug['verts'])

    # Hinge pin boss / eyelet at pivot Y = -0.45, Z = 0.35
    eyelet = bmesh.ops.create_cone(bm_tray, cap_ends=True, segments=12, radius1=0.012, radius2=0.012, depth=0.014)
    bmesh.ops.rotate(bm_tray, matrix=Matrix.Rotation(math.pi/2, 4, 'Y'), verts=eyelet['verts'])
    bmesh.ops.translate(bm_tray, vec=(side * 0.11, -0.45, 0.35), verts=eyelet['verts'])

    # Hinge pin connecting chassis and tray (touches chassis upright at X = ±0.142)
    pin = bmesh.ops.create_cone(bm_tray, cap_ends=True, segments=10, radius1=0.005, radius2=0.005, depth=0.046)
    bmesh.ops.rotate(bm_tray, matrix=Matrix.Rotation(math.pi/2, 4, 'Y'), verts=pin['verts'])
    bmesh.ops.translate(bm_tray, vec=(side * 0.120, -0.45, 0.35), verts=pin['verts'])

# Stiffening ribs / embossed ridges along the bottom
for rib_y in [-0.15, 0.05]:
    rib = bmesh.ops.create_cube(bm_tray, size=1.0)
    bmesh.ops.scale(bm_tray, vec=(0.26, 0.02, 0.004), verts=rib['verts'])
    bmesh.ops.translate(bm_tray, vec=(0.0, rib_y, 0.298), verts=rib['verts'])

me_tray = bpy.data.meshes.new('tray')
bm_tray.to_mesh(me_tray)
bm_tray.free()
obj_tray = bpy.data.objects.new('tray', me_tray)
bpy.context.scene.collection.objects.link(obj_tray)
obj_tray.data.materials.append(mat_tray)

# Sanity check
for _n in ['chassis', 'wheel', 'tray']:
    assert _n in bpy.data.objects, _n
