"""The render CLI honors configured dimensions and explicit overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import codeverse3d.spatial.render as R
import codeverse3d.spatial.render_scene as RS
from codeverse3d.cli.main import app
from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.contracts.common import Backends, Language, Track
from codeverse3d.contracts.spec import Spec
from codeverse3d.workspace import Workspace

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """render_glb memoizes into <cache_dir>/renders, so without this the fake renders
    land in (and are served from) the developer's real ~/.cache/codeverse3d."""
    from codeverse3d.config import get_settings

    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def captured(monkeypatch) -> dict[str, Any]:
    """Intercept the low-level renderer; nothing here needs node."""
    seen: dict[str, Any] = {}

    def fake_run_render(glb, out_dir, params, *, gpu, timeout_s):
        seen.update(params)
        return {"views": [], "renderer": "fake", "duration_ms": 1}

    def fake_scene(ws, out_dir, **kw):
        seen.update(kw)
        return RenderSet(views=[], renderer="fake")

    monkeypatch.setattr(R, "_run_render", fake_run_render)
    monkeypatch.setattr(RS, "render_scene", fake_scene)
    return seen


def _run(runs: Path, slug: str, language: Language, track: Track) -> None:
    ws = Workspace(runs / slug).create()
    ws.write_json(ws.spec_path, Spec(id=slug, track=track, language=language, prompt="a stool",
                                     backends=Backends(generator="gemini-cli:gemini-3.6-flash")))
    (ws.artifacts / "object.glb").write_bytes(b"glTF\x02\x00\x00\x00")


def test_render_uses_the_configured_object_and_scene_sizes_and_explicit_flags_win(tmp_path, captured, monkeypatch):
    runs = tmp_path / "runs"
    _run(runs, "obj", Language.BLENDER, Track.STATIC_OBJECT)
    _run(runs, "scn", Language.SCENE_THREEJS, Track.SCENE)
    for name, value in (("WIDTH", "1600"), ("HEIGHT", "1200"), ("SCENE_WIDTH", "1920"), ("SCENE_HEIGHT", "1080")):
        monkeypatch.setenv(f"C3D_RENDER__{name}", value)
    from codeverse3d.config import get_settings

    get_settings.cache_clear()
    res = runner.invoke(app, ["render", "obj", "--runs-dir", str(runs)])
    assert res.exit_code == 0, res.output
    assert (captured.get("width"), captured.get("height")) == (1600, 1200)
    res = runner.invoke(app, ["render", "obj", "--runs-dir", str(runs), "--width", "320", "--height", "240"])
    assert res.exit_code == 0, res.output
    assert (captured.get("width"), captured.get("height")) == (320, 240)
    res = runner.invoke(app, ["render", "scn", "--runs-dir", str(runs)])
    assert res.exit_code == 0, res.output
    assert (captured.get("width"), captured.get("height")) == (1920, 1080)
