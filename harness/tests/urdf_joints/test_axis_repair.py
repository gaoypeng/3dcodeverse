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


def test_anti_parallel_negates_the_authored_axis(tmp_path):
    ws = _ws(tmp_path)
    assert repair_motion_axes(ws, _report(_finding("Hinge", cos=-0.98))) == ["Hinge"]
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, 1.0)
    assert "authored comment must survive" in (ws.src / "robot.urdf").read_text()


def test_orthogonal_writes_suggested_axis_and_inserts_missing_element(tmp_path):
    ws = _ws(tmp_path)
    assert repair_motion_axes(ws, _report(_finding("Slide", cos=0.0, suggested=(0, 0, 1)))) == ["Slide"]
    assert _axis_of(ws, "Slide") == (0.0, 0.0, 1.0)


def test_plan_casing_maps_to_urdf_joint(tmp_path):
    ws = _ws(tmp_path)
    assert repair_motion_axes(ws, _report(_finding("hinge", cos=-1.0))) == ["hinge"]
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, 1.0)


def test_findings_without_measured_data_are_never_touched(tmp_path):
    ws = _ws(tmp_path)  # the FakeServices shape: ERROR finding, empty data
    assert repair_motion_axes(ws, _report(_finding("Hinge"))) == []
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, -1.0)


def test_warn_and_unknown_joints_are_skipped(tmp_path):
    ws = _ws(tmp_path)
    rep = _report(_finding("Hinge", cos=-1.0, sev=Severity.WARN), _finding("Ghost", cos=-1.0))
    assert repair_motion_axes(ws, rep) == []
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, -1.0)


@pytest.mark.parametrize("raw", ["0", "off", "false"])
def test_kill_switch_disables(tmp_path, switch, raw):
    """``C3D_AXIS_REPAIR=off`` was silently ignored until 2026-09-22: only "0" was read."""
    switch("C3D_AXIS_REPAIR", raw)
    ws = _ws(tmp_path)
    assert repair_motion_axes(ws, _report(_finding("Hinge", cos=-1.0))) == []
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, -1.0)


def test_artifacts_copy_is_kept_in_step(tmp_path):
    ws = _ws(tmp_path)
    (ws.artifacts / "robot.urdf").write_text(URDF)
    assert repair_motion_axes(ws, _report(_finding("Hinge", cos=-1.0))) == ["Hinge"]
    assert (ws.artifacts / "robot.urdf").read_text() == (ws.src / "robot.urdf").read_text()


def test_a_paired_axis_tag_keeps_the_joints_limit():
    """PR #3 review: '/>' never occurs inside '</axis>', so the old splice searched past
    the paired tag, landed on the NEXT self-closing element and deleted the joint's
    <limit/> — an invalid revolute joint shipped to src/ and artifacts/."""
    from codeverse3d.tracks.articulated_object import _set_axis_in_urdf_text

    for axis_form in ('<axis xyz="1 0 0"></axis>', '<axis xyz="1 0 0"/>'):
        urdf = ('<robot name="r"><joint name="h" type="revolute">\n'
                '<parent link="a"/><child link="b"/>\n' + axis_form + '\n'
                '<limit lower="0" upper="1.57" effort="10" velocity="1"/>\n</joint></robot>')
        out = _set_axis_in_urdf_text(urdf, "h", (0.0, 0.0, 1.0))
        assert out is not None and '<axis xyz="0 0 1"/>' in out
        assert "<limit" in out, f"the splice ate the limit for {axis_form!r}"


def test_suggested_axis_is_expressed_in_the_joint_frame(tmp_path):
    """PR #3 review: the suggestion was a WORLD vector written into the JOINT-frame
    <axis>; with a rotated joint origin the 'exact' repair installed a provably wrong
    axis and the motion gate kept its ERROR every round.  The suggestion must FIX the
    motion when installed."""
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
