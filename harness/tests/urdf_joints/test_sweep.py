"""Collision sweep: good design clean, bad pivot penetrates, floating detection, motion direction."""

from __future__ import annotations

import pytest

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.joints import (
    UrdfError,
    load_urdf,
    motion_direction_check,
    pose_samples,
    summary_text,
    sweep_collisions,
    sweep_findings,
)
from tests.urdf_joints.conftest import (
    write_carcass_drawer_robot,
    write_mesh_robot,
    write_prims_robot,
)


def test_good_design_has_no_overlaps(tmp_path):
    r = load_urdf(write_prims_robot(tmp_path / "cab.urdf", axis_z=-1))
    rep = sweep_collisions(r, pose_samples(r))
    assert rep.summary.max_penetration_m == 0.0
    assert rep.summary.floating_links == []
    assert rep.per_pose[0].n_contacts == 1  # door touches body at rest
    assert "no overlaps" in summary_text(rep)
    assert sweep_findings(rep) == []


def test_bad_axis_swings_door_into_body(tmp_path):
    r = load_urdf(write_prims_robot(tmp_path / "cab.urdf", axis_z=+1))
    rep = sweep_collisions(r, pose_samples(r))
    assert rep.summary.rest_max_penetration_m == 0.0
    assert rep.summary.max_penetration_m > 0.1
    assert set(rep.summary.overlapping_poses) == {"hinge@mid", "hinge@upper"}
    assert rep.summary.worst_pair == ("body", "door")
    f = sweep_findings(rep)
    assert all(x.severity == Severity.ERROR for x in f) and len(f) == 2
    assert "pivot" in f[0].fix_hint
    ov = rep.per_pose[-1].overlaps[0]
    assert ov.volume_m3 is not None and ov.volume_m3 > 1e-4 and not ov.approx


def test_rest_penetration_and_floating(tmp_path):
    # door 30 mm inside the body at rest (fully swallowed: FCL surface collide misses it);
    # handle (fixed child of the door) pushed 20 mm off the door face → not attached
    urdf, meshes = write_mesh_robot(tmp_path, handle=True)
    text = urdf.read_text().replace('<origin xyz="0.29 0.2 0" rpy="0 0 0"/>', '<origin xyz="0.29 0.23 0" rpy="0 0 0"/>')
    text = text.replace('<origin xyz="-0.2 0.24 -0.4" rpy="0 0 0"/>', '<origin xyz="-0.2 0.22 -0.4" rpy="0 0 0"/>')
    urdf.write_text(text)
    r = load_urdf(urdf, meshes)
    rep = sweep_collisions(r, [{}])
    assert 0.028 < rep.summary.rest_max_penetration_m < 0.032
    assert rep.summary.floating_at_rest == ["handle"]
    fl = rep.per_pose[0].floating[0]
    # attachment is judged against the PARENT link (door), not the grounded body
    assert fl.nearest == "door" and fl.joint_type == "fixed" and 0.03 < fl.gap_m < 0.06
    sev = {x.target: x.severity for x in sweep_findings(rep)}
    assert sev["body|door"] == Severity.ERROR and sev["handle"] == Severity.ERROR


def test_hinged_child_near_parent_is_only_a_warning(tmp_path):
    # door hinged 6 mm in front of the body face: within 3x hinge clearance → WARN, not ERROR
    urdf, meshes = write_mesh_robot(tmp_path, handle=False)
    text = urdf.read_text().replace('<origin xyz="0.29 0.2 0" rpy="0 0 0"/>', '<origin xyz="0.29 0.194 0" rpy="0 0 0"/>')
    urdf.write_text(text)
    r = load_urdf(urdf, meshes)
    rep = sweep_collisions(r, [{}], hinge_clearance_m=0.004)
    assert rep.summary.floating_at_rest == ["door"]
    fl = rep.per_pose[0].floating[0]
    assert fl.nearest == "body" and fl.joint_type == "revolute" and 0.004 < fl.gap_m < 0.008
    finds = sweep_findings(rep, hinge_clearance_m=0.004)
    assert finds[-1].target == "door" and finds[-1].severity == Severity.WARN
    # an inserted prismatic child (AABB overlap) is never reported as unattached
    rep_ok = sweep_collisions(load_urdf(*write_mesh_robot(tmp_path / "ok")), [{}])
    assert rep_ok.summary.floating_at_rest == []


def test_motion_direction(tmp_path):
    r = load_urdf(write_prims_robot(tmp_path / "cab.urdf", axis_z=-1))
    ok = motion_direction_check(r, "hinge", "front")
    assert ok.ok and ok.observed_dir[1] < -0.9
    bad = motion_direction_check(r, "hinge", "back")
    assert not bad.ok and "WRONG" in bad.message
    with pytest.raises(UrdfError):
        motion_direction_check(r, "hinge", "sideways")
    with pytest.raises(UrdfError):
        motion_direction_check(r, "nope", "front")


def test_trimesh_fallback_backend(tmp_path, monkeypatch):
    import codeverse.spatial.joints_collide as jc
    import codeverse.spatial.joints_sweep as js

    monkeypatch.setattr(jc, "_fcl", None)
    r = load_urdf(write_prims_robot(tmp_path / "cab.urdf", axis_z=+1))
    rep = js.sweep_collisions(r, pose_samples(r), volumes=False)
    assert rep.summary.backend == "trimesh"
    assert rep.summary.max_penetration_m > 0.1 and rep.summary.rest_max_penetration_m == 0.0
    assert rep.summary.floating_links == []


