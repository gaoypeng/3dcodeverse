"""A link generated fully inside another is a gate ERROR (spatial.joints.buried_links)."""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.joints import buried_links, load_urdf
from tests.urdf_joints.conftest import box_glb

URDF = """<?xml version="1.0"?>
<robot name="t">
  <link name="body"><visual><geometry><mesh filename="meshes/body.glb"/></geometry></visual></link>
  <link name="door"><visual><geometry><mesh filename="meshes/door.glb"/></geometry></visual></link>
  <link name="knob"><visual><geometry><mesh filename="meshes/knob.glb"/></geometry></visual></link>
  <joint name="hinge" type="revolute">
    <parent link="body"/>
    <child link="door"/>
    <origin xyz="0 0 0" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
    <limit lower="0" upper="1.57" effort="1" velocity="1"/>
  </joint>
  <joint name="knob_fixed" type="fixed">
    <parent link="door"/>
    <child link="knob"/>
    <origin xyz="0 0 0" rpy="0 0 0"/>
  </joint>
</robot>
"""


def _robot(root: Path, *, knob_center):
    meshes = root / "meshes"
    meshes.mkdir(parents=True, exist_ok=True)
    box_glb(meshes / "body.glb", (0, 0, 0.5), (0.6, 0.6, 1.0))
    box_glb(meshes / "door.glb", (0.31, 0, 0.5), (0.02, 0.6, 1.0))
    box_glb(meshes / "knob.glb", knob_center, (0.04, 0.04, 0.04))
    urdf = root / "robot.urdf"
    urdf.write_text(URDF)
    return load_urdf(urdf, meshes)


def test_buried_link_is_reported_and_a_visible_one_is_not(tmp_path):
    out = buried_links(_robot(tmp_path / "a", knob_center=(0, 0, 0.5)))
    assert [f.target for f in out] == ["knob"] and out[0].severity == Severity.ERROR
    assert out[0].data["inside"] == "body" and out[0].data["fraction"] >= 0.98 and out[0].data["kind"] == "buried"
    assert buried_links(_robot(tmp_path / "b", knob_center=(0.35, 0, 0.5))) == []
