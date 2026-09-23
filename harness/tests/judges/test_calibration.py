import json
from pathlib import Path

import pytest

from codeverse3d.addons.calibration import calibrate, load_run_cases, pearson, spearman
from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.judges.rubrics import load_rubric
from tests.judges.conftest import (
    ACCEPTANCE,
    FakeChatModel,
    good_reply,
    make_measurement,
    make_renders,
    make_spec,
)

R = load_rubric("static_object_v1")


def _fake_run(root: Path, name: str) -> Path:
    run = root / name
    (run / "rounds").mkdir(parents=True)
    (run / "spec.json").write_text(make_spec(id=name).model_dump_json())
    box = {"center": [0, 0, 0.4], "extents": [0.5, 0.5, 0.9]}
    plan = {"object_name": "Chair", "summary": "a chair.", "overall_bbox": box,
            "parts": [{"name": "Seat", "role": "seat", "description": "board", "bbox": box},
                      {"name": "Leg", "role": "leg", "description": "turned", "bbox": box, "instances": 4}],
            "acceptance": [a.model_dump() for a in ACCEPTANCE]}
    (run / "plan.json").write_text(json.dumps(plan))
    (run / "record.json").write_text(json.dumps({"best_round": 1}))
    bad = GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="Leg", message="part floating 30 mm", data={"kind": "floating"}),
        GateFinding(gate="connectivity", severity=Severity.WARN, message="sliver")])
    for i, gates in enumerate(([bad], [GateReport(gate="connectivity", passed=True)])):
        rec = RoundRecord(index=i, kind="baseline" if i == 0 else "refine", gates=gates, measurement=make_measurement(),
                          renders=make_renders(run / "artifacts" / "renders" / f"r{i:02d}", sheet=False))
        (run / "rounds" / f"r{i:02d}.json").write_text(rec.model_dump_json())
    return run


def test_load_cases_reads_the_plan_the_in_run_judge_read(tmp_path):
    """No usable record.json: the plan comes from plan.json, typed, and its digest is the
    in-run one — a Z-up object's size in the measurement frame (W×H×D)."""
    run = _fake_run(tmp_path, "runA")
    cases = load_run_cases(run)
    assert [c.round_index for c in cases] == [0, 1] and cases[0].gate_errors == 1 and cases[1].gate_errors == 0
    assert cases[0].rubric == "static_object_v1" and len(cases[0].inp.acceptance) == 2
    assert cases[1].is_picked and not cases[0].is_picked and cases[0].glb is None  # no usable record: the last round
    assert cases[0].inp.plan_summary == "Chair: a chair. Overall 0.50×0.90×0.50 m (W×H×D). Parts: Seat, Leg×4."
    assert load_run_cases(run, rounds=[1])[0].round_index == 1


def test_with_nothing_picked_the_stand_in_is_the_last_round_that_kept_a_glb(tmp_path, caplog):
    """No picked round: the geometry montage used the last round even when IT kept no GLB (and
    the render was skipped in silence) while an earlier round kept one."""
    from codeverse3d.addons.calibration import render_geometry_views

    run = _fake_run(tmp_path, "runB")
    (run / "artifacts" / "r00").mkdir(parents=True)
    (run / "artifacts" / "r00" / "object.glb").write_bytes(b"glTF")
    cases = load_run_cases(run)
    assert cases[0].is_picked and not cases[1].is_picked and cases[0].glb.endswith("r00/object.glb")
    assert render_geometry_views(cases[1], tmp_path / "out", "clay") is None
    assert "kept no GLB" in caplog.text


def test_correlations():
    assert pearson([0, 1, 2], [2, 1, 0]) == -1.0 and spearman([0, 1, 5], [5, 3, 1]) == -1.0
    assert pearson([1, 1], [0, 1]) is None and spearman([], []) is None
    assert spearman([1, 1, 2], [1, 2, 3]) == pytest.approx(0.866, abs=1e-3)


