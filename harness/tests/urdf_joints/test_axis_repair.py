"""Deterministic axis repair: measured-wrong ``<axis xyz>`` rewritten in place."""
from __future__ import annotations

import xml.etree.ElementTree as ET

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

