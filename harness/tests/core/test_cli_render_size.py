"""SM-09: `3dcv render` dropped render.width / height / scene_width / scene_height.

The four settings exist (config.Render), are accepted from YAML and CV3D_RENDER__*,
and are honoured by the IN-RUN renders (tracks/static_object.py passes r.width/r.height,
tracks/scene.py passes r.scene_width/r.scene_height) — but the one command whose whole
job is rendering hardcoded render.py's 768x768 and render_scene.py's 1024x576, so a CLI
render did not match the one the judge saw.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import codeverse.spatial.render as R
import codeverse.spatial.render_scene as RS
from codeverse.cli.main import app
from codeverse.contracts.artifacts import RenderSet
from codeverse.contracts.common import Backends, Language, Track
from codeverse.contracts.spec import Spec
from codeverse.workspace import Workspace

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """render_glb memoizes into <cache_dir>/renders, so without this the fake renders
    land in (and are served from) the developer's real ~/.cache/codeverse."""
    from codeverse.config import get_settings

    monkeypatch.setenv("CV3D_CACHE_DIR", str(tmp_path / "cache"))
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


def _run(tmp_path: Path, language: Language, track: Track) -> Path:
    runs = tmp_path / "runs"
    ws = Workspace(runs / "r1").create()
    ws.write_json(ws.spec_path, Spec(id="r1", track=track, language=language, prompt="a stool",
                                     backends=Backends(generator="api-agent:gemini:gemini-3.7-flash")))
    (ws.artifacts / "object.glb").write_bytes(b"glTF\x02\x00\x00\x00")
    return runs


def test_object_render_uses_the_configured_size(tmp_path, captured, monkeypatch):
    runs = _run(tmp_path, Language.BLENDER, Track.STATIC_OBJECT)
    monkeypatch.setenv("CV3D_RENDER__WIDTH", "1600")
    monkeypatch.setenv("CV3D_RENDER__HEIGHT", "1200")
    from codeverse.config import get_settings

    get_settings.cache_clear()
    res = runner.invoke(app, ["render", "r1", "--runs-dir", str(runs)])

    assert res.exit_code == 0, res.output
    assert (captured.get("width"), captured.get("height")) == (1600, 1200)


def test_explicit_flags_win_over_the_setting(tmp_path, captured):
    runs = _run(tmp_path, Language.BLENDER, Track.STATIC_OBJECT)

    res = runner.invoke(app, ["render", "r1", "--runs-dir", str(runs), "--width", "320", "--height", "240"])

    assert res.exit_code == 0, res.output
    assert (captured.get("width"), captured.get("height")) == (320, 240)


def test_the_default_is_still_the_settings_default(tmp_path, captured):
    runs = _run(tmp_path, Language.BLENDER, Track.STATIC_OBJECT)

    res = runner.invoke(app, ["render", "r1", "--runs-dir", str(runs)])

    assert res.exit_code == 0, res.output
    assert (captured.get("width"), captured.get("height")) == (768, 768)


def test_scene_render_uses_the_scene_size(tmp_path, captured, monkeypatch):
    runs = _run(tmp_path, Language.SCENE_THREEJS, Track.SCENE)
    monkeypatch.setenv("CV3D_RENDER__SCENE_WIDTH", "1920")
    monkeypatch.setenv("CV3D_RENDER__SCENE_HEIGHT", "1080")
    from codeverse.config import get_settings

    get_settings.cache_clear()
    res = runner.invoke(app, ["render", "r1", "--runs-dir", str(runs)])

    assert res.exit_code == 0, res.output
    assert (captured.get("width"), captured.get("height")) == (1920, 1080)
