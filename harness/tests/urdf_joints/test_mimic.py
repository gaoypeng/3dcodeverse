"""Coupled mechanisms: a <mimic> joint follows the joint it names, everywhere.

compare_art_v3/v4: umbrella, scissor_mirror and folding_workbench are the lowest scorers
on the battery, and all three are one-input mechanisms with many moving links. Before
this the sweep drove every link independently and posed them in states the mechanism
cannot reach."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.contracts.plan import ArticulatedPlan
from codeverse.languages.urdf import lint_workspace, render_urdf
from codeverse.spatial.joints import (
    UrdfError,
    load_urdf,
    motion_direction_check,
    pose_samples,
    sweep_collisions,
)
from codeverse.spatial.joints_model import fk, resolve_q
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


def test_pose_samples_drive_only_the_degrees_of_freedom(tmp_path):
    r = _robot(tmp_path)
    poses = pose_samples(r)
    assert all(set(p) <= {"runner_slide"} for p in poses), poses
    assert any(p.get("runner_slide") == pytest.approx(0.3) for p in poses)
    # the ribs still move: the sweep sees the coupled pose, not a frozen one
    opened = [p for p in poses if p.get("runner_slide")][0]
    assert fk(r, opened)["rib_a"][:3, :3].tolist() != fk(r, {})["rib_a"][:3, :3].tolist()


def test_the_contact_sheet_and_joint_narrowing_show_only_input_joints(tmp_path):
    """`limit_poses` feeds the articulation sheet and `joint_sweep(joints=...)`: a tile for
    a driven joint would be the rest pose with a label saying it moved."""
    from codeverse.spatial.joints_model import fk
    from codeverse.spatial.joints_poses import limit_poses

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


def test_the_skeleton_writes_the_plan_s_coupling(cabinet_plan):
    from codeverse.contracts.plan import MimicPlan
    from codeverse.languages.urdf import compute_urdf_frames

    plan = cabinet_plan.model_copy(deep=True)
    plan.joints[0].mimic = MimicPlan(joint="handle_mount", multiplier=2.0, offset=0.1)
    plan.joints[1].type = "revolute"
    plan.joints[1].lower, plan.joints[1].upper, plan.joints[1].rest = 0.0, 1.0, 0.0
    urdf = render_urdf(compute_urdf_frames(plan))
    assert '<mimic joint="handle_mount" multiplier="2" offset="0.1"/>' in urdf


def test_the_lint_accepts_a_real_coupling_and_names_a_broken_one(tmp_path, monkeypatch):
    from codeverse.workspace import Workspace

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

    from codeverse.contracts.common import Track
    from codeverse.tracks.planner import plan_example

    d = copy.deepcopy(plan_example(Track.ARTICULATED_OBJECT))
    d["parts"].append({"name": "Lid", "role": "top lid", "description": "a flat lid", "attach_to": "Cabinet",
                       "bbox": {"center": [0, 0, 0.61], "extents": [0.4, 0.5, 0.02]}, "material": "oak"})
    d["joints"].append({"name": "LidHinge", "type": "revolute", "parent": "Cabinet", "child": "Lid",
                        "axis": [1, 0, 0], "pivot": [0, 0.21, 0.6], "lower": 0.0, "upper": 1.2, "rest": 0.0,
                        "motion": "the lid tilts up"})
    d["joints"][1].update(joint_overrides)
    return d


def test_a_declared_coupling_validates_and_survives_the_round_trip():
    d = _plan(mimic={"joint": "DrawerSlide", "multiplier": 2.0, "offset": 0.1})
    plan = ArticulatedPlan.model_validate(d)
    assert plan.joints[1].mimic is not None and plan.joints[1].mimic.joint == "DrawerSlide"
    assert plan.joints[1].mimic.multiplier == 2.0 and plan.joints[1].mimic.offset == 0.1


@pytest.mark.parametrize("mimic,msg", [
    ({"joint": "NoSuchJoint"}, "is not a joint in this plan"),
    ({"joint": "LidHinge"}, "mimics itself"),
    ({"joint": "DrawerSlide", "multiplier": 0.0}, "multiplier 0"),
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


def test_a_mimic_of_an_instanced_driver_follows_the_first_instance():
    """An instanced driver exists only as ``<name>_1..._n``; writing the plan's bare name
    made the skeleton fail its own lint and `load_urdf` (a scissor/pantograph plan is the
    likely place to hit it)."""
    from codeverse.languages.urdf import compute_urdf_frames

    d = _plan()
    d["parts"][1]["instances"] = 2                      # the drawer, driven by DrawerSlide
    d["joints"][1]["mimic"] = {"joint": "DrawerSlide", "multiplier": 0.5}
    urdf = render_urdf(compute_urdf_frames(ArticulatedPlan.model_validate(d)))
    assert '<mimic joint="drawer_slide_1" multiplier="0.5" offset="0"/>' in urdf
    assert "drawer_slide_1" in urdf and "drawer_slide_2" in urdf
    names = {ln.split('"')[1] for ln in urdf.splitlines() if "<joint name=" in ln}
    refs = {ln.split('"')[1] for ln in urdf.splitlines() if "<mimic joint=" in ln}
    assert refs <= names, f"dangling mimic reference: {refs - names}"


# ------------------------------------------------------------------ one rule set, three layers
def test_the_three_layers_reject_the_same_near_zero_multiplier(tmp_path):
    """A multiplier of 1e-10 is a coupling that transmits nothing.

    The plan validator refused it (< 1e-9) while the loader and the lint accepted it
    (< 1e-12), so a coupling the planner could not write was one the URDF path took.
    All three read ``MIMIC_MIN_MULTIPLIER`` now.
    """
    from codeverse.contracts.common import MIMIC_MIN_MULTIPLIER
    from codeverse.workspace import Workspace

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
    """``rib_a`` follows ``rib_b`` follows ``rib_a``: no single <mimic> element is wrong,
    only the graph is.  The lint checked one joint at a time and passed this file; the
    loader then refused it, so the failure landed after the build instead of before."""
    from codeverse.workspace import Workspace

    ws = Workspace(tmp_path / "ws").create()
    (ws.src / "robot.urdf").write_text(RIB.replace('<mimic joint="runner_slide" multiplier="4" offset="0"/>',
                                                   '<mimic joint="rib_b_hinge" multiplier="1" offset="0"/>'))
    (ws.src / "model.py").write_text("import bpy\n")
    cycles = [f for f in lint_workspace(ws).findings if "cycle" in f.message]
    assert cycles and {f.target for f in cycles} == {"rib_a_hinge", "rib_b_hinge"}


def test_an_unknown_target_is_reported_once_not_once_per_follower():
    """The chain walk exists to find cycles.  Every joint it walks through is itself in
    the set and reports its own broken target, so the walk must not report it again."""
    from codeverse.contracts.common import MimicSpec, mimic_issues

    issues = mimic_issues([
        MimicSpec(key="a", name="a", movable=True, target="ghost"),
        MimicSpec(key="b", name="b", movable=True, target="a"),
    ])
    assert [(i.joint, i.kind) for i in issues] == [("a", "unknown_target")]


def test_the_lint_warns_when_a_coupling_drives_past_the_follower_s_own_limits(tmp_path):
    """``rib_a_hinge`` is limited to 1.2 and follows a 0–0.3 slide at multiplier 4 — the
    coupling reaches exactly 1.2, so the file that ships is silent.  At multiplier 5 it
    reaches 1.5 and the limits and the multiplier disagree; the sweep then poses the joint
    where its own <limit> says it cannot go."""
    from codeverse.workspace import Workspace

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


def test_the_lint_says_when_only_the_first_instance_of_a_driver_is_followed(tmp_path):
    """The skeleton binds a coupling to ``<driver>_1``, so with an INSTANCED driver the
    other instances stay independent inputs and the sweep drives them separately.  The
    emitter's comment said so; the file that ships did not, so the over-driving was silent.
    """
    from codeverse.workspace import Workspace

    urdf = (RIB.replace('joint name="runner_slide"', 'joint name="runner_slide_1"')
               .replace('<mimic joint="runner_slide" ', '<mimic joint="runner_slide_1" ')
               .replace("</robot>", EXTRA_RUNNER))
    ws = Workspace(tmp_path / "ws").create()
    (ws.src / "model.py").write_text("import bpy\n")
    (ws.src / "robot.urdf").write_text(urdf)

    warns = [f for f in lint_workspace(ws).findings if "stay independent inputs" in f.message]
    assert len(warns) == 1 and warns[0].target == "rib_a_hinge"
    assert "runner_slide_2" in warns[0].message and warns[0].severity.value == "warn"


def test_a_self_mimic_says_it_names_itself(tmp_path):
    """The loader reported a self-coupling as "chain is a cycle", which is true of the
    graph and useless to whoever wrote ``<mimic joint="itself">``.  The shared rules have a
    ``self`` kind; the URDF renderer now has the sentence for it."""
    text = RIB.replace('<mimic joint="runner_slide" multiplier="4" offset="0"/>',
                       '<mimic joint="rib_a_hinge" multiplier="1" offset="0"/>')
    with pytest.raises(UrdfError, match="names itself"):
        _robot(tmp_path, text)


def test_a_self_joint_says_which_links_collided():
    """`parent == child` was the whole message, and pydantic truncates the offending value
    right after the joint name — so a model that wrote one had nothing to act on and
    rewrote the same joint through every re-ask (7 plan calls died that way on
    2026-09-03).  Both sides are named now, and a collision that only exists after
    normalisation says so."""
    import pytest as _pytest

    from codeverse.contracts.plan import JointPlan

    common = {"type": "revolute", "axis": [0, 0, 1], "pivot": [0, 0, 0],
              "lower": 0.0, "upper": 1.0, "rest": 0.0}
    with _pytest.raises(Exception, match="parent and child are both 'Sash'"):
        JointPlan.model_validate({"name": "SashHinge", "parent": "Sash", "child": "Sash", **common})
    with _pytest.raises(Exception, match="same name once normalised"):
        JointPlan.model_validate({"name": "SashHinge", "parent": "SashFrame", "child": "sash_frame",
                                  **common})
