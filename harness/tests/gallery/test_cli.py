"""``3dcv gallery build|serve`` and the ``3dcv flywheel gallery`` alias."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeverse.cli.main import app, resolve_roots

runner = CliRunner()


def test_gallery_group_is_registered():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and "gallery" in r.output
    r = runner.invoke(app, ["gallery", "--help"])
    assert r.exit_code == 0 and "serve" in r.output and "build" in r.output
    r = runner.invoke(app, ["gallery", "serve", "--help"])
    assert r.exit_code == 0
    for flag in ("--port", "--host", "--reload", "--open"):
        assert flag in r.output, flag


def test_build_writes_a_page(gallery_tree: dict[str, Path], tmp_path: Path):
    out = tmp_path / "g.html"
    r = runner.invoke(app, ["gallery", "build", str(gallery_tree["runs"]), str(gallery_tree["battery"]),
                            "--out", str(out), "--title", "batch 9"])
    assert r.exit_code == 0, r.output
    assert "gallery of 6 runs" in r.output
    markup = out.read_text()
    assert "batch 9" in markup and "wooden_chair_ab12cd34" in markup
    assert "data:image/jpeg" not in markup  # link mode by default


def test_build_embed(gallery_tree: dict[str, Path], tmp_path: Path):
    out = tmp_path / "e.html"
    r = runner.invoke(app, ["gallery", "build", str(gallery_tree["runs"]), "--out", str(out), "--embed"])
    assert r.exit_code == 0, r.output
    assert "data:image/jpeg;base64," in out.read_text()


def test_build_rejects_a_missing_root(tmp_path: Path):
    r = runner.invoke(app, ["gallery", "build", str(tmp_path / "nope"), "--out", str(tmp_path / "x.html")])
    assert r.exit_code != 0 and "not a directory" in r.output


def test_flywheel_gallery_alias_still_works(gallery_tree: dict[str, Path], tmp_path: Path):
    out = tmp_path / "alias.html"
    r = runner.invoke(app, ["flywheel", "gallery", str(gallery_tree["runs"]), str(out), "--title", "batch 1"])
    assert r.exit_code == 0, r.output
    assert "gallery of 2 runs" in r.output
    markup = out.read_text()
    assert "batch 1" in markup
    assert "data:image/jpeg;base64," in markup  # the alias keeps the old self-contained behaviour


def test_default_roots_from_cwd(gallery_tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(gallery_tree["root"])
    roots = resolve_roots(None)
    assert [r.name for r in roots] == ["runs", "runs"]
    assert str(roots[1]).endswith(os.path.join("bench", "out", "static_v9", "runs"))


def test_no_roots_anywhere_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CV3D_RUNS_DIR", str(tmp_path / "absent"))
    from codeverse.config import get_settings

    get_settings.cache_clear()
    try:
        r = runner.invoke(app, ["gallery", "build", "--out", str(tmp_path / "x.html")])
        assert r.exit_code != 0 and "no run roots found" in r.output
    finally:
        get_settings.cache_clear()
