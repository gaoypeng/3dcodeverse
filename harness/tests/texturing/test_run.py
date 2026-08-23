"""run.py: the whole pass offline (fake image model, fake render, fake judge)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from codeverse.contracts.run import RunRecord
from codeverse.texturing.generate import FakeImageModel
from codeverse.texturing.run import TextureReport, latest_sheet, load_report, texture_pass
from codeverse.workspace import Workspace
from tests.texturing.conftest import FakeJudge, fake_render


def _ws(tmp_path: Path, chair_glb: Path, chair_spec, chair_plan) -> Workspace:
    ws = Workspace(tmp_path / "run").create()
    ws.write_json(ws.spec_path, chair_spec)
    ws.write_json(ws.plan_path, chair_plan)
    shutil.copy(chair_glb, ws.artifacts / "object.glb")
    rec = RunRecord(spec=chair_spec, plan=chair_plan, workspace=str(ws.root))
    ws.write_json(ws.record_path, rec)
    return ws


def test_texture_pass_ships_and_records(tmp_path, chair_glb, chair_spec, chair_plan):
    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan)
    judge = FakeJudge([(0.70, {"materials": 0.6, "intent_fidelity": 0.8}), (0.73, {"materials": 0.75, "intent_fidelity": 0.8})])
    img = FakeImageModel(usd_per_image=0.05)
    rep = texture_pass(ws, chair_spec, chair_plan, model_id="", image_model=img, judge=judge, render=fake_render,
                       cache_dir=tmp_path / "cache")
    assert isinstance(rep, TextureReport) and rep.shipped and rep.delta == 0.03
    assert rep.plan.source == "default" and len(rep.textures.paths()) == 2
    assert (ws.artifacts / "object_textured.glb").is_file() and (ws.artifacts / "object.glb").read_bytes() == chair_glb.read_bytes()
    assert (ws.artifacts / "textures" / "texturing.json").is_file() and (ws.artifacts / "textures" / "texture_plan.json").is_file()
    assert rep.usage.cost_usd > 0.1  # 2 images + 2 judge calls
    rec = json.loads(ws.record_path.read_text())
    tx = rec["extra"]["texturing"]
    assert tx["shipped"] is True and tx["glb_textured"] == "artifacts/object_textured.glb" and len(tx["textures"]) == 2
    assert tx["texture_plan"]["Seat"] == tx["texture_plan"]["Back"]
    # events were emitted
    events = [json.loads(line)["event"] for line in ws.events_path.read_text().splitlines()]
    assert events[0] == "texture.start" and "texture.gate" in events and events[-1] == "texture.done"
    # report reloads
    assert load_report(ws).shipped
    # planner quick render was written because there was no sheet
    assert latest_sheet(ws) is None and (ws.artifacts / "textures" / "planner_views" / "sheet.png").is_file()


def test_texture_pass_not_shipped_keeps_report(tmp_path, chair_glb, chair_spec, chair_plan):
    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan)
    judge = FakeJudge([(0.70, {"materials": 0.6}), (0.60, {"materials": 0.6})])
    rep = texture_pass(ws, chair_spec, chair_plan, model_id="", image_model=FakeImageModel(), judge=judge, render=fake_render,
                       cache_dir=tmp_path / "cache")
    assert not rep.shipped and rep.gate is not None and rep.glb_out.endswith("object_textured.glb")
    assert json.loads(ws.record_path.read_text())["extra"]["texturing"]["shipped"] is False


def test_texture_pass_no_judge_and_all_failed(tmp_path, chair_glb, chair_spec, chair_plan):
    ws = _ws(tmp_path, chair_glb, chair_spec, chair_plan)
    rep = texture_pass(ws, chair_spec, chair_plan, model_id="", image_model=FakeImageModel(), judge=False, render=fake_render,
                       cache_dir=tmp_path / "cache")
    assert rep.shipped and rep.gate is None and any("skipped" in n for n in rep.notes)
    # every image fails → nothing applied, not shipped, no crash
    ws2 = _ws(tmp_path / "b", chair_glb, chair_spec, chair_plan)
    rep2 = texture_pass(ws2, chair_spec, chair_plan, model_id="", image_model=FakeImageModel(fail_on=("seamless",)), judge=False,
                        render=fake_render, cache_dir=tmp_path / "cache2")
    assert not rep2.shipped and rep2.apply is None and rep2.glb_out == "" and rep2.textures.failed()