def test_calibrate_offline(tmp_path):
    runs = [_fake_run(tmp_path, "runA"), _fake_run(tmp_path, "runB")]
    ids = ["A1", "A2"]
    # every round gets 2 samples; gate-error rounds get low scores so the correlation is negative
    def reply(req):
        low = "errors (1)" in req.messages[0].parts[0].text
        return good_reply(R, ids, 0.5 if low else 0.9)
    model = FakeChatModel(default=reply)
    table = calibrate(runs, model_id="fake:fake-1", n_samples=2, out_dir=tmp_path / "out", geometry_mode=None,
                      chat_model=model, max_workers=2)
    assert len(table.rows) == 4 and table.n_samples == 2
    r0 = next(r for r in table.rows if r.run == "runA" and r.round == 0)
    assert r0.gate_errors == 1 and r0.caps == ["floating_part"] and r0.mean == 0.5 and r0.uncapped == 0.5
    r1 = next(r for r in table.rows if r.run == "runA" and r.round == 1)
    assert r1.mean == pytest.approx(0.9) and r1.passed and r1.n_used == 2
    assert table.pearson_errors_vs_score == -1.0 and table.spearman_errors_vs_score == -1.0
    assert table.pearson_stored_vs_new is None  # fake rounds carry no stored judgment
    assert len(model.requests) == 8 and table.total_cost_usd == pytest.approx(0.008)
    md = (tmp_path / "out" / "calibration_fake_fake-1.md").read_text()
    assert "| runA | r00 | baseline | 1/1 |" in md and "pearson(gate errors, new score) = -1.000" in md
    data = json.loads((tmp_path / "out" / "calibration_fake_fake-1.json").read_text())
    assert data["rows"][0]["per_criterion_std"]
    empty = tmp_path / "empty"
    (empty / "rounds").mkdir(parents=True)
    (empty / "spec.json").write_text(make_spec(id="empty").model_dump_json())
    with pytest.raises(ValueError, match="no judgeable rounds"):
        calibrate([empty], model_id="fake:fake-1", out_dir=tmp_path / "out", chat_model=model)


def test_run_labels_stay_distinct_across_battery_layouts():
    """Bench cells all end in .../run: compare_backends cells for DIFFERENT prompts
    (and ab_plan's control vs treatment arms) used to collapse to one label, so
    their judgment sidecars overwrote each other (V9c)."""
    from codeverse3d.addons.calibration import _run_label

    # compare_backends: <battery>/cells/<prompt>/<arm>/run
    a = _run_label(Path("/bench/out/compare_v1/cells/chair/harness-gemini/run"))
    b = _run_label(Path("/bench/out/compare_v1/cells/table/harness-gemini/run"))
    # ab_plan: <out>/<arm>/cells/<item>/<slug>/run
    c = _run_label(Path("/bench/out/ab/control/cells/chair/g37/run"))
    d = _run_label(Path("/bench/out/ab/treatment/cells/chair/g37/run"))
    e = _run_label(Path("/bench/out/ab/control/cells/table/g37/run"))
    assert len({a, b, c, d, e}) == 5, (a, b, c, d, e)
    # a plain harness run keeps its slug as the whole label
    assert _run_label(Path("/home/u/proj/runs/chair_bl")) == "chair_bl"


def test_calibration_judges_what_the_in_run_judge_saw(tmp_path, monkeypatch):
    """It rebuilt a thinner JudgeInput than `3dcode judge` (no previous verdict, track
    context, stored clay views or GLB for the D48 slices), re-rendered clay views a round
    had stored, and always used VlmJudge where `3dcode judge` picks ReferenceJudge."""
    import codeverse3d.addons.calibration as cal
    import codeverse3d.judges.vlm_judge as vj
    from codeverse3d.contracts.spec import ReferenceImage
    from tests.flywheel_cli.conftest import _judgment, make_fake_run, tiny_png

    ws, rec = make_fake_run(tmp_path / "runs")
    rec.spec = rec.spec.model_copy(update={"references": [ReferenceImage(path=str(tiny_png(ws.root / "ref.png")))]})
    ws.write_json(ws.record_path, rec)
    for r in rec.rounds:
        ws.write_json(ws.root / "rounds" / f"r{r.index:02d}.json", r)
    tiny_png(ws.renders_dir(1) / "clay" / "view_front.png")
    made, seen = [], []

    class _Vlm:
        def __init__(self, *a, **kw):
            made.append(type(self).__name__)
            self.model_id = kw["model_id"]

        def judge(self, inp):
            seen.append(inp)
            return _judgment(0.6, False, [])

    class _Ref(_Vlm):
        pass

    monkeypatch.setattr(vj, "VlmJudge", _Vlm)
    monkeypatch.setattr(vj, "ReferenceJudge", _Ref)
    monkeypatch.setattr(cal, "render_geometry_views", lambda *a, **k: pytest.fail("stored clay views re-rendered"))
    table = calibrate([ws.root], model_id="fake:fake-1", n_samples=1, out_dir=tmp_path / "out", max_workers=1)
    assert set(made) == {"_Ref"}
    r1 = next(inp for inp in seen if inp.round_index == 1)
    assert r1.previous is not None and r1.previous.overall == 0.55
    assert r1.geometry_views is not None and r1.glb_path == str(ws.round_artifacts(1) / "object.glb")  # the round's own
    assert next(row for row in table.rows if row.round == 1).geometry_views
