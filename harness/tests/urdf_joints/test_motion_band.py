"""The motion gate's middle band: leaning along the planned direction is a WARN, not WRONG."""

from __future__ import annotations

from types import SimpleNamespace

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.joints import load_urdf, motion_direction_check
from codeverse.spatial.joints_sweep import OK_COS, PARTIAL_COS
from tests.urdf_joints.conftest import write_mesh_robot


def test_flags_follow_the_cosine_bands(tmp_path):
    urdf, meshes = write_mesh_robot(tmp_path)
    r = load_urdf(urdf, meshes)
    seen = set()
    for expected in ("front", "back", "left", "right", "up", "down"):
        chk = motion_direction_check(r, "hinge", expected)
        assert chk.ok == (chk.cos > OK_COS)
        assert chk.partial == ((not chk.ok) and chk.cos > PARTIAL_COS)
        seen.add("ok" if chk.ok else "partial" if chk.partial else "wrong")
        if chk.partial:
            assert "MOSTLY" in chk.message and "WRONG" not in chk.message
        elif not chk.ok:
            assert "WRONG" in chk.message
    assert "ok" in seen and "wrong" in seen  # the door swings to the front-left: both extremes are exercised


def test_partial_is_a_warn_in_the_gate(tmp_path, cabinet_plan, monkeypatch):
    from codeverse.spatial import joints as sj
    from codeverse.tracks import articulated_object as ao

    urdf, meshes = write_mesh_robot(tmp_path / "artifacts")
    ws = SimpleNamespace(artifacts=tmp_path / "artifacts", src=tmp_path / "src")

    def leaning(robot, joint, expected, *, probe=None):
        return sj.MotionCheck(joint=joint, expected=expected, observed_dir=(0.0, -0.4, 0.9), ok=False, partial=True,
                              cos=0.4, message=f"{joint}: MOSTLY along {expected} (cos 0.40)")

    monkeypatch.setattr(sj, "motion_direction_check", leaning)
    rep = ao.default_motion_checks(ws, cabinet_plan)
    assert rep is not None and rep.passed  # a WARN never fails the gate
    f = [x for x in rep.findings if x.target == "hinge"][0]
    assert f.severity == Severity.WARN and f.data["cos"] == 0.4 and "keep the axis sign" in f.fix_hint
