"""Collision sweep: rest penetration, floating detection, weld policy, finding aggregation."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import GateFinding, Severity
from codeverse3d.spatial.joints_model import load_urdf
from codeverse3d.spatial.joints_poses import pose_samples
from codeverse3d.spatial.joints_sweep import (
    MAX_PAIR_FINDINGS,
    aggregate_findings,
    sweep_collisions,
    sweep_findings,
)
from tests.urdf_joints.conftest import (
    write_carcass_drawer_robot,
    write_mesh_robot,
)


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


@pytest.mark.parametrize("fcl", [True, False], ids=["fcl", "trimesh-fallback"])
def test_agent_style_meshes_penetration_is_real_and_deterministic(tmp_path, monkeypatch, fcl):
    """Non-watertight, inverted-winding links (the wrapper's usual output), both backends, deterministic."""
    import codeverse3d.spatial.joints_collide as jc

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
    """A weld overlap is one WARN under the rest policy, not an ERROR per swung pose."""
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
    from codeverse3d.spatial import tools as ts

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


# ------------------------------------------------------------------ sweep aggregation
def _pen(a, b, depth, pose, sev=Severity.ERROR):
    return GateFinding(gate="articulation", severity=sev, target=f"{a}|{b}", message=f"{a}/{b} {depth}",
                       fix_hint="shrink", data={"kind": "penetration", "pose": pose, "depth_m": depth})


def test_aggregate_merges_ranks_caps_and_summarises():
    fs = [_pen("door", "wall", 0.004, {"hinge": 0.5}), _pen("door", "wall", 0.031, {"hinge": 1.5}),
          _pen("door", "wall", 0.002, {}), _pen("lid", "box", 0.010, {"lid_j": 1.0}),
          GateFinding(gate="articulation", severity=Severity.ERROR, target="leg", message="floating",
                      data={"kind": "unattached", "pose": {}, "gap_m": 0.05})]
    out = aggregate_findings(fs)
    assert [f.target for f in out] == ["leg", "door|wall", "lid|box"]  # gap 0.05 > 0.031 > 0.010
    dw = out[1]
    assert dw.data["n_poses"] == 3 and dw.data["max_depth_m"] == 0.031
    assert "3 of the sampled poses" in dw.message and "31.0 mm" in dw.message and "hinge=1.50" in dw.message
    assert "1 at rest" in dw.message
    assert all(f.severity == Severity.ERROR for f in out)
    # errors are capped; the rest is one summary WARN
    fs = [_pen(f"p{i}", "base", 0.001 * (i + 1), {"j": 1.0}) for i in range(MAX_PAIR_FINDINGS + 3)]
    out = aggregate_findings(fs)
    errors = [f for f in out if f.severity == Severity.ERROR]
    warns = [f for f in out if f.severity == Severity.WARN]
    assert len(errors) == MAX_PAIR_FINDINGS and errors[0].target == f"p{MAX_PAIR_FINDINGS + 2}|base"
    assert len(warns) == 1 and warns[0].data["kind"] == "penetration_summary" and "3 more" in warns[0].message
