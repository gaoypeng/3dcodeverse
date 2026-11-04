"""Shared fixtures: synthetic robots (box primitives or trimesh-written GLBs) and plans."""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from codeverse3d.contracts.plan import ArticulatedPlan, BBox, JointPlan, PartPlan

CABINET_PRIMS = """<robot name="cab">
<link name="body"><visual><origin xyz="0 0 0.4"/><geometry><box size="0.6 0.4 0.8"/></geometry></visual></link>
<link name="door"><visual><origin xyz="0.29 -0.01 0.4"/><geometry><box size="0.58 0.02 0.78"/></geometry></visual></link>
<joint name="hinge" type="revolute"><parent link="body"/><child link="door"/>
  <origin xyz="-0.29 -0.2 0" rpy="0 0 0"/><axis xyz="0 0 {axis_z}"/><limit lower="0" upper="1.57" effort="10" velocity="1"/></joint>
</robot>"""


def write_prims_robot(path: Path, *, axis_z: int = -1) -> Path:
    path.write_text(CABINET_PRIMS.format(axis_z=axis_z))
    return path


def box_glb(path: Path, center, size) -> Path:
    m = trimesh.creation.box(size)
    m.apply_translation(center)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(m.export(file_type="glb"))
    return path


def write_mesh_robot(root: Path, *, door_visual_xyz: str = "0.29 0.2 0", handle: bool = False) -> tuple[Path, Path]:
    """URDF in the enforced convention + world-coordinate GLB meshes (like the wrapper writes)."""
    meshes = root / "meshes"
    box_glb(meshes / "body.glb", (0, 0, 0.4), (0.6, 0.4, 0.8))
    box_glb(meshes / "door.glb", (0, -0.21, 0.4), (0.58, 0.02, 0.78))
    links = f"""
<link name="body"><visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></visual>
  <collision><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></collision></link>
<link name="door"><visual><origin xyz="{door_visual_xyz}" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></visual>
  <collision><origin xyz="{door_visual_xyz}" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></collision></link>
<joint name="hinge" type="revolute"><parent link="body"/><child link="door"/>
  <origin xyz="-0.29 -0.2 0" rpy="0 0 0"/><axis xyz="0 0 -1"/><limit lower="0" upper="1.57" effort="10" velocity="1"/></joint>"""
    if handle:
        box_glb(meshes / "handle.glb", (0.2, -0.24, 0.4), (0.02, 0.04, 0.1))
        links += """
<link name="handle"><visual><origin xyz="-0.2 0.24 -0.4" rpy="0 0 0"/><geometry><mesh filename="meshes/handle.glb"/></geometry></visual>
  <collision><origin xyz="-0.2 0.24 -0.4" rpy="0 0 0"/><geometry><mesh filename="meshes/handle.glb"/></geometry></collision></link>
<joint name="handle_mount" type="fixed"><parent link="door"/><child link="handle"/><origin xyz="0.49 -0.04 0.4" rpy="0 0 0"/></joint>"""
    urdf = root / "robot.urdf"
    urdf.write_text(f'<robot name="cab">{links}\n</robot>\n')
    return urdf, meshes


@pytest.fixture
def cabinet_plan() -> ArticulatedPlan:
    return ArticulatedPlan(
        object_name="Cabinet", summary="cabinet with a hinged door",
        overall_bbox=BBox(center=(0, -0.01, 0.4), extents=(0.6, 0.42, 0.8)), root_link="Body",
        parts=[
            PartPlan(name="Body", role="carcass", description="hollow box", bbox=BBox(center=(0, 0, 0.4), extents=(0.6, 0.4, 0.8))),
            PartPlan(name="Door", role="front door", description="panel", bbox=BBox(center=(0, -0.21, 0.4), extents=(0.58, 0.02, 0.78))),
            PartPlan(name="Handle", role="door handle", description="bar", bbox=BBox(center=(0.2, -0.24, 0.4), extents=(0.02, 0.04, 0.1)),
                     attach_to="Door"),
        ],
        joints=[
            JointPlan(name="hinge", type="revolute", parent="Body", child="Door", axis=(0, 0, -1), pivot=(-0.29, -0.2, 0),
                      lower=0.0, upper=1.57, motion="door swings open to the front"),
            JointPlan(name="handle_mount", type="fixed", parent="Door", child="Handle", axis=(0, 0, 1), pivot=(0.2, -0.24, 0.4)),
        ],
    )
