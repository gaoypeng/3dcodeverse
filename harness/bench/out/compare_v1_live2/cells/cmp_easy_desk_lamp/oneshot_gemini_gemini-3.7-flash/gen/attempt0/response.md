```python
"""
Desk Lamp Model
Mid-century modern style desk lamp with a heavy round base,
a single angled arm leaning forward, and a conical metal shade opening downward.
"""

import math
import bmesh
import bpy
from mathutils import Matrix, Vector


def create_material(name, base_color, metallic=0.0, roughness=0.3, emission_color=None, emission_strength=0.0):
    """Creates a Principled BSDF material."""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()
    node_out = nodes.new(type="ShaderNodeOutputMaterial")
    node_bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")

    # Set Principled BSDF socket values
    node_bsdf.inputs["Base Color"].default_value = base_color
    node_bsdf.inputs["Metallic"].default_value = metallic
    node_bsdf.inputs["Roughness"].default_value = roughness

    # Emission handling for light bulb
    if emission_color is not None and emission_strength > 0:
        if "Emission Color" in node_bsdf.inputs:
            node_bsdf.inputs["Emission Color"].default_value = emission_color
            node_bsdf.inputs["Emission Strength"].default_value = emission_strength
        elif "Emission" in node_bsdf.inputs:
            node_bsdf.inputs["Emission"].default_value = emission_color

    links.new(node_bsdf.outputs["BSDF"], node_out.inputs["Surface"])
    return mat


def set_smooth(mesh):
    """Sets smooth shading on all faces of a mesh."""
    for poly in mesh.polygons:
        poly.use_smooth = True


def make_base(materials):
    """Creates the heavy circular weighted base with a bottom felt pad."""
    # 1. Base Body
    bm = bmesh.new()
    # Create stepped profile for heavy cast look
    segments = 48
    r_outer = 0.095
    r_top = 0.088
    h_total = 0.022
    h_step = 0.005

    # Bottom ring
    v_bot_center = bm.verts.new((0, 0, 0.002))
    v_bot_rim = [
        bm.verts.new(
            (
                r_outer * math.cos(i * 2 * math.pi / segments),
                r_outer * math.sin(i * 2 * math.pi / segments),
                0.002,
            )
        )
        for i in range(segments)
    ]
    for i in range(segments):
        bm.faces.new(
            (
                v_bot_center,
                v_bot_rim[(i + 1) % segments],
                v_bot_rim[i],
            )
        )

    # Lower chamfer
    v_mid_rim = [
        bm.verts.new(
            (
                r_outer * math.cos(i * 2 * math.pi / segments),
                r_outer * math.sin(i * 2 * math.pi / segments),
                h_step,
            )
        )
        for i in range(segments)
    ]
    for i in range(segments):
        bm.faces.new(
            (
                v_bot_rim[i],
                v_bot_rim[(i + 1) % segments],
                v_mid_rim[(i + 1) % segments],
                v_mid_rim[i],
            )
        )

    # Top outer rim
    v_top_outer = [
        bm.verts.new(
            (
                r_outer * math.cos(i * 2 * math.pi / segments),
                r_outer * math.sin(i * 2 * math.pi / segments),
                h_total - 0.003,
            )
        )
        for i in range(segments)
    ]
    for i in range(segments):
        bm.faces.new(
            (
                v_mid_rim[i],
                v_mid_rim[(i + 1) % segments],
                v_top_outer[(i + 1) % segments],
                v_top_outer[i],
            )
        )

    # Top chamfer rim
    v_top_inner = [
        bm.verts.new(
            (
                r_top * math.cos(i * 2 * math.pi / segments),
                r_top * math.sin(i * 2 * math.pi / segments),
                h_total,
            )
        )
        for i in range(segments)
    ]
    for i in range(segments):
        bm.faces.new(
            (
                v_top_outer[i],
                v_top_outer[(i + 1) % segments],
                v_top_inner[(i + 1) % segments],
                v_top_inner[i],
            )
        )

    # Top flat cap
    v_top_center = bm.verts.new((0, 0, h_total))
    for i in range(segments):
        bm.faces.new(
            (
                v_top_center,
                v_top_inner[i],
                v_top_inner[(i + 1) % segments],
            )
        )

    bm.normal_update()
    mesh = bpy.data.meshes.new("LampBase")
    bm.to_mesh(mesh)
    bm.free()
    set_smooth(mesh)

    base_obj = bpy.data.objects.new("LampBase", mesh)
    base_obj.data.materials.append(materials["matte_black"])
    bpy.context.scene.collection.objects.link(base_obj)

    # 2. Bottom Felt Pad (visible edge resting on z=0)
    bm_pad = bmesh.new()
    bmesh.ops.create_cylinder(
        bm_pad,
        cap_ends=True,
        radius=0.093,
        depth=0.002,
        segments=48,
        matrix=Matrix.Translation((0, 0, 0.001)),
    )
    mesh_pad = bpy.data.meshes.new("BasePad")
    bm_pad.to_mesh(mesh_pad)
    bm_pad.free()
    set_smooth(mesh_pad)

    pad_obj = bpy.data.objects.new("BasePad", mesh_pad)
    pad_obj.data.materials.append(materials["rubber"])
    bpy.context.scene.collection.objects.link(pad_obj)

    return h_total


def make_switch(materials, z_pos):
    """Creates a brass push/rotary switch button on the base."""
    bm = bmesh.new()
    # Switch bezel collar
    bmesh.ops.create_cylinder(
        bm,
        cap_ends=True,
        radius=0.006,
        depth=0.004,
        segments=24,
        matrix=Matrix.Translation((0, -0.052, z_pos + 0.002)),
    )
    # Switch button toggle
    bmesh.ops.create_cylinder(
        bm,
        cap_ends=True,
        radius=0.0035,
        depth=0.008,
        segments=24,
        matrix=Matrix.Translation((0, -0.052, z_pos + 0.006)),
    )
    bm.normal_update()
    mesh = bpy.data.meshes.new("BaseSwitch")
    bm.to_mesh(mesh)
    bm.free()
    set_smooth(mesh)

    obj = bpy.data.objects.new("BaseSwitch", mesh)
    obj.data.materials.append(materials["brass"])
    bpy.context.scene.collection.objects.link(obj)


def make_arm_and_mount(materials, base_top_z):
    """Creates the base pivot mount, the single forward-angled arm, and the top joint."""
    arm_base_pos = Vector((0.0, 0.035, base_top_z))

    # 1. Base Arm Mount (Brass Collar & Bracket)
    bm_mount = bmesh.new()
    # Bottom mount cylinder
    bmesh.ops.create_cylinder(
        bm_mount,
        cap_ends=True,
        radius=0.014,
        depth=0.012,
        segments=32,
        matrix=Matrix.Translation((0.0, 0.035, base_top_z + 0.006)),
    )
    # Pivot fork / bracket
    bmesh.ops.create_cube(
        bm_mount,
        size=1.0,
        matrix=Matrix.Translation((0.0, 0.035, base_top_z + 0.017))
        @ Matrix.Scale(0.016, 4, (1, 0, 0))
        @ Matrix.Scale(0.012, 4, (0, 1, 0))
        @ Matrix.Scale(0.014, 4, (0, 0, 1)),
    )
    # Pivot horizontal pin
    bmesh.ops.create_cylinder(
        bm_mount,
        cap_ends=True,
        radius=0.004,
        depth=0.022,
        segments=16,
        matrix=Matrix.Translation((0.0, 0.035, base_top_z + 0.018))
        @ Matrix.Rotation(math.radians(90), 4, "Y"),
    )
    bm_mount.normal_update()
    mesh_mount = bpy.data.meshes.new("ArmBaseMount")
    bm_mount.to_mesh(mesh_mount)
    bm_mount.free()
    set_smooth(mesh_mount)

    mount_obj = bpy.data.objects.new("ArmBaseMount", mesh_mount)
    mount_obj.data.materials.append(materials["brass"])
    bpy.context.scene.collection.objects.link(mount_obj)

    # 2. Main Straight Arm (Leaning forward into -Y)
    # Pivot origin at base mount
    pivot_z = base_top_z + 0.018
    arm_start = Vector((0.0, 0.035, pivot_z))
    arm_length = 0.44
    tilt_angle = math.radians(24.0)  # Leaning forward towards -Y

    # Direction vector: leans forward (-Y) and up (+Z)
    dir_vec = Vector((0.0, -math.sin(tilt_angle), math.cos(tilt_angle)))
    arm_end = arm_start + dir_vec * arm_length
    arm_center = (arm_start + arm_end) * 0.5

    # Rotation matrix aligning cylinder (initially along Z) with dir_vec
    rot_arm = Matrix.Rotation(-tilt_angle, 4, "X")

    bm_arm = bmesh.new()
    bmesh.ops.create_cylinder(
        bm_arm,
        cap_ends=True,
        radius=0.006,
        depth=arm_length,
        segments=28,
        matrix=Matrix.Translation(arm_center) @ rot_arm,
    )
    bm_arm.normal_update()
    mesh_arm = bpy.data.meshes.new("LampArm")
    bm_arm.to_mesh(mesh_arm)
    bm_arm.free()
    set_smooth(mesh_arm)

    arm_obj = bpy.data.objects.new("LampArm", mesh_arm)
    arm_obj.data.materials.append(materials["matte_black"])
    bpy.context.scene.collection.objects.link(arm_obj)

    # 3. Top Joint (Brass Swivel Fitting)
    bm_joint = bmesh.new()
    # Spherical / cylindrical knuckle at top of the arm
    bmesh.ops.create_uvsphere(
        bm_joint,
        u_segments=24,
        v_segments=16,
        radius=0.011,
        matrix=Matrix.Translation(arm_end),
    )
    # Locking screw / wing knob on side (+X)
    bmesh.ops.create_cylinder(
        bm_joint,
        cap_ends=True,
        radius=0.0035,
        depth=0.028,
        segments=16,
        matrix=Matrix.Translation(arm_end) @ Matrix.Rotation(math.radians(90), 4, "Y"),
    )
    bmesh.ops.create_cylinder(
        bm_joint,
        cap_ends=True,
        radius=0.007,
        depth=0.004,
        segments=16,
        matrix=Matrix.Translation(arm_end + Vector((0.014, 0, 0)))
        @ Matrix.Rotation(math.radians(90), 4, "Y"),
    )
    bm_joint.normal_update()
    mesh_joint = bpy.data.meshes.new("UpperJoint")
    bm_joint.to_mesh(mesh_joint)
    bm_joint.free()
    set_smooth(mesh_joint)

    joint_obj = bpy.data.objects.new("UpperJoint", mesh_joint)
    joint_obj.data.materials.append(materials["brass"])
    bpy.context.scene.collection.objects.link(joint_obj)

    return arm_end


def make_shade_and_bulb(materials, top_pos):
    """Creates the conical metal shade opening downwards and the inner light bulb."""
    # Shade orientation: tilted slightly forward from straight down (e.g. 15 degrees)
    shade_tilt = math.radians(12.0)
    rot_shade = Matrix.Rotation(shade_tilt, 4, "X")

    # Downward axis vector for shade
    shade_dir = Vector((0.0, -math.sin(shade_tilt), -math.cos(shade_tilt)))

    # 1. Brass Shade Socket / Neck attached to joint
    socket_length = 0.025
    socket_pos = top_pos + shade_dir * (socket_length * 0.5)

    bm_socket = bmesh.new()
    bmesh.ops.create_cylinder(
        bm_socket,
        cap_ends=True,
        radius=0.010,
        depth=socket_length,
        segments=24,
        matrix=Matrix.Translation(socket_pos) @ rot_shade,
    )
    bm_socket.normal_update()
    mesh_socket = bpy.data.meshes.new("ShadeSocket")
    bm_socket.to_mesh(mesh_socket)
    bm_socket.free()
    set_smooth(mesh_socket)

    socket_obj = bpy.data.objects.new("ShadeSocket", mesh_socket)
    socket_obj.data.materials.append(materials["brass"])
    bpy.context.scene.collection.objects.link(socket_obj)

    # 2. Conical Shade
    # Top of cone attaches at socket end
    shade_top_center = top_pos + shade_dir * socket_length
    shade_height = 0.135
    r_top = 0.028
    r_bottom = 0.092
    segments = 48

    # Local coordinates along shade axis (Z from 0 to -shade_height)
    bm_shade = bmesh.new()

    # Outer cone vertices
    v_outer_top = [
        bm_shade.verts.new(
            (
                r_top * math.cos(i * 2 * math.pi / segments),
                r_top * math.sin(i * 2 * math.pi / segments),
                0.0,
            )
        )
        for i in range(segments)
    ]
    v_outer_bot = [
        bm_shade.verts.new(
            (
                r_bottom * math.cos(i * 2 * math.pi / segments),
                r_bottom * math.sin(i * 2 * math.pi / segments),
                -shade_height,
            )
        )
        for i in range(segments)
    ]

    # Inner cone vertices (for realistic thickness and white interior)
    r_top_in = r_top - 0.0025
    r_bot_in = r_bottom - 0.0025
    v_inner_top = [
        bm_shade.verts.new(
            (
                r_top_in * math.cos(i * 2 * math.pi / segments),
                r_top_in * math.sin(i * 2 * math.pi / segments),
                -0.003,
            )
        )
        for i in range(segments)
    ]
    v_inner_bot = [
        bm_shade.verts.new(
            (
                r_bot_in * math.cos(i * 2 * math.pi / segments),
                r_bot_in * math.sin(i * 2 * math.pi / segments),
                -shade_height + 0.001,
            )
        )
        for i in range(segments)
    ]

    # Outer wall faces
    for i in range(segments):
        bm_shade.faces.new(
            (
                v_outer_top[i],
                v_outer_top[(i + 1) % segments],
                v_outer_bot[(i + 1) % segments],
                v_outer_bot[i],
            )
        )

    # Bottom rim lip faces (joining outer and inner)
    for i in range(segments):
        bm_shade.faces.new(
            (
                v_outer_bot[i],
                v_outer_bot[(i + 1) % segments],
                v_inner_bot[(i + 1) % segments],
                v_inner_bot[i],
            )
        )

    # Inner wall faces
    for i in range(segments):
        bm_shade.faces.new(
            (
                v_inner_bot[i],
                v_inner_bot[(i + 1) % segments],
                v_inner_top[(i + 1) % segments],
                v_inner_top[i],
            )
        )

    # Top cap rim faces
    for i in range(segments):
        bm_shade.faces.new(
            (
                v_inner_top[i],
                v_inner_top[(i + 1) % segments],
                v_outer_top[(i + 1) % segments],
                v_outer_top[i],
            )
        )

    # Top inner flat cap
    v_in_cap_center = bm_shade.verts.new((0, 0, -0.003))
    for i in range(segments):
        bm_shade.faces.new(
            (
                v_in_cap_center,
                v_inner_top[(i + 1) % segments],
                v_inner_top[i],
            )
        )

    # Top outer flat cap
    v_out_cap_center = bm_shade.verts.new((0, 0, 0.0))
    for i in range(segments):
        bm_shade.faces.new(
            (
                v_out_cap_center,
                v_outer_top[i],
                v_outer_top[(i + 1) % segments],
            )
        )

    # Transform shade into world orientation
    world_transform = Matrix.Translation(shade_top_center) @ rot_shade
    bm_shade.transform(world_transform)
    bm_shade.normal_update()

    mesh_shade = bpy.data.meshes.new("ConicalShade")
    bm_shade.to_mesh(mesh_shade)
    bm_shade.free()
    set_smooth(mesh_shade)

    shade_obj = bpy.data.objects.new("ConicalShade", mesh_shade)
    shade_obj.data.materials.append(materials["matte_black"])
    bpy.context.scene.collection.objects.link(shade_obj)

    # 3. Light Bulb tucked inside shade
    bulb_pos = shade_top_center + shade_dir * 0.055
    bm_bulb = bmesh.new()
    bmesh.ops.create_uvsphere(
        bm_bulb,
        u_segments=24,
        v_segments=16,
        radius=0.022,
        matrix=Matrix.Translation(bulb_pos)
        @ Matrix.Scale(1.0, 4, (1, 0, 0))
        @ Matrix.Scale(1.0, 4, (0, 1, 0))
        @ Matrix.Scale(1.25, 4, (0, 0, 1))
        @ rot_shade,
    )
    bm_bulb.normal_update()
    mesh_bulb = bpy.data.meshes.new("LightBulb")
    bm_bulb.to_mesh(mesh_bulb)
    bm_bulb.free()
    set_smooth(mesh_bulb)

    bulb_obj = bpy.data.objects.new("LightBulb", mesh_bulb)
    bulb_obj.data.materials.append(materials["bulb_emission"])
    bpy.context.scene.collection.objects.link(bulb_obj)


def main():
    # Setup high quality materials
    materials = {
        "matte_black": create_material(
            "MatteBlackMetal",
            base_color=(0.04, 0.04, 0.045, 1.0),
            metallic=0.15,
            roughness=0.35,
        ),
        "brass": create_material(
            "BrushedBrass",
            base_color=(0.85, 0.65, 0.22, 1.0),
            metallic=0.92,
            roughness=0.25,
        ),
        "rubber": create_material(
            "RubberFelt",
            base_color=(0.02, 0.02, 0.02, 1.0),
            metallic=0.0,
            roughness=0.9,
        ),
        "bulb_emission": create_material(
            "FrostedBulb",
            base_color=(0.95, 0.93, 0.88, 1.0),
            metallic=0.0,
            roughness=0.15,
            emission_color=(1.0, 0.96, 0.88, 1.0),
            emission_strength=4.0,
        ),
    }

    # 1. Base on ground plane (z=0, footprint centered)
    base_top_z = make_base(materials)

    # 2. Switch on top of base
    make_switch(materials, base_top_z)

    # 3. Arm leaning forward (-Y) with pivots
    top_pos = make_arm_and_mount(materials, base_top_z)

    # 4. Conical shade opening downward + inner bulb
    make_shade_and_bulb(materials, top_pos)


if __name__ == "__main__":
    main()
```