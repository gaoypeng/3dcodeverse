"""Coupled mechanisms: a <mimic> joint follows the joint it names, everywhere."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.plan import ArticulatedPlan
from codeverse3d.languages.urdf import lint_workspace
from codeverse3d.spatial.joints_model import UrdfError, fk, load_urdf, resolve_q
from codeverse3d.spatial.joints_poses import pose_samples
from codeverse3d.spatial.joints_sweep import motion_direction_check, sweep_collisions
from tests.urdf_joints.conftest import box_glb

RIB = """<?xml version="1.0"?>
<robot name="umbrella">
  <link name="shaft"><visual><geometry><mesh filename="meshes/shaft.glb"/></geometry></visual></link>
  <link name="runner"><visual><geometry><mesh filename="meshes/runner.glb"/></geometry></visual></link>
  <link name="rib_a"><visual><geometry><mesh filename="meshes/rib_a.glb"/></geometry></visual></link>
  <link name="rib_b"><visual><geometry><mesh filename="meshes/rib_b.glb"/></geometry></visual></link>
  <joint name="runner_slide" type="prismatic">
    <parent link="shaft"/><child link="runner"/>
    <origin xyz="0 0 0.5" rpy="0 0 0"/><axis xyz="0 0 1"/>
    <limit lower="0" upper="0.3" effort="10" velocity="1"/>
  </joint>
  <joint name="rib_a_hinge" type="revolute">
    <parent link="shaft"/><child link="rib_a"/>
    <origin xyz="0 0 1" rpy="0 0 0"/><axis xyz="0 1 0"/>
    <limit lower="0" upper="1.2" effort="10" velocity="1"/>
    <mimic joint="runner_slide" multiplier="4" offset="0"/>
  </joint>
  <joint name="rib_b_hinge" type="revolute">
    <parent link="shaft"/><child link="rib_b"/>
    <origin xyz="0 0 1" rpy="0 0 0"/><axis xyz="0 -1 0"/>
    <limit lower="0" upper="1.2" effort="10" velocity="1"/>
    <mimic joint="rib_a_hinge" multiplier="1" offset="0"/>
  </joint>
</robot>
"""


def _robot(tmp_path: Path, urdf_text: str = RIB):
    meshes = tmp_path / "meshes"
    box_glb(meshes / "shaft.glb", (0, 0, 0.6), (0.04, 0.04, 1.2))
    box_glb(meshes / "runner.glb", (0, 0, 0.5), (0.08, 0.08, 0.06))
    box_glb(meshes / "rib_a.glb", (0.25, 0, 1.0), (0.5, 0.02, 0.02))
    box_glb(meshes / "rib_b.glb", (-0.25, 0, 1.0), (0.5, 0.02, 0.02))
    urdf = tmp_path / "robot.urdf"
    urdf.write_text(urdf_text)
    return load_urdf(urdf, meshes)


def test_a_driven_joint_follows_its_driver_everywhere(tmp_path):
    r = _robot(tmp_path)
    assert [j.name for j in r.independent_joints()] == ["runner_slide"]
    assert r.joints["rib_a_hinge"].mimic and r.joints["rib_b_hinge"].mimic
    q = resolve_q(r, {"runner_slide": 0.2})
    assert q["rib_a_hinge"] == pytest.approx(0.8)      # multiplier 4
    assert q["rib_b_hinge"] == pytest.approx(0.8)      # follows rib_a_hinge, multiplier 1
    # a value handed in for a driven joint is ignored: the mechanism has one input
    assert resolve_q(r, {"runner_slide": 0.1, "rib_a_hinge": 99.0})["rib_a_hinge"] == pytest.approx(0.4)
    assert fk(r, {"runner_slide": 0.1})["rib_a"][:3, :3].tolist() != fk(r, {})["rib_a"][:3, :3].tolist()   # the coupling shows in the rotation
    assert {p.label for p in sweep_collisions(r, pose_samples(r)).per_pose} == {"rest", "runner_slide@upper", "runner_slide@mid"}   # one input, not three
    assert "driven by runner_slide" in motion_direction_check(r, "rib_a_hinge", "up").message


@pytest.mark.parametrize("bad,msg", [('<mimic joint="rib_a_hinge" multiplier="0"/>', "cannot move"), ('<mimic joint="rib_b_hinge"/>', "cycle")])
def test_a_broken_coupling_is_refused_at_load(tmp_path, bad, msg):
    with pytest.raises(UrdfError, match=msg):
        _robot(tmp_path, RIB.replace('<mimic joint="runner_slide" multiplier="4" offset="0"/>', bad))


EXTRA_RUNNER = """  <link name="runner2"><visual><geometry><mesh filename="meshes/rib_b.glb"/></geometry></visual></link>
  <joint name="runner_slide_2" type="prismatic">
    <parent link="shaft"/><child link="runner2"/>
    <origin xyz="0 0 0.6" rpy="0 0 0"/><axis xyz="0 0 1"/>
    <limit lower="0" upper="0.3" effort="10" velocity="1"/>
  </joint>
