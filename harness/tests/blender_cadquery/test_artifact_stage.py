"""ArtifactStage: a canonical artifact exists only when the build that owns it promoted it."""

from __future__ import annotations

from codeverse3d.workspace import Workspace


def test_stage_invalidates_on_enter_discards_on_exit_and_promotes(tmp_ws: Workspace) -> None:
    (tmp_ws.artifacts / "build.json").write_text('{"ok": true}')
    (tmp_ws.artifacts / "object.glb").write_bytes(b"old glb")
    with tmp_ws.stage_artifacts("build.json", "object.glb") as stage:
        assert not (tmp_ws.artifacts / "build.json").exists() and not (tmp_ws.artifacts / "object.glb").exists()  # before any work
        stage.path("object.glb").write_bytes(b"half built")  # never promoted
    assert not (tmp_ws.artifacts / "object.glb").exists()  # exit without promote: canonical stays absent
    with tmp_ws.stage_artifacts("build.json", "object.stl") as stage:
        stage.path("build.json").write_text('{"ok": true}')
        published = stage.promote()  # object.stl was never staged: skipped, not an error
    assert [p.name for p in published] == ["build.json"]
    meshes = tmp_ws.artifacts / "meshes"   # promote replaces an existing dir
    meshes.mkdir()
    (meshes / "old.glb").write_bytes(b"old")
    stage = tmp_ws.stage_artifacts("meshes")
    stage.path("meshes").mkdir()
    (stage.path("meshes") / "new.glb").write_bytes(b"new")
    stage.promote()
    assert [p.name for p in meshes.iterdir()] == ["new.glb"]
