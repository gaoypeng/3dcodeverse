```python
import bpy
import math
from mathutils import Vector


def create_material(name, base_color, roughness):
    material = bpy.data.materials.new(name=name)
    material.use_nodes = True

    principled = material.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (*base_color, 1.0)
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Metallic"].default_value = 0.0

    return material


def create_seat(material):
    segments = 64

    # Rounded 30 cm diameter seat, with its top exactly at 45 cm.
    profile = [
        (0.136, 0.415),
        (0.142, 0.417),
        (0.147, 0.422),
        (0.150, 0.429),
        (0.150, 0.441),
        (0.148, 0.446),
        (0.142, 0.450),
    ]

    vertices = []
    faces = []

    for radius, z in profile:
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            vertices.append((
                radius * math.cos(angle),
                radius * math.sin(angle),
                z,
            ))

    bottom_center = len(vertices)
    vertices.append((0.0, 0.0, profile[0][1]))

    top_center = len(vertices)
    vertices.append((0.0, 0.0, profile[-1][1]))

    for ring in range(len(profile) - 1):
        lower = ring * segments
        upper = (ring + 1) * segments

        for i in range(segments):
            j = (i + 1) % segments
            faces.append((
                lower + i,
                lower + j,
                upper + j,
                upper + i,
            ))

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((bottom_center, j, i))

        top_ring = (len(profile) - 1) * segments
        faces.append((top_center, top_ring + i, top_ring + j))

    mesh = bpy.data.meshes.new("SeatMesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()

    obj = bpy.data.objects.new("Seat", mesh)
    bpy.context.scene.collection.objects.link(obj)

    side_face_count = (len(profile) - 1) * segments
    for polygon in mesh.polygons[:side_face_count]:
        polygon.use_smooth = True

    return obj


def create_splayed_leg(name, lower_center, upper_center, lower_radius,
                       upper_radius, material, segments=24):
    lower_center = Vector(lower_center)
    upper_center = Vector(upper_center)

    axis = upper_center - lower_center
    axis_direction = axis.normalized()

    reference = Vector((0.0, 0.0, 1.0))
    basis_u = axis_direction.cross(reference).normalized()
    basis_v = axis_direction.cross(basis_u).normalized()

    vertices = []

    # The ring vertices are adjusted along the leg axis so both cut faces are
    # exactly horizontal. Every lower vertex therefore touches z=0.
    for center, radius in (
        (lower_center, lower_radius),
        (upper_center, upper_radius),
    ):
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            radial = radius * (
                math.cos(angle) * basis_u +
                math.sin(angle) * basis_v
            )
            axial_adjustment = -radial.z / axis_direction.z
            point = center + radial + axis_direction * axial_adjustment
            vertices.append(tuple(point))

    bottom_center_index = len(vertices)
    vertices.append(tuple(lower_center))

    top_center_index = len(vertices)
    vertices.append(tuple(upper_center))

    faces = []

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((i, j, segments + j, segments + i))

    for i in range(segments):
        j = (i + 1) % segments
        faces.append((bottom_center_index, j, i))
        faces.append((
            top_center_index,
            segments + i,
            segments + j,
        ))

    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)

    for polygon in mesh.polygons[:segments]:
        polygon.use_smooth = True

    return obj


def main():
    seat_material = create_material(
        "SeatWood",
        (0.36, 0.155, 0.055),
        0.38,
    )
    leg_material = create_material(
        "LegWood",
        (0.29, 0.105, 0.032),
        0.43,
    )

    create_seat(seat_material)

    foot_radius = 0.172
    attachment_radius = 0.078
    attachment_height = 0.423

    leg_specs = (
        ("LegFront", -90.0),
        ("LegRearRight", 30.0),
        ("LegRearLeft", 150.0),
    )

    for name, angle_degrees in leg_specs:
        angle = math.radians(angle_degrees)
        direction = Vector((math.cos(angle), math.sin(angle), 0.0))

        lower = direction * foot_radius
        lower.z = 0.0

        upper = direction * attachment_radius
        upper.z = attachment_height

        create_splayed_leg(
            name=name,
            lower_center=lower,
            upper_center=upper,
            lower_radius=0.014,
            upper_radius=0.019,
            material=leg_material,
        )


main()
```