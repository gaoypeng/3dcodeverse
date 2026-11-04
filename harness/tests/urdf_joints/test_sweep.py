"""Collision sweep: rest penetration, floating detection, weld policy, finding aggregation."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import GateFinding, Severity
from codeverse3d.spatial.joints_model import load_urdf
from codeverse3d.spatial.joints_sweep import (
    aggregate_findings,
    sweep_collisions,
    sweep_findings,
)
from tests.urdf_joints.conftest import (
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
