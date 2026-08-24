"""Small deterministic mesh helpers shared by the telescope part builders."""
import math

import bpy
import bmesh
from mathutils import Matrix, Vector


def material(name, rgb, roughness=0.35, metallic=1.0, alpha=1.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        bsdf.inputs["Transmission Weight"].default_value = 0.35
        mat.surface_render_method = 'BLENDED'
    return mat


def _frame(direction):
    axis = Vector(direction).normalized()
    side = Vector((1.0, 0.0, 0.0))
    if abs(axis.dot(side)) > 0.92:
        side = Vector((0.0, 1.0, 0.0))
    e1 = (side - axis * axis.dot(side)).normalized()
    e2 = axis.cross(e1).normalized()
    return axis, e1, e2


def add_cone(bm, center, direction, depth, radius1, radius2, mat_index=0, segments=32):
    before = set(bm.faces)
    result = bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius1, radius2=radius2, depth=depth,
    )
    axis = Vector(direction).normalized()
    rot = Vector((0.0, 0.0, 1.0)).rotation_difference(axis).to_matrix().to_4x4()
    transform = Matrix.Translation(Vector(center)) @ rot
    bmesh.ops.transform(bm, matrix=transform, verts=result["verts"])
    for face in set(bm.faces) - before:
        face.material_index = mat_index


def add_box_between(bm, p0, p1, width, thickness, mat_index=0):
    p0, p1 = Vector(p0), Vector(p1)
    axis = p1 - p0
    before = set(bm.faces)
    result = bmesh.ops.create_cube(bm, size=1.0)
    rot = Vector((0.0, 0.0, 1.0)).rotation_difference(axis.normalized()).to_matrix().to_4x4()
    transform = Matrix.Translation((p0 + p1) * 0.5) @ rot @ Matrix.Diagonal((width, thickness, axis.length, 1.0))
    bmesh.ops.transform(bm, matrix=transform, verts=result["verts"])
    for face in set(bm.faces) - before:
        face.material_index = mat_index


def add_tapered_rect(bm, p0, p1, size0, size1, mat_index=0):
    p0, p1 = Vector(p0), Vector(p1)
    axis, e1, e2 = _frame(p1 - p0)
    verts = []
    for p, (w, t) in ((p0, size0), (p1, size1)):
        verts.extend([
            bm.verts.new(p - e1 * w / 2 - e2 * t / 2),
            bm.verts.new(p + e1 * w / 2 - e2 * t / 2),
            bm.verts.new(p + e1 * w / 2 + e2 * t / 2),
            bm.verts.new(p - e1 * w / 2 + e2 * t / 2),
        ])
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    for indices in faces:
        bm.faces.new(tuple(verts[i] for i in indices)).material_index = mat_index


def add_lathe(bm, center, direction, profile, mat_index=0, segments=48, ribbed=False):
    """Closed solid of revolution. Profile is [(axial_offset, radius), ...]."""
    axis, e1, e2 = _frame(direction)
    center = Vector(center)
    rings = []
    for s, radius in profile:
        ring = []
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            rr = radius * (1.0 + (0.055 if ribbed and i % 2 == 0 else 0.0))
            ring.append(bm.verts.new(center + axis * s + e1 * (rr * math.cos(angle)) + e2 * (rr * math.sin(angle))))
        rings.append(ring)
    for a, b in zip(rings, rings[1:]):
        for i in range(segments):
            bm.faces.new((a[i], a[(i + 1) % segments], b[(i + 1) % segments], b[i])).material_index = mat_index
    bm.faces.new(tuple(reversed(rings[0]))).material_index = mat_index
    bm.faces.new(tuple(rings[-1])).material_index = mat_index


def add_ellipsoid(bm, center, axis_direction, radii, mat_index=0, segments=32, rings=16):
    before = set(bm.faces)
    result = bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=1.0)
    axis, e1, e2 = _frame(axis_direction)
    frame = Matrix(((e1.x, e2.x, axis.x, center[0]),
                    (e1.y, e2.y, axis.y, center[1]),
                    (e1.z, e2.z, axis.z, center[2]),
                    (0.0, 0.0, 0.0, 1.0)))
    transform = frame @ Matrix.Diagonal((radii[0], radii[1], radii[2], 1.0))
    bmesh.ops.transform(bm, matrix=transform, verts=result["verts"])
    for face in set(bm.faces) - before:
        face.material_index = mat_index


def finish(name, bm, materials, bevel=0.0008, smooth=True):
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.00005)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for mat in materials:
        obj.data.materials.append(mat)
    if smooth:
        for poly in mesh.polygons:
            poly.use_smooth = True
    if bevel > 0.0:
        mod = obj.modifiers.new("EdgeSoftening", 'BEVEL')
        mod.width = bevel
        mod.segments = 2
        mod.limit_method = 'ANGLE'
    return obj
