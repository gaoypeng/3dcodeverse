"""ArticulatedDeskLamp — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

Authoring contract:
- Z is UP, -Y is FRONT, +X is RIGHT.
- Object stands on z=0, centered on Z.
- ONE mesh object per URDF link, named EXACTLY like the link.
- Objects built in WORLD coordinates at rest pose (URDF q=0).
"""
import bpy
import bmesh
import math
from mathutils import Vector, Matrix


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        bpy.data.meshes.remove(block)
    for block in bpy.data.materials:
        bpy.data.materials.remove(block)


def create_material(name, base_color, metallic=0.2, roughness=0.5):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = base_color
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def add_lathe(bm, origin, axis, profile, segs=32):
    """
    Creates a revolved manifold body around `axis` passing through `origin`.
    `profile` is a list of (radius, height_along_axis).
    """
    origin = Vector(origin)
    axis = Vector(axis).normalized()
    up = Vector((0, 0, 1))
    if abs(axis.dot(up)) > 0.99:
        rot = axis.to_track_quat('Z', 'Y').to_matrix()
    else:
        rot = axis.to_track_quat('Z', 'X').to_matrix()
    radial1 = rot @ Vector((1, 0, 0))
    radial2 = rot @ Vector((0, 1, 0))

    ring_verts = []
    for r, h in profile:
        ring = []
        center_h = origin + axis * h
        if r <= 1e-6:
            v = bm.verts.new(center_h)
            ring = [v] * segs
        else:
            for i in range(segs):
                theta = 2.0 * math.pi * i / segs
                pos = center_h + (radial1 * math.cos(theta) + radial2 * math.sin(theta)) * r
                ring.append(bm.verts.new(pos))
        ring_verts.append(ring)

    for j in range(len(profile) - 1):
        r1, _ = profile[j]
        r2, _ = profile[j + 1]
        ring1 = ring_verts[j]
        ring2 = ring_verts[j + 1]

        if r1 <= 1e-6 and r2 <= 1e-6:
            continue
        elif r1 <= 1e-6:
            v_center = ring1[0]
            for i in range(segs):
                i_next = (i + 1) % segs
                bm.faces.new([v_center, ring2[i], ring2[i_next]])
        elif r2 <= 1e-6:
            v_center = ring2[0]
            for i in range(segs):
                i_next = (i + 1) % segs
                bm.faces.new([ring1[i], ring1[i_next], v_center])
        else:
            for i in range(segs):
                i_next = (i + 1) % segs
                bm.faces.new([ring1[i], ring1[i_next], ring2[i_next], ring2[i]])


def add_cylinder(bm, p1, p2, r1, r2=None, segs=16):
    if r2 is None:
        r2 = r1
    v = Vector(p2) - Vector(p1)
    height = v.length
    if height < 1e-6:
        return
    axis = v.normalized()
    up = Vector((0, 0, 1))
    if abs(axis.dot(up)) > 0.99:
        rot = axis.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    else:
        rot = axis.to_track_quat('Z', 'X').to_matrix().to_4x4()
    mid = (Vector(p1) + Vector(p2)) * 0.5
    mat = Matrix.Translation(mid) @ rot

    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segs,
        radius1=r1,
        radius2=r2,
        depth=height,
        matrix=mat
    )


def add_box(bm, center, size):
    mat = Matrix.Translation(Vector(center)) @ Matrix.Diagonal(Vector(size)).to_4x4()
    bmesh.ops.create_cube(bm, size=1.0, matrix=mat)


def clean_bmesh_islands(bm, min_size_ratio=0.06):
    """
    Remove tiny disconnected floating geometric islands whose bounding box extent
    is less than min_size_ratio of the total bounding box extent of the mesh.
    """
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    
    if not bm.faces:
        return

    # Compute overall bbox max dimension
    all_coords = [v.co for v in bm.verts]
    min_co = Vector((min(v.x for v in all_coords), min(v.y for v in all_coords), min(v.z for v in all_coords)))
    max_co = Vector((max(v.x for v in all_coords), max(v.y for v in all_coords), max(v.z for v in all_coords)))
    overall_max_dim = max((max_co - min_co).x, (max_co - min_co).y, (max_co - min_co).z)
    
    visited_faces = set()
    components = []
    
    for face in bm.faces:
        if face in visited_faces:
            continue
        comp_faces = []
        queue = [face]
        visited_faces.add(face)
        
        while queue:
            f = queue.pop()
            comp_faces.append(f)
            for edge in f.edges:
                for linked_face in edge.link_faces:
                    if linked_face not in visited_faces:
                        visited_faces.add(linked_face)
                        queue.append(linked_face)
        
        comp_verts = {v for f in comp_faces for v in f.verts}
        c_min = Vector((min(v.co.x for v in comp_verts), min(v.co.y for v in comp_verts), min(v.co.z for v in comp_verts)))
        c_max = Vector((max(v.co.x for v in comp_verts), max(v.co.y for v in comp_verts), max(v.co.z for v in comp_verts)))
        comp_max_dim = max((c_max - c_min).x, (c_max - c_min).y, (c_max - c_min).z)
        components.append((comp_max_dim, comp_faces))
        
    for comp_dim, comp_faces in components:
        if comp_dim < overall_max_dim * min_size_ratio:
            bmesh.ops.delete(bm, geom=comp_faces, context='FACES')
            
    loose_verts = [v for v in bm.verts if not v.link_faces]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')


