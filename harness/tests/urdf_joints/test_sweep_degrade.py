"""A pose-render timeout inside the joint sweep degrades to a WARN, it does not fail the run."""

from __future__ import annotations

from types import SimpleNamespace

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.render import RenderError


def test_pose_render_failure_keeps_the_measured_sweep(tmp_path, monkeypatch):
    """art_verify architect_lamp, 2026-08-26: render_glb.mjs timed out after 330 s inside
    render_poses with three articulated runs sharing the browser; gates() raised and the run
    was recorded failed before its first round."""
    import codeverse.tracks.articulated_object as art
    from codeverse.spatial import joints

    urdf = tmp_path / "src" / "robot.urdf"
    urdf.parent.mkdir(parents=True)
    urdf.write_text("<robot/>")
    ws = SimpleNamespace(artifacts=tmp_path / "artifacts", src=tmp_path / "src")
    monkeypatch.setattr(joints, "load_urdf", lambda u, meshes: "robot")
    monkeypatch.setattr(joints, "pose_samples", lambda robot: [])
    monkeypatch.setattr(joints, "sweep_collisions", lambda robot, poses: "report")
    monkeypatch.setattr(joints, "sweep_findings", lambda report: [])

    def boom(robot, out_dir):
        raise RenderError("render_glb failed: node script render_glb.mjs timed out after 330s")
        yield  # pragma: no cover

    monkeypatch.setattr(joints, "render_poses", boom)
    gate, views = art.default_joint_sweep(ws, None, tmp_path / "poses")
    assert gate.passed, "the measured sweep had no errors; a render timeout is not a collision"
    assert views == []
    warns = [f for f in gate.findings if f.severity == Severity.WARN]
    assert warns and "pose renders failed" in warns[0].message
