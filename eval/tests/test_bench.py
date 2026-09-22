"""Bench runner + report with a fake track (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.report import build_report, load_results
from bench.run_bench import Battery, BenchOptions, run_battery
from codeverse3d.contracts.run import RunRecord, RunStatus
from codeverse3d.workspace import Workspace
from tests.conftest import make_fake_run

REPO = Path(__file__).resolve().parents[1]
BATTERIES = sorted((REPO / "bench" / "prompts").glob("*.yaml"))


def test_all_batteries_are_valid():
    """One corpus contract is clearer than 27 identical pytest cases."""
    assert BATTERIES, "no checked-in benchmark batteries found"
    expected = {"static_objects_v1": 24, "articulated_v1": 12, "scenes_v1": 12}
    for path in BATTERIES:
        b = Battery.load(path)
        ids = [p.id for p in b.prompts]
        assert len(ids) == len(set(ids)), path.name
        tiers = {p.tier for p in b.prompts}
        assert tiers <= {"easy", "medium", "hard"}, path.name
        # Head-to-head batteries keep the other side's brief verbatim: no must
        # items by design (bench/h2h_*.py).
        assert all(p.must_have for p in b.prompts) or b.name.startswith("h2h_"), path.name
        if b.name in expected:  # Other batteries are owned elsewhere; only their schema is checked here.
            assert len(tiers) == 3, path.name
            assert len(b.prompts) == expected[b.name], path.name


def _fake_run_fn(scores_by_id: dict[str, tuple[float, float]], fail_ids: set[str] = frozenset()):
    def run(spec, ws: Workspace, resume: bool) -> RunRecord:
        pid = spec.id.split("/")[-1]
        if pid in fail_ids:
            raise RuntimeError("boom")
        # reuse the synthetic record builder in the bench workspace (slug = prompt id)
        _ws, rec = make_fake_run(ws.root.parent, ws.root.name, prompt=spec.prompt, language=spec.language,
                                 scores=scores_by_id.get(pid, (0.5, 0.7)))
        rec.spec = spec
        ws.write_json(ws.spec_path, spec)
        rec.status = RunStatus.MAX_ROUNDS
        ws.write_json(ws.record_path, rec)
        return rec
    return run


def test_run_battery_resume_and_report(tmp_path: Path):
    battery = REPO / "bench" / "prompts" / "static_objects_v1.yaml"
    out = tmp_path / "bench_out"
    scores = {"furn_easy_stool": (0.6, 0.85), "furn_med_dining_chair": (0.5, 0.7), "furn_hard_rolltop_desk": (0.4, 0.6)}
    opts = BenchOptions(parallel=2, limit=4, judge="gemini:gemini-3.7-flash", generator="gemini-cli:gemini-3.6-flash")
    res = run_battery(battery, out, opts, run_fn=_fake_run_fn(scores, fail_ids={"veh_easy_toy_car"}))
    assert len(res) == 4
    by_id = {r.id: r for r in res}
    assert by_id["furn_easy_stool"].score_picked == 0.85 and by_id["furn_easy_stool"].picked_round == 1
    # like `3dcode make`, the battery hands over the picked round
    assert (out / "runs" / "furn_easy_stool" / "deliverable" / "manifest.json").is_file()
    assert json.loads((out / "runs" / "furn_easy_stool" / "selection.json").read_text())["round"] == 1
    assert by_id["veh_easy_toy_car"].status == "error" and "boom" in by_id["veh_easy_toy_car"].errors
    assert (out / "results.csv").is_file() and len(load_results(out)) == 4
    assert (out / "runs" / "furn_easy_stool" / "spec.json").is_file()
    spec = json.loads((out / "runs" / "furn_easy_stool" / "spec.json").read_text())
    assert spec["backends"]["judge"] == "gemini:gemini-3.7-flash" and "bench" in spec["tags"]
    # resume: already-done prompts are not re-run; the failed one is re-run (it is in results → also skipped)
    calls = []

    def counting(spec, ws, resume):
        calls.append(spec.id)
        return _fake_run_fn(scores)(spec, ws, resume)

    res2 = run_battery(battery, out, BenchOptions(parallel=1, limit=5), run_fn=counting)
    assert calls == ["static_objects_v1/veh_med_pickup"] and len(res2) == 5
    rep = build_report(out)
    assert rep.n == 5 and (out / "report.md").is_file() and (out / "report.html").is_file()
    tiers = {g.group: g for g in rep.by_tier}
    assert tiers["easy"].n == 2 and tiers["easy"].picked_mean == pytest.approx(0.85)  # toy car errored → unscored
    assert "pass" not in rep.markdown.split("### per prompt")[0], "no pass rate: a run is not passed or failed"
    assert rep.overall is not None and rep.overall.errors == 1
    assert "| furn_easy_stool | easy |" in rep.markdown
    page = (out / "report.html").read_text()
    assert "data:image/jpeg;base64," in page and "furn_easy_stool" in page  # self-contained gallery
    assert "veh_easy_toy_car" in page and "boom" in page  # errored prompt still gets a card
    # pin (REVIEW_2026-08-26 D1+D2): the stats tables sit in the page and every card carries its tier
    assert "<table>" in page and "by category" in page and page.count("<h3>") == 2
    assert page.count("easy") >= 2 and "<title>bench — " in page


def test_every_bench_prompt_opens_its_own_run_ledger(tmp_path: Path):
    """The batteries produce most of the runs; without a ledger their per-call rows
    went to the per-process fallback log instead of the run (docs/COST.md §12)."""
    from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ChatResponse
    from codeverse3d.contracts.common import Usage
    from codeverse3d.cost.instrument import MeteredChatModel
    from codeverse3d.cost.ledger import load_ledger

    class FakeChat:
        provider, model, id = "gemini", "gemini-3.7-flash", "gemini:gemini-3.7-flash"

        def generate(self, request: ChatRequest) -> ChatResponse:
            return ChatResponse(text="ok", usage=Usage(backend="gemini", model="gemini-3.7-flash",
                                                       input_tokens=1000, output_tokens=10))

    inner = _fake_run_fn({})

    def run(spec, ws: Workspace, resume: bool) -> RunRecord:
        MeteredChatModel(FakeChat()).generate(
            ChatRequest(messages=[ChatMessage.user("x")], label="api-agent:baseline:t0"))
        return inner(spec, ws, resume)

    battery = REPO / "bench" / "prompts" / "static_objects_v1.yaml"
    out = tmp_path / "bench_out"
    res = run_battery(battery, out, BenchOptions(parallel=2, limit=3), run_fn=run)
    assert len(res) == 3
    for r in res:
        # the fake run's own rows (make_fake_run writes a ledger like a run) + the metered call
        rows = [row for row in load_ledger(Path(r.workspace)) if row.label == "api-agent:baseline:t0"]
        assert len(rows) == 1, f"{r.id}: {rows}"
        assert rows[0].run == r.id and rows[0].cost_usd > 0  # and not a sibling's row


def test_a_truncated_last_line_does_not_cost_the_whole_resume(tmp_path: Path):
    """A truncated final JSONL row preserves earlier paid results on resume."""
    battery = REPO / "bench" / "prompts" / "static_objects_v1.yaml"
    out = tmp_path / "bench_out"
    run_battery(battery, out, BenchOptions(parallel=1, limit=2), run_fn=_fake_run_fn({}))
    jl = out / "results.jsonl"
    good = [ln for ln in jl.read_text().splitlines() if ln.strip()]
    assert len(good) == 2
    # SIGKILL mid-append: the last row is truncated inside a JSON string
    jl.write_text("\n".join(good) [: -30])

    calls: list[str] = []

    def counting(spec, ws, resume):
        calls.append(spec.id.split("/")[-1])
        return _fake_run_fn({})(spec, ws, resume)

    res = run_battery(battery, out, BenchOptions(parallel=1, limit=2), run_fn=counting)
    # the intact first row is still credited: only the truncated prompt is re-run
    assert calls == [good and json.loads(good[1])["id"]]
    assert len(res) == 2
    # and the already-paid rows remain reportable
    assert {r.id for r in load_results(out)} == {json.loads(ln)["id"] for ln in good}


def test_a_provider_outage_is_not_model_latency_and_not_an_error():
    """Provider downtime is excluded from model latency and crash counts."""
    from bench.report import _stats
    from bench.run_bench import BenchItemResult

    clear = [BenchItemResult(id=f"p{i}", tier="easy", score_baseline=0.5, score_picked=0.8,
                             minutes=1.0, cost_usd=0.10, status="max_rounds") for i in range(4)]
    storm = BenchItemResult(
        id="p_storm", tier="easy", minutes=60.0, cost_usd=0.03, status="infra_failed",
        errors="ModelError: Gemini API error 503: The model is overloaded. Please try again later.")

    base, with_storm = _stats("easy", clear), _stats("easy", clear + [storm])
    assert base.minutes_mean == 1.0 and base.cost_mean == 0.10
    assert with_storm.minutes_mean == 1.0, "someone else's downtime is not this model's latency"
    assert with_storm.cost_mean == 0.10
    # the loss stays visible, in its own column, and is NOT an error
    assert with_storm.n == 5 and with_storm.n_evaluated == 4 and with_storm.infra_failed == 1
    assert with_storm.errors == 0, "a provider outage must not read as a crash"
    # the scored rates were already safe; they must stay so
    assert with_storm.picked_mean == base.picked_mean and with_storm.delta_mean == base.delta_mean
    # a row written before 2026-09-22 still reads: its score_final is the picked score
    assert BenchItemResult.model_validate({"id": "old", "tier": "easy", "score_final": 0.7, "passed": True}).score_picked == 0.7


def test_the_runner_classifies_the_outage_that_reaches_it(tmp_path: Path):
    """The status has to be recorded in the first place — the reporter can only honour
    what run_battery wrote."""
    from codeverse3d.models.base import ModelError

    battery = REPO / "bench" / "prompts" / "static_objects_v1.yaml"
    out = tmp_path / "bench_out"

    def storm(spec, ws, resume):
        raise ModelError("Gemini API error 503: The model is overloaded.", retryable=True, status=503)

    res = run_battery(battery, out, BenchOptions(parallel=1, limit=1), run_fn=storm)
    assert [r.status for r in res] == ["infra_failed"]

    # ... and --redo-status makes it actionable, the way compare_backends' already is
    calls: list[str] = []

    def counting(spec, ws, resume):
        calls.append(spec.id)
        return _fake_run_fn({})(spec, ws, resume)

    run_battery(battery, out, BenchOptions(parallel=1, limit=1), run_fn=counting)
    assert calls == [], "a plain resume still skips every recorded row"
    run_battery(battery, out, BenchOptions(parallel=1, limit=1, redo_status=["infra_failed"]), run_fn=counting)
    assert len(calls) == 1, "--redo-status infra_failed re-runs what the weather lost"
    # ...and it is the ONLY way in.  A `resume` switch that merely dropped the recorded rows
    # skipped the archive below and silently resumed the old workspace, spec and clock.
    assert "resume" not in BenchOptions.model_fields


def test_a_redo_starts_from_a_fresh_workspace(tmp_path):
    """Redoing with a new budget archives the old workspace and starts fresh."""
    from bench.run_bench import archive_attempt

    out = tmp_path / "out"
    b = Battery.load(Path("bench/prompts/static_objects_v1.yaml"))
    pid = b.prompts[0].id
    ws_root = out / "runs" / pid
    stale = Workspace(ws_root).create()  # a real (stale) workspace, the way the budget run left it
    stale.spec_path.write_text("{}")
    (ws_root / "src" / "old.py").write_text("# stale")
    out.mkdir(exist_ok=True)
    (out / "results.jsonl").write_text(json.dumps({"id": pid, "tier": b.prompts[0].tier, "status": "budget", "score_final": None}) + "\n")
    seen: list[bool] = []

    def run(spec, ws, resume):
        seen.append(resume)
        _ws, rec = make_fake_run(ws.root.parent, ws.root.name, prompt=spec.prompt, language=spec.language, scores=(0.5, 0.7))
        rec.spec = spec
        ws.write_json(ws.spec_path, spec)
        ws.write_json(ws.record_path, rec)
        return rec

    opts = BenchOptions(ids=[pid], redo_status=["budget"], parallel=1, rounds=1)
    run_battery(Path("bench/prompts/static_objects_v1.yaml"), out, opts, run_fn=run)
    assert seen == [False], "the redo ran fresh, not resumed"
    assert (out / "runs" / f"{pid}.attempt1" / "src" / "old.py").exists(), "the old tree is archived beside the new one"
    assert archive_attempt(out / "runs" / pid).name == f"{pid}.attempt2"


def test_the_launcher_hands_its_flags_to_run_battery(tmp_path: Path, monkeypatch, capsys):
    """``python -m bench.run_bench`` replaced ``3dcode bench run`` (2026-09-22) with the same flags."""
    import bench.run_bench as rb

    seen: dict = {}

    def fake(battery, out, opts, *, on_result=None):
        seen.update(battery=battery, out=out, opts=opts)
        return []

    monkeypatch.setattr(rb, "run_battery", fake)
    battery = REPO / "bench" / "prompts" / "static_objects_v1.yaml"
    assert rb.main([str(battery), "--out", str(tmp_path), "--generator", "g", "--judge", "j", "--rounds", "2",
                    "--max-minutes", "120", "--parallel", "3", "--id", "a", "--id", "b", "--tier", "easy",
                    "--limit", "5", "--redo-status", "infra_failed,error", "--no-report"]) == 0
    o = seen["opts"]
    assert (o.generator, o.judge, o.rounds, o.max_minutes, o.parallel, o.limit) == ("g", "j", 2, 120.0, 3, 5)
    assert o.ids == ["a", "b"] and o.tiers == ["easy"] and o.redo_status == ["infra_failed", "error"]
    assert seen["out"] == tmp_path and "0 results" in capsys.readouterr().out
    # no --parallel / --out: BenchOptions' measured knee, and bench/out/<battery name>
    assert rb.main([str(battery), "--no-report"]) == 0
    assert seen["opts"].parallel == BenchOptions().parallel
    assert seen["out"] == REPO / "bench" / "out" / "static_objects_v1"
    with pytest.raises(SystemExit):
        rb.main([str(battery), "--rounds", "-1"])


def test_the_report_command_rebuilds_the_report(tmp_path: Path, capsys):
    """``python -m bench.report <out>`` replaced ``3dcode bench report``."""
    from bench.report import main

    battery = REPO / "bench" / "prompts" / "static_objects_v1.yaml"
    run_battery(battery, tmp_path, BenchOptions(parallel=1, limit=1), run_fn=_fake_run_fn({}))
    assert main([str(tmp_path)]) == 0
    assert "# bench report" in capsys.readouterr().out and (tmp_path / "report.html").is_file()
    with pytest.raises(SystemExit):
        main([str(tmp_path / "missing")])
