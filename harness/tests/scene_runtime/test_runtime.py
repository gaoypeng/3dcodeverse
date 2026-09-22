"""SceneThreeJsRuntime: protocol conformance + build gate."""

from __future__ import annotations

import json

import pytest

from codeverse3d.contracts.common import Language
from codeverse3d.languages import get_runtime
from codeverse3d.languages.base import LanguageRuntime
from codeverse3d.languages.scene_threejs import SceneThreeJsRuntime
from codeverse3d.prompts.catalog import language_text
from tests.scene_runtime.conftest import needs_browser


def test_runtime_registered_conforms_and_writes_skeleton(ws):
    rt = get_runtime(Language.SCENE_THREEJS)
    assert isinstance(rt, SceneThreeJsRuntime)
    assert isinstance(rt, LanguageRuntime)
    assert rt.language == Language.SCENE_THREEJS
    assert "src/scene.js" in rt.entry_globs and "src/zones/*.js" in rt.entry_globs
    doc = language_text(rt.language, "contract.md")
    assert "createScene" in doc and "update(t, dt)" in doc
    assert "`#include <...>` alone on its line" in language_text(rt.language, "glsl_cookbook.md")
    paths = rt.skeleton(ws, None)
    assert (ws.src / "scene.js") in paths


@pytest.mark.node
@needs_browser
def test_build_ok_on_example(starter_ws):
    rt = SceneThreeJsRuntime()
    res = rt.build(starter_ws)
    assert res.ok, res.stdout_tail
    assert res.language == "scene_threejs" and res.glb_path is None
    assert res.census["totals"]["meshes"] > 10
    assert (starter_ws.artifacts / "build.json").is_file() and (starter_ws.artifacts / "census.json").is_file()
    assert json.loads((starter_ws.artifacts / "gates" / "shader_preflight.json").read_text())["passed"]


@pytest.mark.node
@needs_browser
def test_build_fails_with_file_line_on_shader_error(starter_ws):
    # the pond's water shader: a file the example scene compiles (the starter's sky comes
    # from lib/environment.js worldShell since D71)
    p = starter_ws.src / "shaders" / "water.js"
    text = p.read_text().replace("float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0);", "float fres = pow(1.0 - max(dot(N, V), 0.0), 3.0) + nope;")
    assert "nope" in text
    p.write_text(text)
    res = SceneThreeJsRuntime().build(starter_ws)
    assert not res.ok
    assert res.error_file == "src/shaders/water.js" and res.error_line
    assert "nope" in res.error_message
    assert "shader_preflight: FAILED" in res.stdout_tail


@pytest.mark.node
@needs_browser
def test_build_fails_on_import_error(starter_ws):
    (starter_ws.src / "scene.js").write_text("import { x } from './nope.js';\nexport function createScene() {}\n")
    res = SceneThreeJsRuntime().build(starter_ws)
    assert not res.ok and res.error_file == "src/scene.js" and "nope.js" in res.error_message


def test_build_interprets_combined_summary_offline(ws, monkeypatch):
    """One probe boot yields both gate reports, including the no-boot shape."""
    import codeverse3d.languages.scene_threejs as rt_mod
    from codeverse3d.spatial.render_scene import NodeResult

    calls: list[list[str]] = []

    def fake_run(script, args, **kw):
        calls.append([script, *args])
        return NodeResult(0, "", "", {
            "ok": True,
            "boot": {"ok": True, "stage": "ready", "cameras": [{"name": "a"}], "camera_problems": []},
            "update_ok": True, "census": {"totals": {"meshes": 3, "lights": 1}},
            "console_errors": [], "console_warnings": [], "shader_errors": [],
            "shader_report": {"ok": False, "errors": [
                {"file": "src/shaders/x.js", "line": 7, "kind": "compile", "message": "fragment shader: bad", "fix_hint": "fix it"},
            ], "warnings": [], "compile": {"ms": 3, "programs": 4, "custom_materials": 1}, "duration_ms": 9},
        }, 5)

    import codeverse3d.spatial.render_scene as rs_mod
    monkeypatch.setattr(rs_mod, "run_scene_script", fake_run)
    res = rt_mod.SceneThreeJsRuntime().build(ws)
    assert len(calls) == 1 and calls[0][0] == "probe_scene.mjs" and "--compile" in calls[0]
    assert not res.ok  # shader gate failed
    assert res.error_file == "src/shaders/x.js" and res.error_line == 7
    import json as _json
    probe = _json.loads((ws.artifacts / "gates" / "scene_probe.json").read_text())
    shaders = _json.loads((ws.artifacts / "gates" / "shader_preflight.json").read_text())
    assert probe["gate"] == "scene_probe" and probe["passed"]
    assert shaders["gate"] == "shader_preflight" and not shaders["passed"]
    assert shaders["findings"][0]["target"] == "src/shaders/x.js:7"
    assert (ws.artifacts / "census.json").is_file()

    # boot failure → shader gate failed-empty (old two-call behaviour preserved)
    def fake_run_noboot(script, args, **kw):
        return NodeResult(1, "", "", {
            "ok": False, "boot": {"ok": False, "stage": "import", "error": "SyntaxError: x"},
            "console_errors": [], "console_warnings": [], "shader_errors": [],
            "shader_report": {"ok": False, "skipped": "scene did not boot", "errors": [], "warnings": []},
        }, 5)

    monkeypatch.setattr(rs_mod, "run_scene_script", fake_run_noboot)
    res2 = rt_mod.SceneThreeJsRuntime().build(ws)
    assert not res2.ok
    shaders2 = _json.loads((ws.artifacts / "gates" / "shader_preflight.json").read_text())
    assert not shaders2["passed"] and shaders2["findings"] == []


def test_probe_crash_leaves_no_stale_probe_outputs(ws, monkeypatch):
    """A probe crash removes prior outputs and publishes a failed build."""
    import codeverse3d.spatial.render_scene as rs_mod
    from codeverse3d.spatial.render_scene import SceneRenderError

    for name in ("census.json", "scene_probe.json", "shader_preflight.json"):
        (ws.artifacts / name).write_text('{"stale": true}')
    (ws.artifacts / "build.json").write_text('{"ok": true}')

    def crash(script, args, **kw):
        raise SceneRenderError("chrome went away")

    monkeypatch.setattr(rs_mod, "run_scene_script", crash)
    res = SceneThreeJsRuntime().build(ws)
    assert not res.ok
    for name in ("census.json", "scene_probe.json", "shader_preflight.json"):
        assert not (ws.artifacts / name).exists(), name
    assert json.loads((ws.artifacts / "build.json").read_text())["ok"] is False
