"""texture tools (registry) + `3dcv texture` CLI, offline."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from codeverse.spatial.registry import ToolContext, get_tool, list_tools
from codeverse.texturing.generate import FakeImageModel
from codeverse.workspace import Workspace
from tests.texturing.conftest import FakeJudge, fake_render


def _ws(tmp_path: Path, chair_glb, chair_spec, chair_plan) -> Workspace:
    ws = Workspace(tmp_path / "run").create()
    ws.write_json(ws.spec_path, chair_spec)
    ws.write_json(ws.plan_path, chair_plan)
    shutil.copy(chair_glb, ws.artifacts / "object.glb")
    return ws


def test_tools_registered_for_object_tracks():
    names = {t.name for t in list_tools(track="static_object")}
    assert {"texture_pass", "texture_preview"} <= names
    assert "texture_pass" not in {t.name for t in list_tools(track="scene")}
    card = get_tool("texture_pass").card()
    assert "judge" in card and "texture_pass" in card


def test_texture_pass_tool_runs_with_injected_fakes(tmp_path, chair_glb, chair_spec, chair_plan):
    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan)
    judge = FakeJudge([(0.7, {"materials": 0.5}), (0.72, {"materials": 0.7})])
    ctx = ToolContext(workspace=ws, track="static_object", language="blender",
                      extra={"image_model": FakeImageModel(), "judge": judge, "render": fake_render, "cache_dir": tmp_path / "c"})
    obs = get_tool("texture_pass").call(ctx, {"model": ""})
    assert obs.ok, obs.text
    assert "SHIPPED" in obs.text and obs.numbers["shipped"] is True and obs.images
    assert (ws.artifacts / "object_textured.glb").is_file()
    # preview tool: cached_render_glb needs the real renderer; with a fake we only check the usage error path
    obs2 = get_tool("texture_preview").call(ToolContext(workspace=Workspace(tmp_path / "empty").create()), {})
    assert not obs2.ok and "texture_pass" in obs2.text


def test_texture_pass_tool_without_glb_is_usage_error(tmp_path, chair_spec, chair_plan):
    ws = Workspace(tmp_path / "run").create()
    ws.write_json(ws.spec_path, chair_spec)
    ws.write_json(ws.plan_path, chair_plan)
    obs = get_tool("texture_pass").call(ToolContext(workspace=ws), {})
    assert not obs.ok and "build" in obs.text


def test_cli_texture_show_and_help(tmp_path, chair_glb, chair_spec, chair_plan):
    from codeverse.cli.main import app
    from codeverse.texturing.run import texture_pass

    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan)
    texture_pass(ws, chair_spec, chair_plan, model_id="", image_model=FakeImageModel(), judge=False, render=fake_render,
                 cache_dir=tmp_path / "c")
    runner = CliRunner()
    res = runner.invoke(app, ["texture", "show", str(ws.root)])
    assert res.exit_code == 0, res.output
    assert "texture pass" in res.output and "wood_oiled_oak" in res.output
    res = runner.invoke(app, ["texture", "--help"])
    assert res.exit_code == 0 and "scene-pack" in res.output and "pass" in res.output
    res = runner.invoke(app, ["texture", "show", str(tmp_path / "nowhere")])
    assert res.exit_code != 0