def mesh_from_bm(name, bm, mat=None, clean_islands=False):
    if clean_islands:
        clean_bmesh_islands(bm)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    if mat:
        if isinstance(mat, list):
            for m in mat:
                ob.data.materials.append(m)
        else:
            ob.data.materials.append(mat)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def build_base(mat_black):
    """
    Weighted table base:
    - BBox: centre (0.000, 0.000, 0.025), extents (0.200, 0.200, 0.050)
    - Cylindrical weighted base: diam 0.20 m, height 0.025 m (z = 0 to 0.025)
    - Clevis bracket at y = 0.020, rising to z = 0.048 m (outer face x = ±0.0175)
    - Rotary switch on front
    """
    bm = bmesh.new()

    # Lathed solid base body (diameter 0.20m, height 0.025m)
    profile = [
        (0.000, 0.000),
        (0.099, 0.000),
        (0.100, 0.002),
        (0.100, 0.014),
        (0.096, 0.016),
        (0.096, 0.021),
        (0.091, 0.023),
        (0.088, 0.025),
        (0.035, 0.025),
        (0.000, 0.025),
    ]
    add_lathe(bm, origin=(0, 0, 0), axis=(0, 0, 1), profile=profile, segs=36)

    # Base bracket mounting plinth at y=0.020, z=0.025 to 0.030
    add_cylinder(bm, (0, 0.020, 0.025), (0, 0.020, 0.030), r1=0.026, r2=0.024, segs=24)

    # Clevis bracket ears at x = ±0.015 (thickness 0.005, spanning x in [-0.0175, -0.0125] and [+0.0125, +0.0175])
    for side in [-1, 1]:
        ear_x = side * 0.015
        add_box(bm, center=(ear_x, 0.020, 0.038), size=(0.005, 0.024, 0.018))
        add_cylinder(bm, (ear_x - 0.0025, 0.020, 0.045), (ear_x + 0.0025, 0.020, 0.045), r1=0.009, segs=16)

    # Rotary power switch on base front (y = -0.055, z = 0.025 to 0.039)
    add_cylinder(bm, (0, -0.055, 0.025), (0, -0.055, 0.032), r1=0.013, r2=0.011, segs=20)
    add_cylinder(bm, (0, -0.055, 0.032), (0, -0.055, 0.037), r1=0.008, r2=0.008, segs=20)
    add_box(bm, center=(0, -0.055, 0.038), size=(0.004, 0.015, 0.003))

    # Base cord relief grommet at the back (y = +0.090 to 0.099, z = 0.008)
    add_cylinder(bm, (0, 0.088, 0.008), (0, 0.099, 0.008), r1=0.006, r2=0.005, segs=16)

    return mesh_from_bm('base', bm, mat_black)


