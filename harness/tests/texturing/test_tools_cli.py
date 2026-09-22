"""texture tools (registry) + `3dcode texture` CLI, offline."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from codeverse3d.spatial.registry import ToolContext, get_tool
from codeverse3d.texturing.run import TextureServices
from codeverse3d.workspace import Workspace
from tests.texturing.conftest import FakeImageModel, FakeJudge, FakePlanModel, fake_render


def _ws(tmp_path: Path, chair_glb, chair_spec, chair_plan, *, texture: bool = True) -> Workspace:
    ws = Workspace(tmp_path / "run").create()
    # texturing.run.texture_requested is the ONE owner of "does this run texture?" —
    # the tool refuses in a run whose spec says no, so the spec has to say yes here
    spec = chair_spec.model_copy(update={"options": chair_spec.options.model_copy(update={"texture": texture})})
    ws.write_json(ws.spec_path, spec)
    ws.write_json(ws.plan_path, chair_plan)
    shutil.copy(chair_glb, ws.artifacts / "object.glb")
    return ws


def test_texture_pass_tool_runs_with_injected_fakes(tmp_path, chair_glb, chair_spec, chair_plan):
    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan)
    judge = FakeJudge([(0.7, {"materials": 0.5}), (0.72, {"materials": 0.7})])
    # plan_model too: without it material_plan builds a REAL model from the spec's
    # planner id, so this "injected fakes" test only passed on a box that has keys.
    services = TextureServices(image_model=FakeImageModel(), judge_obj=judge, render=fake_render,
                               plan_model=FakePlanModel(), cache_dir=tmp_path / "c")
    ctx = ToolContext(workspace=ws, track="static_object", language="blender",
                      extra={"texture_services": services})
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


def test_texture_pass_tool_refuses_when_the_run_did_not_ask_for_texturing(
        tmp_path, chair_glb, chair_spec, chair_plan):
    """`texture: false` must actually prevent the pass.  The tool is registered for every
    object track, so an agent used to be able to buy a texture pass inside any run
    (docs/COST.md §15: the quality run paid for two)."""
    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan, texture=False)
    services = TextureServices(image_model=FakeImageModel(), judge_obj=FakeJudge([(0.7, {})]),
                               render=fake_render, cache_dir=tmp_path / "c")
    obs = get_tool("texture_pass").call(
        ToolContext(workspace=ws, track="static_object", language="blender",
                    extra={"texture_services": services}), {})
    assert not obs.ok and "did not ask for texturing" in obs.text
    assert not (ws.artifacts / "object_textured.glb").exists()


def test_cli_texture_show_and_help(tmp_path, chair_glb, chair_spec, chair_plan):
    from codeverse3d.cli.main import app
    from codeverse3d.texturing.run import texture_pass

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


def test_cli_scene_pack_spend_joins_the_runs_ledger(tmp_path, chair_spec, monkeypatch):
    """`3dcode texture scene-pack` opened no run_ledger (`pass` did), so its plan + image
    spend went to the per-process log instead of the run's telemetry/cost.jsonl."""
    from codeverse3d.cli.main import app
    from codeverse3d.contracts.common import Language, Track, Usage
    from codeverse3d.contracts.plan import BBox, CameraPlan, ScenePlan, ZonePlan
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.cost.ledger import load_ledger, record_call
    from codeverse3d.texturing.plan import ScenePack

    ws = Workspace(tmp_path / "run").create()
    ws.write_json(ws.spec_path, chair_spec.model_copy(update={"track": Track.SCENE, "language": Language.SCENE_THREEJS}))
    bb = BBox(center=(0, 0, 0), extents=(30, 10, 30))
    ws.write_json(ws.plan_path, ScenePlan(
        title="Zen Garden", summary="a small Kyoto garden", setting="Kyoto", bounds=bb, environment="raked gravel",
        zones=[ZonePlan(name="Pond", description="koi pond with stepping stones", bbox=bb)],
        cameras=[CameraPlan(name="main", position=(10, 3, 10), look_at=(0, 0, 0))]))
    with run_ledger(ws.root, run=ws.root.name):   # the run kept a ledger while it ran
        record_call(Usage(model="gemini-3.7-flash", input_tokens=1000), label="planner")

    def fake_pack(plan, out_dir, image_model, model_id, **_):
        record_call(Usage(model="gemini-3.1-flash-image", input_tokens=200, cost_usd=0.04), label="texture_pack")
        return ScenePack(out_dir=str(out_dir))

    monkeypatch.setattr("codeverse3d.texturing.plan.scene_texture_pack", fake_pack)
    monkeypatch.setattr("codeverse3d.cli.texture_cmd._image_model", lambda name: FakeImageModel())
    res = CliRunner().invoke(app, ["texture", "scene-pack", str(ws.root), "--model", ""])
    assert res.exit_code == 0, res.output
    assert [r.label for r in load_ledger(ws.root)] == ["planner", "texture_pack"]
