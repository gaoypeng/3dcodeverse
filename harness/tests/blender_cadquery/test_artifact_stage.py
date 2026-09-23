"""ArtifactStage: a canonical artifact exists only when the build that owns it promoted it."""

from __future__ import annotations

from codeverse3d.workspace import Workspace


def _seed(ws: Workspace) -> None:
    (ws.artifacts / "build.json").write_text('{"ok": true}')
    (ws.artifacts / "object.glb").write_bytes(b"old glb")
    meshes = ws.artifacts / "meshes"
    meshes.mkdir(exist_ok=True)
    (meshes / "old.glb").write_bytes(b"old mesh")


def test_enter_invalidates_immediately_and_exit_discards(tmp_ws: Workspace) -> None:
    _seed(tmp_ws)
    with tmp_ws.stage_artifacts("build.json", "object.glb", "meshes") as stage:
        # the previous round's outputs are gone BEFORE any work (or early return) happens
        assert not (tmp_ws.artifacts / "build.json").exists()
        assert not (tmp_ws.artifacts / "object.glb").exists()
        assert not (tmp_ws.artifacts / "meshes").exists()
        assert stage.staging_dir.is_dir() and stage.staging_dir.parent.name == ".staging"
        stage.path("object.glb").write_bytes(b"half built")  # never promoted
    # exit without promote: canonical stays absent, staging discarded
    assert not (tmp_ws.artifacts / "object.glb").exists()
    staging_root = tmp_ws.artifacts / ".staging"
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_default_promote_skips_names_never_written(tmp_ws: Workspace) -> None:
    with tmp_ws.stage_artifacts("build.json", "object.stl") as stage:
        stage.path("build.json").write_text('{"ok": true}')
        published = stage.promote()  # object.stl was never staged → skipped, not an error
    assert [p.name for p in published] == ["build.json"]
    assert not (tmp_ws.artifacts / "object.stl").exists()


def test_promote_replaces_an_existing_dir(tmp_ws: Workspace) -> None:
    meshes = tmp_ws.artifacts / "meshes"
    meshes.mkdir()
    (meshes / "old.glb").write_bytes(b"old")
    stage = tmp_ws.stage_artifacts("meshes")  # standalone (no enter → no invalidation)
    stage.path("meshes").mkdir()
    (stage.path("meshes") / "new.glb").write_bytes(b"new")
    stage.promote()
    stage.discard()
    assert [p.name for p in meshes.iterdir()] == ["new.glb"]  # old members do not survive