def build_lower_arm(mat_black):
    """
    Lower articulated strut:
    - BBox: centre (0.000, 0.050, 0.200), extents (0.060, 0.100, 0.350)
    - Spans from base pivot (0, 0.020, 0.045) to elbow pivot (0, 0.080, 0.360)
    """
    bm = bmesh.new()

    p_base = Vector((0.0, 0.020, 0.045))
    p_elbow = Vector((0.0, 0.080, 0.360))

    # Vector along lower arm
    v_arm = p_elbow - p_base
    n_back = Vector((0.0, v_arm.z, -v_arm.y)).normalized()  # Points backwards towards +Y

    # Secondary parallel tension struts endpoints
    p_sec_base = p_base + n_back * 0.022
    p_sec_elbow = p_elbow + n_back * 0.022

    # Main twin struts at x = ±0.022
    for side in [-1, 1]:
        sx = side * 0.022
        p1 = Vector((sx, p_base.y, p_base.z))
        p2 = Vector((sx, p_elbow.y, p_elbow.z))
        add_cylinder(bm, p1, p2, r1=0.004, segs=16)

        # Bottom single continuous collar spanning from ±0.016 to ±0.030 (touches base clevis at ±0.0175 with 1.5mm overlap)
        add_cylinder(bm, (sx - side * 0.006, p_base.y, p_base.z), (sx + side * 0.008, p_base.y, p_base.z), r1=0.008, segs=16)

        # Elbow single continuous collar spanning from ±0.016 to ±0.030 (touches upper_arm hub at ±0.017 with 1.0mm overlap)
        add_cylinder(bm, (sx - side * 0.006, p_elbow.y, p_elbow.z), (sx + side * 0.008, p_elbow.y, p_elbow.z), r1=0.008, segs=16)

    # Secondary parallel tension struts at x = ±0.022
    for side in [-1, 1]:
        sx = side * 0.022
        p_s1 = Vector((sx, p_sec_base.y, p_sec_base.z))
        p_s2 = Vector((sx, p_sec_elbow.y, p_sec_elbow.z))
        add_cylinder(bm, p_s1, p_s2, r1=0.0035, segs=12)

    # Cross braces between main struts (3 pieces, span -0.022 to +0.022)
    # Welded directly into both main struts to avoid disconnected islands
    for t in [0.25, 0.50, 0.75]:
        pt = p_base * (1 - t) + p_elbow * t
        add_cylinder(bm, (-0.024, pt.y, pt.z), (0.024, pt.y, pt.z), r1=0.0035, segs=12)

    # Cross braces between secondary struts (2 pieces, span -0.022 to +0.022)
    for t in [0.30, 0.70]:
        pt = p_sec_base * (1 - t) + p_sec_elbow * t
        add_cylinder(bm, (-0.024, pt.y, pt.z), (0.024, pt.y, pt.z), r1=0.0035, segs=10)

    return mesh_from_bm('lower_arm', bm, mat_black)


def build_upper_arm(mat_black):
    """
    Upper articulated strut:
    - BBox: centre (0.000, -0.060, 0.440), extents (0.060, 0.300, 0.200)
    - Spans from elbow pivot (0, 0.080, 0.360) to shade pivot (0, -0.200, 0.500)
    """
    bm = bmesh.new()

    p_elbow = Vector((0.0, 0.080, 0.360))
    p_shade = Vector((0.0, -0.200, 0.500))

    # Vector along upper arm
    v_arm = p_shade - p_elbow  # (0, -0.280, +0.140)
    n_up = Vector((0.0, v_arm.z, -v_arm.y)).normalized()  # Points upwards and backwards

    # Elbow central pivot hub (1 piece, x from -0.017 to +0.017, overlaps lower_arm collar at ±0.016 by 1.0mm)
    add_cylinder(bm, (-0.017, p_elbow.y, p_elbow.z), (0.017, p_elbow.y, p_elbow.z), r1=0.006, segs=16)

    # Twin main struts at x = ±0.011 (2 pieces)
    for side in [-1, 1]:
        sx = side * 0.011
        p1 = Vector((sx, p_elbow.y, p_elbow.z))
        p2 = Vector((sx, p_shade.y, p_shade.z))
        add_cylinder(bm, p1, p2, r1=0.004, segs=16)

        # Tip knuckle hub and thumbscrew as single continuous cylinder (from ±0.005 to ±0.030, overlaps shade hub at ±0.006 by 1.0mm)
        add_cylinder(bm, (sx - side * 0.006, p_shade.y, p_shade.z), (sx + side * 0.019, p_shade.y, p_shade.z), r1=0.006, segs=16)

    # Parallel upper tension rods (2 pieces)
    p_up_elbow = p_elbow + n_up * 0.024
    p_up_shade = p_shade + n_up * 0.024
    for side in [-1, 1]:
        sx = side * 0.011
        p_u1 = Vector((sx, p_up_elbow.y, p_up_elbow.z))
        p_u2 = Vector((sx, p_up_shade.y, p_up_shade.z))
        add_cylinder(bm, p_u1, p_u2, r1=0.0035, segs=12)

        # Linkage brackets connecting upper rod to main strut (4 pieces)
        add_cylinder(bm, Vector((sx, p_elbow.y, p_elbow.z)), p_u1 + n_up * 0.010, r1=0.0035, segs=10)
        add_cylinder(bm, Vector((sx, p_shade.y, p_shade.z)), p_u2 + n_up * 0.010, r1=0.0035, segs=10)

    # Cross braces along main struts (3 pieces)
    for t in [0.25, 0.50, 0.75]:
        pt = p_elbow * (1 - t) + p_shade * t
        add_cylinder(bm, (-0.011, pt.y, pt.z), (0.011, pt.y, pt.z), r1=0.0035, segs=12)

    # Cross braces between upper tension rods (2 pieces)
    for t in [0.30, 0.70]:
        pt = p_up_elbow * (1 - t) + p_up_shade * t
        add_cylinder(bm, (-0.011, pt.y, pt.z), (0.011, pt.y, pt.z), r1=0.0035, segs=10)

    # Rear bracket extension on elbow (2 pieces)
    for side in [-1, 1]:
        sx = side * 0.011
        add_cylinder(bm, Vector((sx, p_elbow.y - 0.010, p_elbow.z + 0.005)), Vector((sx, 0.090, 0.340)), r1=0.004, segs=10)

    return mesh_from_bm('upper_arm', bm, mat_black)


