"""Coupled mechanisms: a <mimic> joint follows the joint it names, everywhere."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.plan import ArticulatedPlan
from codeverse3d.languages.urdf import lint_workspace, render_urdf
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


def test_a_driven_joint_follows_its_driver_through_the_chain(tmp_path):
    r = _robot(tmp_path)
    assert [j.name for j in r.independent_joints()] == ["runner_slide"]
    assert r.joints["rib_a_hinge"].driven and r.joints["rib_b_hinge"].driven
    q = resolve_q(r, {"runner_slide": 0.2})
    assert q["rib_a_hinge"] == pytest.approx(0.8)      # multiplier 4
    assert q["rib_b_hinge"] == pytest.approx(0.8)      # follows rib_a_hinge, multiplier 1
    # a value handed in for a driven joint is ignored: the mechanism has one input
    assert resolve_q(r, {"runner_slide": 0.1, "rib_a_hinge": 99.0})["rib_a_hinge"] == pytest.approx(0.4)
    # the rib hinges about its own frame origin, so the coupling shows in the rotation
    assert fk(r, {"runner_slide": 0.1})["rib_a"][:3, :3].tolist() != fk(r, {})["rib_a"][:3, :3].tolist()
    assert fk(r, {"runner_slide": 0.1})["runner"][2, 3] == pytest.approx(fk(r, {})["runner"][2, 3] + 0.1)


def test_the_contact_sheet_and_joint_narrowing_show_only_input_joints(tmp_path):
    from codeverse3d.spatial.joints_model import fk
    from codeverse3d.spatial.joints_poses import limit_poses

    r = _robot(tmp_path)
    labels = [lab for lab, _ in limit_poses(r)]
    assert labels == ["rest", "runner_slide@upper"], labels
    assert not any("rib_" in lab for lab in labels)
    # and the one non-rest pose really moves the ribs, through the driver
    _, q = limit_poses(r)[1]
    assert fk(r, q)["rib_a"][:3, :3].tolist() != fk(r, {})["rib_a"][:3, :3].tolist()


def test_the_sweep_poses_the_mechanism_the_way_it_moves(tmp_path):
    r = _robot(tmp_path)
    rep = sweep_collisions(r, pose_samples(r))
    assert {p.label for p in rep.per_pose} == {"rest", "runner_slide@upper", "runner_slide@mid"}
    assert rep.summary.n_poses == 3  # one input, not three


def test_a_driven_joint_is_probed_through_its_driver(tmp_path):
    r = _robot(tmp_path)
    chk = motion_direction_check(r, "rib_a_hinge", "up")
    assert "driven by runner_slide" in chk.message
    assert chk.observed_dir != (0.0, 0.0, 0.0)


@pytest.mark.parametrize("bad,msg", [
    ('<mimic joint="nope"/>', "names no joint"),
    ('<mimic joint="rib_a_hinge" multiplier="0"/>', "cannot move"),
    ('<mimic joint="rib_b_hinge"/>', "cycle"),
])
def test_a_broken_coupling_is_refused_at_load(tmp_path, bad, msg):
    text = RIB.replace('<mimic joint="runner_slide" multiplier="4" offset="0"/>', bad)
    with pytest.raises(UrdfError, match=msg):
        _robot(tmp_path, text)


def test_the_lint_accepts_a_real_coupling_and_names_a_broken_one(tmp_path, monkeypatch):
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "ws").create()
    (ws.src / "robot.urdf").write_text(RIB)
    (ws.src / "model.py").write_text("import bpy\n")
    findings = [f for f in lint_workspace(ws).findings if "mimic" in f.message]
    assert findings == []
    (ws.src / "robot.urdf").write_text(RIB.replace('joint="runner_slide" multiplier="4"', 'joint="ghost" multiplier="4"'))
    errs = [f for f in lint_workspace(ws).findings if "mimic" in f.message and f.severity.value == "error"]
    assert len(errs) == 1 and "names no joint" in errs[0].message


# ------------------------------------------------------------------ the plan's own rules
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


@pytest.mark.parametrize("mimic,msg", [
    ({"joint": "NoSuchJoint"}, "is not a joint in this plan"),
    ({"joint": "LidHinge"}, "mimics itself"),
])
def test_a_broken_coupling_is_refused_by_the_plan(mimic, msg):
    with pytest.raises(Exception, match=msg):
        ArticulatedPlan.model_validate(_plan(mimic=mimic))


def test_a_coupling_that_names_a_fixed_joint_is_refused():
    d = _plan(mimic={"joint": "CabinetWeld"})
    d["parts"].append({"name": "Plinth", "role": "base", "description": "a plinth", "attach_to": "Cabinet",
                       "bbox": {"center": [0, 0, -0.05], "extents": [0.4, 0.5, 0.1]}, "material": "oak"})
    d["joints"].append({"name": "CabinetWeld", "type": "fixed", "parent": "Cabinet", "child": "Plinth",
                        "axis": [0, 0, 1], "pivot": [0, 0, 0], "lower": 0.0, "upper": 0.0, "motion": "rigid"})
    with pytest.raises(Exception, match="which is fixed and never moves"):
        ArticulatedPlan.model_validate(d)


def test_a_mimic_cycle_is_refused():
    d = _plan(mimic={"joint": "DrawerSlide"})
    d["joints"][0]["mimic"] = {"joint": "LidHinge"}
    with pytest.raises(Exception, match="loops back through"):
        ArticulatedPlan.model_validate(d)


# ------------------------------------------------------------------ one rule set, three layers
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


def test_the_lint_sees_a_cycle_the_per_joint_pass_could_not(tmp_path):
    """No single <mimic> is wrong, only the graph: the lint must see it before the build."""
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "ws").create()
    (ws.src / "robot.urdf").write_text(RIB.replace('<mimic joint="runner_slide" multiplier="4" offset="0"/>',
                                                   '<mimic joint="rib_b_hinge" multiplier="1" offset="0"/>'))
    (ws.src / "model.py").write_text("import bpy\n")
    cycles = [f for f in lint_workspace(ws).findings if "cycle" in f.message]
    assert cycles and {f.target for f in cycles} == {"rib_a_hinge", "rib_b_hinge"}


def test_an_unknown_target_is_reported_once_not_once_per_follower():
    from codeverse3d.contracts.common import MimicSpec, mimic_issues

    issues = mimic_issues([
        MimicSpec(key="a", name="a", movable=True, target="ghost"),
        MimicSpec(key="b", name="b", movable=True, target="a"),
    ])
    assert [(i.joint, i.kind) for i in issues] == [("a", "unknown_target")]


def test_the_lint_warns_when_a_coupling_drives_past_the_follower_s_own_limits(tmp_path):
    """At multiplier 4 the coupling reaches exactly the 1.2 limit (silent); at 5 it overshoots."""
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "ws").create()
    (ws.src / "model.py").write_text("import bpy\n")
    (ws.src / "robot.urdf").write_text(RIB)
    assert not [f for f in lint_workspace(ws).findings if "drives it over" in f.message]

    (ws.src / "robot.urdf").write_text(RIB.replace('joint="runner_slide" multiplier="4"',
                                                   'joint="runner_slide" multiplier="5"'))
    warns = [f for f in lint_workspace(ws).findings if "drives it over" in f.message]
    assert len(warns) == 1 and warns[0].severity.value == "warn" and warns[0].target == "rib_a_hinge"
    assert "1.5" in warns[0].message


EXTRA_RUNNER = """  <link name="runner2"><visual><geometry><mesh filename="meshes/rib_b.glb"/></geometry></visual></link>
  <joint name="runner_slide_2" type="prismatic">
    <parent link="shaft"/><child link="runner2"/>
    <origin xyz="0 0 0.6" rpy="0 0 0"/><axis xyz="0 0 1"/>
    <limit lower="0" upper="0.3" effort="10" velocity="1"/>
  </joint>
