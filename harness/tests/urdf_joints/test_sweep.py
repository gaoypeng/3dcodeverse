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
from tests.urdf_joints.conftest import write_mesh_robot, write_prims_robot


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
    import codeverse.spatial.joints_sweep as js

    monkeypatch.setattr(js, "_fcl", None)
    r = load_urdf(write_prims_robot(tmp_path / "cab.urdf", axis_z=+1))
    rep = js.sweep_collisions(r, pose_samples(r), volumes=False)
    assert rep.summary.backend == "trimesh"
    assert rep.summary.max_penetration_m > 0.1 and rep.summary.rest_max_penetration_m == 0.0
    assert rep.summary.floating_links == []
