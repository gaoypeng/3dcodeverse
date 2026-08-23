"""SceneThreeJsRuntime: protocol conformance + build gate."""

from __future__ import annotations

import json

import pytest

from codeverse.contracts.common import Language
from codeverse.languages import get_runtime
from codeverse.languages.base import LanguageRuntime
from codeverse.languages.scene_threejs.runtime import SceneThreeJsRuntime
from tests.scene_runtime.conftest import needs_browser


def test_runtime_registered_and_conforms():
    rt = get_runtime(Language.SCENE_THREEJS)
    assert isinstance(rt, SceneThreeJsRuntime)
    assert isinstance(rt, LanguageRuntime)
    assert rt.language == Language.SCENE_THREEJS
    assert "src/scene.js" in rt.entry_globs and "src/zones/*.js" in rt.entry_globs
    doc = rt.contract_doc()
    assert "createScene" in doc and "update(t, dt)" in doc
    # the built-in fallback contract is always available and carries the GLSL rules
    from codeverse.languages.scene_threejs import runtime as rt_mod
    fallback = (rt_mod._HERE / "CONTRACT.md").read_text()
    assert "createScene" in fallback and "#include" in fallback
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