</robot>"""


def test_the_skeleton_says_when_only_the_first_instance_of_a_driver_is_followed():
    """An instanced driver exists only as <name>_1.._n: the skeleton follows _1 and says so."""
    from codeverse3d.languages.urdf import compute_urdf_frames

    d = _plan()
    d["parts"][1]["instances"] = 2
    d["joints"][1]["mimic"] = {"joint": "DrawerSlide", "multiplier": 0.5}
    urdf = render_urdf(compute_urdf_frames(ArticulatedPlan.model_validate(d)))
    assert '<mimic joint="drawer_slide_1" multiplier="0.5" offset="0"/>' in urdf
    names = {ln.split('"')[1] for ln in urdf.splitlines() if "<joint name=" in ln}
    refs = {ln.split('"')[1] for ln in urdf.splitlines() if "<mimic joint=" in ln}
    assert refs <= names, f"dangling mimic reference: {refs - names}"
    line = next(ln for ln in urdf.splitlines() if "<mimic joint=" in ln)
    assert "follows drawer_slide_1 only; drawer_slide_2 stay independent inputs" in line


def test_the_lint_does_not_mistake_hand_named_joints_for_instances(tmp_path):
    """hinge_1 / hinge_2 written by an agent are two joints, not one instanced driver."""
    from codeverse3d.workspace import Workspace

    urdf = (RIB.replace('joint name="runner_slide"', 'joint name="runner_slide_1"')
               .replace('<mimic joint="runner_slide" ', '<mimic joint="runner_slide_1" ')
               .replace("</robot>", EXTRA_RUNNER))
    ws = Workspace(tmp_path / "ws").create()
    (ws.src / "model.py").write_text("import bpy\n")
    (ws.src / "robot.urdf").write_text(urdf)

    findings = lint_workspace(ws).findings
    assert not [f for f in findings if "independent inputs" in f.message]
    assert not [f for f in findings if "mimic" in f.message and f.severity.value == "error"]


def test_the_exported_pose_glb_moves_the_followers_like_fk_does(tmp_path):
    """urdf_to_glb poses followers through fk, like the sweep (it used to leave them at rest)."""
    import numpy as np
    import trimesh

    from codeverse3d.spatial.joints_export import ZUP_TO_YUP, urdf_to_glb

    robot = _robot(tmp_path)
    q = {"runner_slide": 0.3}

    def rib_a_world(glb):
        scene = trimesh.load(glb, force="scene")
        node = next(n for n in scene.graph.nodes_geometry if scene.graph[n][1] == "rib_a")
        T, _ = scene.graph[node]
        return trimesh.transform_points([scene.geometry["rib_a"].centroid], T)[0]

    rest = rib_a_world(urdf_to_glb(robot, tmp_path / "rest.glb", {}))
    posed = rib_a_world(urdf_to_glb(robot, tmp_path / "posed.glb", q))
    assert not np.allclose(rest, posed), "rib_a did not follow runner_slide in the exported scene"
    # and it lands exactly where fk puts that point: undo the export's Y-up, move the rest
    # centroid by fk's rib_a motion for the same q, re-apply Y-up
    T0, T1 = fk(robot, {})["rib_a"], fk(robot, q)["rib_a"]
    rest_zup = trimesh.transform_points([rest], np.linalg.inv(ZUP_TO_YUP))[0]
    moved_zup = trimesh.transform_points([rest_zup], T1 @ np.linalg.inv(T0))[0]
    expected = trimesh.transform_points([moved_zup], ZUP_TO_YUP)[0]
    assert np.allclose(posed, expected, atol=1e-6), (posed, expected)


def test_a_self_mimic_says_it_names_itself(tmp_path):
    text = RIB.replace('<mimic joint="runner_slide" multiplier="4" offset="0"/>',
                       '<mimic joint="rib_a_hinge" multiplier="1" offset="0"/>')
    with pytest.raises(UrdfError, match="names itself"):
        _robot(tmp_path, text)


def test_a_self_joint_says_which_links_collided():
    """parent == child names both sides (pydantic truncated the value; models re-asked blind)."""
    from codeverse3d.contracts.plan import JointPlan

    common = {"type": "revolute", "axis": [0, 0, 1], "pivot": [0, 0, 0],
              "lower": 0.0, "upper": 1.0, "rest": 0.0}
    with pytest.raises(Exception, match="parent and child are both 'Sash'"):
        JointPlan.model_validate({"name": "SashHinge", "parent": "Sash", "child": "Sash", **common})
    with pytest.raises(Exception, match="same name once normalised"):
        JointPlan.model_validate({"name": "SashHinge", "parent": "SashFrame", "child": "sash_frame",
                                  **common})
