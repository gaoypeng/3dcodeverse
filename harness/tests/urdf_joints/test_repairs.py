"""Deterministic articulated repairs (tracks/articulated_repairs.py)."""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.joints import load_urdf
from codeverse.tracks.articulated_repairs import buried_links, flip_joint_axis
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
  <joint name="no_axis" type="prismatic">
    <parent link="body"/>
    <child link="knob"/>
    <limit lower="0" upper="0.1" effort="1" velocity="1"/>
  </joint>
</robot>
"""


def test_flip_negates_only_the_named_joint():
    text, xyz = flip_joint_axis(URDF, "Hinge")  # any casing
    assert xyz == "0 0 -1" and '<axis xyz="0 0 -1"/>' in text
    assert text.count("<axis") == URDF.count("<axis")  # nothing added elsewhere
    assert text.replace('<axis xyz="0 0 -1"/>', '<axis xyz="0 0 1"/>') == URDF  # everything else byte-identical
    again, xyz2 = flip_joint_axis(text, "hinge")
    assert xyz2 == "0 0 1" and again == URDF  # an involution


def test_flip_writes_the_negated_default_axis_when_missing_and_ignores_unknown_joints():
    text, xyz = flip_joint_axis(URDF, "no_axis")
    assert xyz == "-1 0 0" and '<axis xyz="-1 0 0"/>' in text
    same, none = flip_joint_axis(URDF, "not_there")
    assert none is None and same == URDF
    t2, x2 = flip_joint_axis('<joint name="j" type="revolute"><axis xyz="0.5 -0.25 0"/></joint>', "j")
    assert x2 == "-0.5 0.25 0"


def _two_box_robot(root: Path, *, knob_center) -> Path:
    meshes = root / "meshes"
    meshes.mkdir(parents=True, exist_ok=True)
    box_glb(meshes / "body.glb", (0, 0, 0.5), (0.6, 0.6, 1.0))
    box_glb(meshes / "door.glb", (0.31, 0, 0.5), (0.02, 0.6, 1.0))
    box_glb(meshes / "knob.glb", knob_center, (0.04, 0.04, 0.04))
    urdf = root / "robot.urdf"
    urdf.write_text(URDF.replace('''  <joint name="no_axis" type="prismatic">
    <parent link="body"/>
    <child link="knob"/>
    <limit lower="0" upper="0.1" effort="1" velocity="1"/>
  </joint>
''', ""))
    return urdf


def test_buried_link_is_reported_and_a_visible_one_is_not(tmp_path):
    r = load_urdf(_two_box_robot(tmp_path / "a", knob_center=(0, 0, 0.5)), tmp_path / "a" / "meshes")
    out = buried_links(r)
    assert [f.target for f in out] == ["knob"] and out[0].severity == Severity.ERROR
    assert out[0].data["inside"] == "body" and out[0].data["fraction"] >= 0.98 and out[0].data["kind"] == "buried"
    r = load_urdf(_two_box_robot(tmp_path / "b", knob_center=(0.35, 0, 0.5)), tmp_path / "b" / "meshes")
    assert buried_links(r) == []


# ------------------------------------------------------------------ motion-direction repair
class _Events:
    def __init__(self):
        self.items = []

    def emit(self, event, **kw):
        self.items.append((event, kw))


def _ws(tmp_path):
    from types import SimpleNamespace

    from tests.urdf_joints.conftest import write_mesh_robot

    art = tmp_path / "artifacts"
    src = tmp_path / "src"
    write_mesh_robot(art)
    src.mkdir()
    (src / "robot.urdf").write_text((art / "robot.urdf").read_text())
    return SimpleNamespace(artifacts=art, src=src)


def test_reversed_hinge_is_flipped_in_both_urdf_copies_and_passes(tmp_path, cabinet_plan):
    from codeverse.tracks.articulated_object import MOTION_GATE, default_motion_checks
    from codeverse.tracks.articulated_repairs import repair_motion_directions

    ws = _ws(tmp_path)
    for p in (ws.artifacts / "robot.urdf", ws.src / "robot.urdf"):  # break it: the door now swings into the body
        p.write_text(flip_joint_axis(p.read_text(), "hinge")[0])
    before = default_motion_checks(ws, cabinet_plan)
    assert before is not None and not before.passed and [f.target for f in before.errors] == ["hinge"]
    ev = _Events()
    after = repair_motion_directions(ws, cabinet_plan, before, recheck=default_motion_checks, events=ev)
    assert after.gate == MOTION_GATE and after.passed
    repair = [f for f in after.findings if f.data.get("kind") == "repair"]
    assert len(repair) == 1 and repair[0].severity == Severity.WARN and "keep it" in repair[0].message
    assert '<axis xyz="0 0 -1"/>' in (ws.src / "robot.urdf").read_text()
    assert '<axis xyz="0 0 -1"/>' in (ws.artifacts / "robot.urdf").read_text()
    assert ev.items == [("repair.motion_flip", {"kept": {"hinge": "0 0 -1"}, "reverted": []})]


def test_a_flip_that_does_not_help_is_reverted(tmp_path, cabinet_plan):
    """The plan says 'front' but the hinge axis is z: flipping changes swing direction, not
    the front/back sense of a door that swings sideways — the flip must not stick."""
    from codeverse.contracts.artifacts import GateFinding, GateReport
    from codeverse.tracks.articulated_object import MOTION_GATE
    from codeverse.tracks.articulated_repairs import repair_motion_directions

    ws = _ws(tmp_path)
    original = (ws.src / "robot.urdf").read_text()
    fake_report = GateReport(gate=MOTION_GATE, passed=False, findings=[GateFinding(
        gate=MOTION_GATE, severity=Severity.ERROR, target="hinge", message="wrong")])

    def still_wrong(ws_, plan_):
        return fake_report

    after = repair_motion_directions(ws, cabinet_plan, fake_report, recheck=still_wrong)
    assert not after.passed and (ws.src / "robot.urdf").read_text() == original
    assert (ws.artifacts / "robot.urdf").read_text() == original


def test_no_flip_is_attempted_when_a_flip_would_not_satisfy_the_check(tmp_path, cabinet_plan):
    """The door swings about z; the plan (here) says it should move UP — no sign satisfies
    that, so the file is left alone and the fixer is told a flip would not help."""
    from codeverse.tracks.articulated_object import default_motion_checks
    from codeverse.tracks.articulated_repairs import repair_motion_directions

    ws = _ws(tmp_path)
    plan = cabinet_plan.model_copy(deep=True)
    plan.joints[0].motion = "door swings up"
    original = (ws.src / "robot.urdf").read_text()
    before = default_motion_checks(ws, plan)
    assert before is not None and [f.target for f in before.errors] == ["hinge"]
    ev = _Events()
    after = repair_motion_directions(ws, plan, before, recheck=default_motion_checks, events=ev)
    assert not after.passed and (ws.src / "robot.urdf").read_text() == original
    assert ev.items == []  # nothing was flipped, nothing to announce
    assert "sign flip alone would NOT" in after.errors[0].fix_hint
