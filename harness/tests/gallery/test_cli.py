"""``3dcode gallery build|serve``."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codeverse3d.cli.main import app, resolve_roots

runner = CliRunner()


def test_build_writes_a_linked_page_and_embed_inlines_the_images(gallery_tree: dict[str, Path], tmp_path: Path):
    out = tmp_path / "g.html"
    r = runner.invoke(app, ["gallery", "build", str(gallery_tree["runs"]), str(gallery_tree["battery"]),
                            "--out", str(out), "--title", "batch 9"])
    assert r.exit_code == 0, r.output
    assert "gallery of 6 runs" in r.output
    markup = out.read_text()
    assert "batch 9" in markup and "wooden_chair_ab12cd34" in markup
    assert "data:image/jpeg" not in markup  # link mode by default
    r = runner.invoke(app, ["gallery", "build", str(gallery_tree["runs"]), "--out", str(out), "--embed"])
    assert r.exit_code == 0, r.output
    assert "data:image/jpeg;base64," in out.read_text()


def test_build_rejects_a_missing_root(tmp_path: Path):
    r = runner.invoke(app, ["gallery", "build", str(tmp_path / "nope"), "--out", str(tmp_path / "x.html")])
    assert r.exit_code != 0 and "not a directory" in r.output


def test_default_roots_from_cwd(gallery_tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("codeverse3d.cli._common.EVAL_ROOT", gallery_tree["root"] / "no_eval")
    monkeypatch.chdir(gallery_tree["root"])
    roots = resolve_roots(None)
    assert [r.name for r in roots] == ["runs", "runs"]
    assert str(roots[1]).endswith(os.path.join("eval", "bench", "out", "static_v9", "runs"))


def test_default_roots_find_the_eval_batteries_from_any_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """D77: the batteries live in <repo>/eval/bench/out/<b>/runs, found from any cwd."""
    repo = tmp_path / "repo"
    battery = repo / "eval" / "bench" / "out" / "b1" / "runs"
    battery.mkdir(parents=True)
    (repo / "harness").mkdir()
    monkeypatch.setattr("codeverse3d.cli._common.EVAL_ROOT", repo / "eval")
    for cwd in (repo / "harness", repo, repo / "eval"):  # each battery listed once, from anywhere
        monkeypatch.chdir(cwd)
        assert [p.resolve() for p in resolve_roots(None)] == [battery.resolve()], cwd


def test_no_roots_anywhere_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("codeverse3d.cli._common.EVAL_ROOT", tmp_path / "no_eval")
    monkeypatch.setenv("C3D_RUNS_DIR", str(tmp_path / "absent"))
    from codeverse3d.config import get_settings

    get_settings.cache_clear()
    try:
        r = runner.invoke(app, ["gallery", "build", "--out", str(tmp_path / "x.html")])
        assert r.exit_code != 0 and "no run roots found" in r.output
    finally:
        get_settings.cache_clear()
