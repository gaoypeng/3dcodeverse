"""ArtifactStage: the stage-then-promote lifecycle every runtime's build outputs use.

The invariant under test: a canonical artifact exists ONLY when the build that owns
it finished with that artifact as its result — entering a stage invalidates the
canonical names before any early return can leak, and exit-without-promote leaves
them absent.
"""

from __future__ import annotations

import pytest

from codeverse.workspace import Workspace


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


def test_promote_publishes_files_and_dirs(tmp_ws: Workspace) -> None:
    _seed(tmp_ws)
    with tmp_ws.stage_artifacts("build.json", "object.glb", "meshes") as stage:
        stage.path("build.json").write_text('{"ok": true}')
        stage.path("object.glb").write_bytes(b"new glb")
        stage.path("meshes").mkdir()
        (stage.path("meshes") / "leg.glb").write_bytes(b"new mesh")
        published = stage.promote()
        assert {p.name for p in published} == {"build.json", "object.glb", "meshes"}
    assert (tmp_ws.artifacts / "object.glb").read_bytes() == b"new glb"
    assert (tmp_ws.artifacts / "meshes" / "leg.glb").read_bytes() == b"new mesh"
    assert not (tmp_ws.artifacts / ".staging").exists()  # fully cleaned up


def test_selective_promote_leaves_the_rest_invalidated(tmp_ws: Workspace) -> None:
    """The urdf failure paths publish build.json (the failed status) and nothing else."""
    _seed(tmp_ws)
    with tmp_ws.stage_artifacts("build.json", "object.glb") as stage:
        stage.path("build.json").write_text('{"ok": false}')
        stage.path("object.glb").write_bytes(b"fresh but unpublished")
        stage.promote("build.json")
    assert (tmp_ws.artifacts / "build.json").read_text() == '{"ok": false}'
    assert not (tmp_ws.artifacts / "object.glb").exists()


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


def test_invalidate_standalone_and_name_validation(tmp_ws: Workspace) -> None:
    _seed(tmp_ws)
    tmp_ws.stage_artifacts("build.json", "object.glb", "meshes").invalidate()
    for name in ("build.json", "object.glb", "meshes"):
        assert not (tmp_ws.artifacts / name).exists(), name
    stage = tmp_ws.stage_artifacts("build.json")
    with pytest.raises(ValueError):
        stage.path("other.json")
    with pytest.raises(ValueError):
        stage.invalidate("other.json")
    with pytest.raises(FileNotFoundError):
        stage.promote("build.json")  # a named promote of something never staged is a bug
    stage.discard()
    with pytest.raises(ValueError):
        tmp_ws.stage_artifacts()