@pytest.mark.parametrize("fcl", [True, False], ids=["fcl", "trimesh-fallback"])
def test_agent_style_meshes_penetration_is_real_and_deterministic(tmp_path, monkeypatch, fcl):
    """Non-watertight, inverted-winding links (the wrapper's usual output): a clean design
    must report no overlap and a drawer driven 30 mm through the side panel must be caught —
    identically on every call (no random ray re-casts) and on both backends."""
    import codeverse.spatial.joints_collide as jc

    if not fcl:
        monkeypatch.setattr(jc, "_fcl", None)
    clean = load_urdf(*write_carcass_drawer_robot(tmp_path / "clean"))
    assert not clean.links["Carcass"].mesh.is_watertight and clean.links["Carcass"].mesh.volume < 0
    reps = [sweep_collisions(clean, pose_samples(clean, n_random=4, seed=0), volumes=False) for _ in range(3)]
    assert all(r.summary.max_penetration_m == 0.0 and r.summary.overlapping_poses == [] for r in reps)
    assert reps[0].summary.floating_links == [] and reps[0].summary.link_islands == {"Carcass": 5, "Drawer": 5}
    assert reps[0].summary.backend == ("fcl" if fcl else "trimesh")

    bad = load_urdf(*write_carcass_drawer_robot(tmp_path / "bad", drawer_shift_x=0.032))
    rests = [sweep_collisions(bad, [{}]).summary.rest_max_penetration_m for _ in range(3)]
    assert len(set(rests)) == 1 and 0.008 < rests[0] < 0.02  # 30 mm through a 30 mm panel → ≥ 10 mm to its surface
    rep = sweep_collisions(bad, [{}])
    ov = rep.per_pose[0].overlaps
    assert [(o.a, o.b, o.approx) for o in ov] == [("Carcass", "Drawer", False)]  # islands are watertight → exact
    assert ov[0].volume_m3 is not None and ov[0].volume_m3 > 1e-6
    sev = {x.target: x.severity for x in sweep_findings(rep, rest_max_m=0.005)}
    assert sev["Carcass|Drawer"] == Severity.ERROR


def test_welded_child_overlap_is_structural_not_motion(tmp_path):
    """A handle sunk 3 mm into its door (fixed joint) never moves relative to the door:
    one WARN under the rest policy, not an ERROR repeated for every swung pose."""
    urdf, meshes = write_mesh_robot(tmp_path, handle=True)
    urdf.write_text(urdf.read_text().replace('<origin xyz="-0.2 0.24 -0.4" rpy="0 0 0"/>', '<origin xyz="-0.2 0.243 -0.4" rpy="0 0 0"/>'))
    r = load_urdf(urdf, meshes)
    rep = sweep_collisions(r, pose_samples(r))
    ovs = [(pr.label, o) for pr in rep.per_pose for o in pr.overlaps]
    assert [(lbl, o.a, o.b, o.rigid) for lbl, o in ovs] == [("rest", "door", "handle", True)]
    assert 0.0025 < rep.summary.rest_max_penetration_m < 0.0035 and rep.summary.overlapping_poses == ["rest"]
    finds = sweep_findings(rep)
    assert [(f.target, f.severity) for f in finds] == [("door|handle", Severity.WARN)]
    # without a rest pose in the sweep the weld is still measured (once, first pose) and counts as rest penetration
    rep2 = sweep_collisions(r, [{"hinge": 1.0}, {"hinge": 1.5}])
    assert [len(pr.overlaps) for pr in rep2.per_pose] == [1, 0] and rep2.summary.rest_max_penetration_m > 0.0025
    assert sweep_findings(rep2)[0].severity == Severity.WARN and "rigidly joined" in sweep_findings(rep2)[0].fix_hint


# ------------------------------------------------- joints= narrows the RENDER, not the sweep
def test_joints_argument_narrows_the_render_to_those_joints(monkeypatch, tmp_path):
    """The tool accepted joints=[...] from the start and nothing consumed it: every call
    rendered every joint's limit poses (three views each). Measured 2026-08-25: articulated
    rounds ran a median 1007 s vs 497 s for static objects, agents calling the sweep 3-8
    times a round on 10-joint objects (~63 renders a call)."""
    from codeverse.spatial import tools as ts

    class _J:
        def __init__(self, name, lo, hi, type="revolute"): self.name, self.lower, self.upper, self.type = name, lo, hi, type
    class _Robot:
        joints = {n: _J(n, -1.0, 1.0) for n in ("hinge", "slide", "knob")}
        def movable_joints(self): return list(self.joints.values())
        def independent_joints(self): return list(self.joints.values())  # no coupling in this fake

    full = ts._poses_for(_Robot(), None)
    assert full is None, "no filter = the full articulation sheet, unchanged"
    narrowed = ts._poses_for(_Robot(), ["hinge"])
    labels = [lbl for lbl, _ in narrowed]
    assert labels[0] == "rest" and all(x == "rest" or x.startswith("hinge@") for x in labels)
    assert len(narrowed) == 3, "rest + hinge@lower + hinge@upper, nothing from slide or knob"
    typo = ts._poses_for(_Robot(), ["hinge_typo"])
    assert [lbl for lbl, _ in typo] == ["rest"], "an unknown joint yields a visibly wrong sheet, not the full one"
