"""Deterministic axis repair: measured-wrong ``<axis xyz>`` rewritten in place."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.tracks.articulated_object import MOTION_GATE, repair_motion_axes
from codeverse.workspace import Workspace

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


def test_kill_switch_disables(tmp_path, monkeypatch):
    monkeypatch.setenv("CV3D_AXIS_REPAIR", "0")
    ws = _ws(tmp_path)
    assert repair_motion_axes(ws, _report(_finding("Hinge", cos=-1.0))) == []
    assert _axis_of(ws, "Hinge") == (0.0, 0.0, -1.0)


def test_artifacts_copy_is_kept_in_step(tmp_path):
    ws = _ws(tmp_path)
    (ws.artifacts / "robot.urdf").write_text(URDF)
    assert repair_motion_axes(ws, _report(_finding("Hinge", cos=-1.0))) == ["Hinge"]
    assert (ws.artifacts / "robot.urdf").read_text() == (ws.src / "robot.urdf").read_text()
