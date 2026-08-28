"""SceneThreeJsRuntime: protocol conformance + build gate."""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.common import Language
from codeverse.languages import get_runtime
from codeverse.languages.base import LanguageRuntime
from codeverse.languages.scene_threejs import SceneThreeJsRuntime
from tests.scene_runtime.conftest import needs_browser


def test_runtime_registered_and_conforms():
    rt = get_runtime(Language.SCENE_THREEJS)
    assert isinstance(rt, SceneThreeJsRuntime)
    assert isinstance(rt, LanguageRuntime)
    assert rt.language == Language.SCENE_THREEJS
    assert "src/scene.js" in rt.entry_globs and "src/zones/*.js" in rt.entry_globs
    doc = rt.contract_doc()
    assert "createScene" in doc and "update(t, dt)" in doc
    # the GLSL chunk rules are the cookbook's job, not the contract's — this used to
    # assert them against languages/scene_threejs/CONTRACT.md, a file contract_doc()
    # never reached (deleted 2026-08-28); prompts/scene_threejs/glsl_cookbook.md states
    # the same rule and IS delivered
    assert "`#include <...>` alone on its line" in rt.cookbook_path().with_name("glsl_cookbook.md").read_text()
    assert rt.cookbook_path().name == "cookbook.md"


def test_skeleton_writes_example(ws):
    rt = SceneThreeJsRuntime()
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
    p = starter_ws.src / "shaders" / "sky.js"
    text = p.read_text().replace("float h = clamp(vWorldDir.y, -0.2, 1.0);", "float h = clamp(vWorldDir.y, -0.2, 1.0) + nope;")
    p.write_text(text)
    res = SceneThreeJsRuntime().build(starter_ws)
    assert not res.ok
    assert res.error_file == "src/shaders/sky.js" and res.error_line
    assert "nope" in res.error_message
    assert "shader_preflight: FAILED" in res.stdout_tail


@pytest.mark.node
@needs_browser
def test_build_fails_on_import_error(starter_ws):
    (starter_ws.src / "scene.js").write_text("import { x } from './nope.js';\nexport function createScene() {}\n")
    res = SceneThreeJsRuntime().build(starter_ws)
    assert not res.ok and res.error_file == "src/scene.js" and "nope.js" in res.error_message


def test_build_interprets_combined_summary_offline(ws, monkeypatch):
    """The single-boot build (probe_scene.mjs --compile) still yields the SAME two
    GateReports: scene_probe from the probe summary, shader_preflight from the
    embedded shader_report; a non-booting scene keeps the failed-empty shader gate."""
    import codeverse.languages.scene_threejs as rt_mod
    from codeverse.spatial.render_scene import NodeResult

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

    import codeverse.spatial.render_scene as rs_mod
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
    """A driver crash (SceneRenderError) must not leave the previous round's census
    or the root driver outputs (scene_probe.json / shader_preflight.json) looking
    current; build.json on disk says ok:false, never the previous round's ok:true."""
    import codeverse.spatial.render_scene as rs_mod
    from codeverse.spatial.render_scene import SceneRenderError

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
