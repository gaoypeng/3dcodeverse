"""The texture pass with every dependency injected, and the `3dcode texture` CLI, offline."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from codeverse3d.texturing.run import texture_pass
from codeverse3d.workspace import Workspace
from tests.texturing.conftest import FakeImageModel, FakeJudge, FakePlanModel, fake_render


def _ws(tmp_path: Path, chair_glb, chair_spec, chair_plan) -> Workspace:
    ws = Workspace(tmp_path / "run").create()
    ws.write_json(ws.spec_path, chair_spec)
    ws.write_json(ws.plan_path, chair_plan)
    shutil.copy(chair_glb, ws.artifacts / "object.glb")
    return ws


def test_texture_pass_runs_with_injected_fakes(tmp_path, chair_glb, chair_spec, chair_plan):
    """Every dependency injected, the spec's REAL planner id passed as ``model_id``: the pass must
    not build a model from it (tests/install/test_hermetic.py runs this with no keys at all)."""
    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan)
    judge = FakeJudge([(0.7, {"materials": 0.5}), (0.72, {"materials": 0.7})])
    rep = texture_pass(ws, chair_spec, chair_plan, model_id=chair_spec.backends.planner, image_model=FakeImageModel(),
                       plan_model=FakePlanModel(), judge_obj=judge, render=fake_render, cache_dir=tmp_path / "c")
    assert rep.shipped and rep.plan.source != "default"
    assert (ws.artifacts / "object_textured.glb").is_file()


def test_cli_texture_show_and_help(tmp_path, chair_glb, chair_spec, chair_plan):
    from codeverse3d.cli.main import app

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
