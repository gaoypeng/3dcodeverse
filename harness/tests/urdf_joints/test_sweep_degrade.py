"""A pose-render timeout inside the joint sweep degrades to a WARN, it does not fail the run."""

from __future__ import annotations

from types import SimpleNamespace

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.spatial.render import RenderError


def test_pose_render_failure_keeps_the_measured_sweep(tmp_path, monkeypatch):
    """Regression: a render_glb timeout inside render_poses made gates() raise and failed the run before round 0."""
    import codeverse3d.tracks.articulated_object as art
    from codeverse3d.contracts.artifacts import GateReport
    from codeverse3d.spatial import joints_export, joints_sweep

    ws = SimpleNamespace(artifacts=tmp_path / "artifacts", src=tmp_path / "src")
    monkeypatch.setattr(joints_sweep, "sweep_gate", lambda ws_: (GateReport.of(joints_sweep.SWEEP_GATE), "robot"))

    def boom(robot, out_dir):
        raise RenderError("render_glb failed: node script render_glb.mjs timed out after 330s")
        yield  # pragma: no cover

    monkeypatch.setattr(joints_export, "render_poses", boom)
    gate, views = art.default_joint_sweep(ws, None, tmp_path / "poses")
    assert gate.passed, "the measured sweep had no errors; a render timeout is not a collision"
    assert views == []
    warns = [f for f in gate.findings if f.severity == Severity.WARN]
    assert warns and "pose renders failed" in warns[0].message
