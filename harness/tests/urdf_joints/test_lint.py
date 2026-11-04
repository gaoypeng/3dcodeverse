"""URDF + model.py lint rules."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.urdf import lint_urdf_text
from codeverse3d.spatial.joints_model import UrdfError, load_urdf

GOOD = """<?xml version="1.0"?>
<robot name="cab">
  <link name="body">
    <visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></visual>
    <collision><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></collision>
  </link>
  <link name="door">
    <visual><origin xyz="0.29 0.2 0" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></visual>
    <collision><origin xyz="0.29 0.2 0" rpy="0 0 0"/><geometry><mesh filename="meshes/door.glb"/></geometry></collision>
  </link>
  <joint name="hinge" type="revolute">
    <parent link="body"/><child link="door"/>
    <origin xyz="-0.29 -0.2 0" rpy="0 0 0"/><axis xyz="0 0 -1"/>
    <limit lower="0" upper="1.57" effort="10" velocity="1"/>
  </joint>
</robot>
"""
MODEL = 'import bpy\nbpy.ops.mesh.primitive_cube_add()\nbpy.context.active_object.name = "body"\nd = bpy.data.objects.new("door", None)\n'


def _msgs(findings, sev=None):
    return [f.message for f in findings if sev is None or f.severity == sev]


def _door(old: str, new: str) -> str:
    return GOOD.replace(old, new)


# (mutation of GOOD, severity, a fragment of the finding's message): one row per lint rule
RULES = [
    (_door('<joint name="hinge"', '<link name="x"/><joint name="hinge"'), Severity.ERROR, "exactly one root"),
    (_door("</robot>", '<joint name="j2" type="fixed"><parent link="body"/><child link="door"/></joint></robot>'), Severity.ERROR, "child of two joints"),
    (_door('lower="0" upper="1.57"', 'lower="1" upper="0"'), Severity.ERROR, "upper 0.0 < lower 1.0"),
    (_door('filename="meshes/door.glb"', 'filename="door.glb"'), Severity.ERROR, "mesh filename"),
    (_door('name="door"', 'name="DoorOpen"').replace('link="door"', 'link="DoorOpen"').replace("meshes/door.glb", "meshes/DoorOpen.glb"),
     Severity.WARN, "state word"),
    (_door('name="body"', 'name="world"').replace('link="body"', 'link="world"').replace("meshes/body.glb", "meshes/world.glb"),
     Severity.ERROR, "reserved"),
]


@pytest.mark.parametrize(("text", "severity", "fragment"), RULES, ids=[r[2] for r in RULES])
def test_lint_rules(text, severity, fragment):
    f, _ = lint_urdf_text(text)
    assert any(fragment in x.message and x.severity == severity for x in f), _msgs(f)


def test_xml_errors_and_pascal_case_part_names_are_clean():
    f, _ = lint_urdf_text("<robot name='x'><link name='a'>")
    assert f[0].severity == Severity.ERROR and "well-formed" in f[0].message and f[0].data.get("line") == 1
    # the plan's PascalCase part names are accepted silently (check_contract normalises names)
    f, _ = lint_urdf_text(_door('name="door"', 'name="FrontDoor"').replace('link="door"', 'link="FrontDoor"').replace("meshes/door.glb", "meshes/FrontDoor.glb"))
    assert f == []


def test_lint_clean_means_the_loader_parses(tmp_path):
    """Regression (audit N18): lint and build parse joints through one ``parse_joint``; a bad limit once raised a bare ValueError."""
    text = GOOD.replace('lower="0" upper="1.57"', 'lower="0 1" upper="1.57"')
    (tmp_path / "robot.urdf").write_text(text)
    with pytest.raises(UrdfError) as e:
        load_urdf(tmp_path / "robot.urdf", load_meshes=False)
    assert str(e.value) in _msgs(lint_urdf_text(text)[0], Severity.ERROR)
