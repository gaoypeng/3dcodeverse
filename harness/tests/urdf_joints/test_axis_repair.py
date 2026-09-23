"""Deterministic axis repair: measured-wrong ``<axis xyz>`` rewritten in place."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.tracks.articulated_object import MOTION_GATE, repair_motion_axes
from codeverse3d.workspace import Workspace

URDF = """<?xml version="1.0"?>
<robot name="cab">
  <!-- authored comment must survive the repair -->
  <link name="body"/>
  <link name="door"/>
  <link name="tray"/>
  <joint name="Hinge" type="revolute">
    <parent link="body"/><child link="door"/>
    <origin xyz="-0.29 -0.2 0" rpy="0 0 0"/><axis xyz="0 0 -1"/>
    <limit lower="0" upper="1.57" effort="10" velocity="1"/>
  </joint>
  <joint name="Slide" type="prismatic">
    <parent link="body"/><child link="tray"/>
    <origin xyz="0 0 0" rpy="0 0 0"/>
    <limit lower="0" upper="0.3" effort="10" velocity="1"/>
  </joint>
</robot>
"""


def _ws(tmp_path) -> Workspace:
    ws = Workspace(tmp_path / "run")
    ws.create()
    (ws.src / "robot.urdf").write_text(URDF)
    return ws


def _finding(target: str, *, cos=None, suggested=None, sev=Severity.ERROR) -> GateFinding:
    data = {} if cos is None else {"cos": cos, "suggested_axis": suggested, "expected": "up"}
    return GateFinding(gate=MOTION_GATE, severity=sev, target=target, message="wrong direction", data=data)


def _report(*findings: GateFinding) -> GateReport:
    return GateReport(gate=MOTION_GATE, passed=False, findings=list(findings))


def _axis_of(ws: Workspace, joint: str):
    root = ET.fromstring((ws.src / "robot.urdf").read_text())
    el = next(j for j in root.findall("joint") if j.get("name") == joint).find("axis")
    return None if el is None else tuple(float(v) for v in el.get("xyz").split())


def test_repair_skips_what_it_cannot_trust_then_fixes_both_kinds(tmp_path):
    ws = _ws(tmp_path)
    (ws.artifacts / "robot.urdf").write_text(URDF)
    # WARN, an unknown joint and an unmeasured finding are all skipped
    rep = _report(_finding("Hinge", cos=-1.0, sev=Severity.WARN), _finding("Ghost", cos=-1.0), _finding("Hinge"))
    assert repair_motion_axes(ws, rep) == []
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, -1.0)
    # anti-parallel negates the authored axis (the plan's casing maps to the URDF joint);
    # orthogonal writes the suggested axis, inserting the missing <axis> element
    rep = _report(_finding("hinge", cos=-0.98), _finding("Slide", cos=0.0, suggested=(0, 0, 1)))
    assert repair_motion_axes(ws, rep) == ["hinge", "Slide"]
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, 1.0)
    assert _axis_of(ws, "Slide") == (0.0, 0.0, 1.0)
    assert "authored comment must survive" in (ws.src / "robot.urdf").read_text()
    # the artifacts copy is kept in step
    assert (ws.artifacts / "robot.urdf").read_text() == (ws.src / "robot.urdf").read_text()


@pytest.mark.parametrize("raw", ["0", "off", "false"])
def test_kill_switch_disables(tmp_path, switch, raw):
    """Every off spelling disables it (only "0" used to be read)."""
    switch("C3D_AXIS_REPAIR", raw)
    ws = _ws(tmp_path)
    assert repair_motion_axes(ws, _report(_finding("Hinge", cos=-1.0))) == []
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, -1.0)


def test_a_paired_axis_tag_keeps_the_joints_limit():
    """The splice of a paired <axis></axis> used to eat the joint's <limit/>."""
    from codeverse3d.tracks.articulated_object import _set_axis_in_urdf_text

    for axis_form in ('<axis xyz="1 0 0"></axis>', '<axis xyz="1 0 0"/>'):
        urdf = ('<robot name="r"><joint name="h" type="revolute">\n'
                '<parent link="a"/><child link="b"/>\n' + axis_form + '\n'
                '<limit lower="0" upper="1.57" effort="10" velocity="1"/>\n</joint></robot>')
        out = _set_axis_in_urdf_text(urdf, "h", (0.0, 0.0, 1.0))
        assert out is not None and '<axis xyz="0 0 1"/>' in out
        assert "<limit" in out, f"the splice ate the limit for {axis_form!r}"


def test_suggested_axis_is_expressed_in_the_joint_frame(tmp_path):
    """A rotated joint origin: installing the suggestion must actually fix the motion."""
    import numpy as np

    from codeverse3d.spatial.joints_model import load_urdf
    from codeverse3d.spatial.joints_sweep import motion_direction_check

    urdf = tmp_path / "r.urdf"
    urdf.write_text(
        '<robot name="r">\n'
        '  <link name="base"><visual><geometry><box size="0.2 0.2 0.2"/></geometry></visual>\n'
        '    <collision><geometry><box size="0.2 0.2 0.2"/></geometry></collision></link>\n'
        '  <link name="door"><visual><origin xyz="0.3 0 0"/><geometry><box size="0.6 0.05 0.4"/></geometry></visual>\n'
        '    <collision><origin xyz="0.3 0 0"/><geometry><box size="0.6 0.05 0.4"/></geometry></collision></link>\n'
        '  <joint name="hinge" type="revolute">\n'
        '    <parent link="base"/><child link="door"/>\n'
        '    <origin xyz="0.1 0 0" rpy="0 0 1.5708"/>\n'
        '    <axis xyz="1 0 0"/>\n'
        '    <limit lower="0" upper="1.57" effort="10" velocity="1"/>\n'
        '  </joint>\n</robot>')
    robot = load_urdf(urdf, load_meshes=True)
    chk = motion_direction_check(robot, "hinge", "up")
    assert not chk.ok and chk.suggested_axis is not None
    robot.joints["hinge"].axis = np.asarray(chk.suggested_axis, dtype=float)
    after = motion_direction_check(robot, "hinge", "up")
    assert after.ok and after.cos > 0.9, "installing the suggestion must actually fix the motion"