def build_lamp_shade(mat_black):
    """
    Lamp head and shade:
    - BBox: centre (0.000, -0.260, 0.450), extents (0.160, 0.180, 0.180)
    - Mounts at shade pivot (0, -0.200, 0.500)
    - Knuckle hub at x in [-0.006, +0.006]
    - Conical flared metal shade with rolled rim and inner cavity
    - Light bulb inside
    """
    bm = bmesh.new()

    p_pivot = Vector((0.0, -0.200, 0.500))

    # Knuckle fork / pivot hub around pivot pin (width 0.012, x from -0.006 to +0.006)
    add_cylinder(bm, (-0.006, p_pivot.y, p_pivot.z), (0.006, p_pivot.y, p_pivot.z), r1=0.005, segs=16)

    # Shade orientation axis: points forward and downward towards -Y and -Z
    shade_dir = Vector((0.0, -0.80, -0.60)).normalized()

    # Shade body profile lathed along shade_dir from p_pivot:
    profile = [
        (0.005, 0.000),   # pivot neck
        (0.005, 0.018),
        (0.017, 0.020),   # socket top cap
        (0.019, 0.035),
        (0.023, 0.036),   # vent ring
        (0.023, 0.042),
        (0.020, 0.044),   # cone start
        (0.044, 0.082),   # cone mid
        (0.078, 0.142),   # flared outer cone
        (0.079, 0.146),   # outer rim lip
        (0.076, 0.143),   # inner rim lip
        (0.041, 0.085),   # inner cone mid
        (0.017, 0.048),   # inner socket base
        (0.000, 0.048),   # closed inner socket center
    ]
    add_lathe(bm, origin=p_pivot, axis=shade_dir, profile=profile, segs=36)

    # Light bulb inside the shade (lathed dome)
    p_bulb_origin = p_pivot + shade_dir * 0.050
    bulb_profile = [
        (0.000, 0.000),
        (0.014, 0.002),
        (0.022, 0.018),
        (0.025, 0.035),
        (0.022, 0.048),
        (0.012, 0.056),
        (0.000, 0.058),
    ]
    add_lathe(bm, origin=p_bulb_origin, axis=shade_dir, profile=bulb_profile, segs=24)

    # Back handle / fin reaching backward from socket top
    p_handle_start = p_pivot + shade_dir * 0.020
    p_handle_end = p_handle_start - shade_dir * 0.035
    add_cylinder(bm, p_handle_start, p_handle_end, r1=0.004, r2=0.004, segs=12)
    add_cylinder(bm, p_handle_end, p_handle_end - shade_dir * 0.008, r1=0.007, r2=0.007, segs=16)

    # Rotary switch key on socket cap
    p_switch_base = p_pivot + shade_dir * 0.030
    add_cylinder(bm, p_switch_base + Vector((-0.016, 0, 0)), p_switch_base + Vector((0.016, 0, 0)), r1=0.0025, segs=12)
    add_cylinder(bm, p_switch_base + Vector((-0.019, 0, 0)), p_switch_base + Vector((-0.016, 0, 0)), r1=0.006, segs=12)

    return mesh_from_bm('lamp_shade', bm, mat_black)


def main():
    clear_scene()

    # Matte black powder-coated steel material
    mat_black = create_material("MatteBlackSteel", (0.05, 0.05, 0.05, 1.0), metallic=0.2, roughness=0.5)

    # Build links
    base = build_base(mat_black)
    lower_arm = build_lower_arm(mat_black)
    upper_arm = build_upper_arm(mat_black)
    lamp_shade = build_lamp_shade(mat_black)

    # Ensure all objects exist and are named properly
    for n in ['base', 'lower_arm', 'upper_arm', 'lamp_shade']:
        assert n in bpy.data.objects, f"Missing {n}"


if __name__ == '__main__':
    main()
