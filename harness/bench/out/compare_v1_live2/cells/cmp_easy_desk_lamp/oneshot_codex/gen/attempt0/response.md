```python
import bpy
import math
from mathutils import Vector


def make_material(name, color, metallic, roughness):
    material = bpy.data.materials.new(name=name)
    material.diffuse_color = color
    material.use_nodes = True

    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = color
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = roughness
    return material


def create_mesh_object(name, vertices, faces, materials, material_indices=None,
                       smooth_faces=None):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.clear()

    for material in materials:
        mesh.materials.append(material)

    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)

    if material_indices is not None:
        for polygon, index in zip(mesh.polygons, material_indices):
            polygon.material_index = index

    if smooth_faces is not None:
        for polygon, smooth in zip(mesh.polygons, smooth_faces):
            polygon.use_smooth = smooth

    return obj


def create_rounded_base(material, segments=64):
    profile = [
        (0.140, 0.000),
        (0.169, 0.005),
        (0.183, 0.016),
        (0.187, 0.031),
        (0.181, 0.045),
        (0.163, 0.057),
        (0.128, 0.065),
        (0.070, 0.069),
    ]

    vertices = []
    for radius, z in profile:
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            vertices.append((
                radius * math.cos(angle),
                radius * math.sin(angle),
                z,
            ))

    bottom_center = len(vertices)
    vertices.append((0.0, 0.0, 0.0))
    top_center = len(vertices)
    vertices.append((0.0, 0.0, 0.070))

    faces = []
    smooth = []

    for ring in range(len(profile) - 1):
        lower = ring * segments
        upper = (ring + 1) * segments
        for i in range(segments):
            j = (i + 1) % segments
            faces.append((lower + i, lower + j, upper + j, upper + i))
            smooth.append(True)

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((bottom_center, j, i))
        smooth.append(False)

    last_ring = (len(profile) - 1) * segments
    for i in range(segments):
        j = (i + 1) % segments
        faces.append((top_center, last_ring + i, last_ring + j))
        smooth.append(False)

    return create_mesh_object(
        "RoundBase",
        vertices,
        faces,
        [material],
        smooth_faces=smooth,
    )


def create_cylinder_between(name, start, end, radius, material, segments=32):
    start = Vector(start)
    end = Vector(end)
    axis = end - start
    direction = axis.normalized()

    reference = Vector((0.0, 0.0, 1.0))
    if abs(direction.dot(reference)) > 0.95:
        reference = Vector((0.0, 1.0, 0.0))

    basis_u = direction.cross(reference).normalized()
    basis_v = direction.cross(basis_u).normalized()

    vertices = []
    for point in (start, end):
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            offset = radius * (
                basis_u * math.cos(angle) + basis_v * math.sin(angle)
            )
            vertex = point + offset
            vertices.append(tuple(vertex))

    start_center = len(vertices)
    vertices.append(tuple(start))
    end_center = len(vertices)
    vertices.append(tuple(end))

    faces = []
    smooth = []

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((i, j, segments + j, segments + i))
        smooth.append(True)

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((start_center, j, i))
        smooth.append(False)
        faces.append((end_center, segments + i, segments + j))
        smooth.append(False)

    return create_mesh_object(
        name,
        vertices,
        faces,
        [material],
        smooth_faces=smooth,
    )


def create_shade(outer_material, inner_material, segments=64):
    center_x = 0.0
    center_y = -0.105
    top_z = 0.530
    bottom_z = 0.335

    outer_top_radius = 0.044
    outer_bottom_radius = 0.137
    inner_top_radius = 0.016
    inner_bottom_radius = 0.130

    vertices = []

    rings = [
        (outer_bottom_radius, bottom_z),
        (outer_top_radius, top_z),
        (inner_bottom_radius, bottom_z + 0.004),
        (inner_top_radius, top_z - 0.004),
    ]

    for radius, z in rings:
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            vertices.append((
                center_x + radius * math.cos(angle),
                center_y + radius * math.sin(angle),
                z,
            ))

    outer_bottom = 0
    outer_top = segments
    inner_bottom = segments * 2
    inner_top = segments * 3

    faces = []
    material_indices = []
    smooth = []

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((
            outer_bottom + i,
            outer_bottom + j,
            outer_top + j,
            outer_top + i,
        ))
        material_indices.append(0)
        smooth.append(True)

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((
            inner_bottom + i,
            inner_top + i,
            inner_top + j,
            inner_bottom + j,
        ))
        material_indices.append(1)
        smooth.append(True)

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((
            outer_bottom + i,
            inner_bottom + i,
            inner_bottom + j,
            outer_bottom + j,
        ))
        material_indices.append(0)
        smooth.append(False)

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((
            outer_top + i,
            outer_top + j,
            inner_top + j,
            inner_top + i,
        ))
        material_indices.append(0)
        smooth.append(False)

    return create_mesh_object(
        "ConicalShade",
        vertices,
        faces,
        [outer_material, inner_material],
        material_indices=material_indices,
        smooth_faces=smooth,
    )


def main():
    dark_green = make_material(
        "DarkGreenEnamel",
        (0.055, 0.145, 0.105, 1.0),
        metallic=0.72,
        roughness=0.24,
    )
    arm_metal = make_material(
        "ArmMetal",
        (0.105, 0.120, 0.105, 1.0),
        metallic=0.88,
        roughness=0.20,
    )
    shade_inner = make_material(
        "ShadeReflectiveInterior",
        (0.72, 0.68, 0.52, 1.0),
        metallic=0.78,
        roughness=0.25,
    )

    create_rounded_base(dark_green)

    create_cylinder_between(
        "AngledArm",
        (0.0, 0.038, 0.061),
        (0.0, -0.105, 0.540),
        0.0125,
        arm_metal,
        segments=32,
    )

    create_shade(dark_green, shade_inner)


main()
```