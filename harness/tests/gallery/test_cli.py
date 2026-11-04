"""``3dcodeverse gallery build|serve``."""

from __future__ import annotations

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
    r = runner.invoke(app, ["gallery", "build", str(gallery_tree["runs"]), "--out", str(out), "--embed"])
    assert r.exit_code == 0, r.output
    assert "data:image/jpeg;base64," in out.read_text()


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