</robot>"""


def _plan(**joint_overrides):
    """The worked articulated example with a second joint that can carry a coupling."""
    import copy

    from codeverse3d.contracts.common import Track
    from codeverse3d.tracks.planner import plan_example

    d = copy.deepcopy(plan_example(Track.ARTICULATED_OBJECT))
    d["parts"].append({"name": "Lid", "role": "top lid", "description": "a flat lid", "attach_to": "Cabinet",
                       "bbox": {"center": [0, 0, 0.61], "extents": [0.4, 0.5, 0.02]}, "material": "oak"})
    d["joints"].append({"name": "LidHinge", "type": "revolute", "parent": "Cabinet", "child": "Lid",
                        "axis": [1, 0, 0], "pivot": [0, 0.21, 0.6], "lower": 0.0, "upper": 1.2, "rest": 0.0,
                        "motion": "the lid tilts up"})
    d["joints"][1].update(joint_overrides)
    return d


def test_the_lint_on_couplings(tmp_path):
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "ws").create()
    (ws.src / "model.py").write_text("import bpy\n")
    urdf = ws.src / "robot.urdf"

    def lint(text):
        urdf.write_text(text)
        return lint_workspace(ws).findings

    # a real coupling is accepted; at multiplier 4 it reaches exactly the 1.2 limit (silent)
    findings = lint(RIB)
    assert [f for f in findings if "mimic" in f.message] == []
    assert not [f for f in findings if "drives it over" in f.message]
    # a broken one is named, once (rib_b follows rib_a, which follows the ghost)
    errs = [f for f in lint(RIB.replace('joint="runner_slide" multiplier="4"', 'joint="ghost" multiplier="4"'))
            if "mimic" in f.message and f.severity.value == "error"]
    assert len(errs) == 1 and "names no joint" in errs[0].message
    # at 5 the coupling drives past the follower's own limits
    warns = [f for f in lint(RIB.replace('joint="runner_slide" multiplier="4"', 'joint="runner_slide" multiplier="5"'))
             if "drives it over" in f.message]
    assert len(warns) == 1 and warns[0].severity.value == "warn" and warns[0].target == "rib_a_hinge"
    assert "1.5" in warns[0].message
    # no single <mimic> is wrong, only the graph: the lint must see the cycle before the build
    cycles = [f for f in lint(RIB.replace('<mimic joint="runner_slide" multiplier="4" offset="0"/>',
                                          '<mimic joint="rib_b_hinge" multiplier="1" offset="0"/>'))
              if "cycle" in f.message]
    assert cycles and {f.target for f in cycles} == {"rib_a_hinge", "rib_b_hinge"}
    # hinge_1 / hinge_2 written by an agent are two joints, not one instanced driver
    findings = lint(RIB.replace('joint name="runner_slide"', 'joint name="runner_slide_1"')
                       .replace('<mimic joint="runner_slide" ', '<mimic joint="runner_slide_1" ')
                       .replace("</robot>", EXTRA_RUNNER))
    assert not [f for f in findings if "independent inputs" in f.message]
    assert not [f for f in findings if "mimic" in f.message and f.severity.value == "error"]


def test_a_broken_coupling_is_refused_by_the_plan():
    for mimic, msg in (({"joint": "NoSuchJoint"}, "is not a joint in this plan"), ({"joint": "LidHinge"}, "mimics itself")):
        with pytest.raises(Exception, match=msg):
            ArticulatedPlan.model_validate(_plan(mimic=mimic))
    # a coupling that names a fixed joint
    d = _plan(mimic={"joint": "CabinetWeld"})
    d["parts"].append({"name": "Plinth", "role": "base", "description": "a plinth", "attach_to": "Cabinet",
                       "bbox": {"center": [0, 0, -0.05], "extents": [0.4, 0.5, 0.1]}, "material": "oak"})
    d["joints"].append({"name": "CabinetWeld", "type": "fixed", "parent": "Cabinet", "child": "Plinth",
                        "axis": [0, 0, 1], "pivot": [0, 0, 0], "lower": 0.0, "upper": 0.0, "motion": "rigid"})
    with pytest.raises(Exception, match="which is fixed and never moves"):
        ArticulatedPlan.model_validate(d)
    # a cycle
    d = _plan(mimic={"joint": "DrawerSlide"})
    d["joints"][0]["mimic"] = {"joint": "LidHinge"}
    with pytest.raises(Exception, match="loops back through"):
        ArticulatedPlan.model_validate(d)


def test_the_three_layers_reject_the_same_near_zero_multiplier(tmp_path):
    """Plan validator, loader and lint share one MIMIC_MIN_MULTIPLIER floor."""
    from codeverse3d.contracts.common import MIMIC_MIN_MULTIPLIER
    from codeverse3d.workspace import Workspace

    dead = 1e-10  # under the shared floor, over the 1e-12 the loader and the lint used
    assert dead < MIMIC_MIN_MULTIPLIER
    with pytest.raises(Exception, match="multiplier 0"):
        ArticulatedPlan.model_validate(_plan(mimic={"joint": "DrawerSlide", "multiplier": dead}))

    text = RIB.replace('multiplier="4"', f'multiplier="{dead}"')
    with pytest.raises(UrdfError, match="cannot move"):
        _robot(tmp_path, text)

    ws = Workspace(tmp_path / "lint_ws").create()
    (ws.src / "robot.urdf").write_text(text)
    (ws.src / "model.py").write_text("import bpy\n")
    assert [f for f in lint_workspace(ws).findings if "cannot move" in f.message]
