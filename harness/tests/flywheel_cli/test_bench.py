"""Bench runner + report with a fake track (offline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench.report import build_report, load_results  # noqa: E402
from bench.run_bench import Battery, BenchOptions, run_battery  # noqa: E402
from codeverse.contracts.run import RunRecord, RunStatus  # noqa: E402
from codeverse.workspace import Workspace  # noqa: E402
from tests.flywheel_cli.conftest import make_fake_run  # noqa: E402

BATTERIES = sorted((REPO / "bench" / "prompts").glob("*.yaml"))


@pytest.mark.parametrize("path", BATTERIES, ids=[p.stem for p in BATTERIES])
def test_batteries_are_valid(path: Path):
    b = Battery.load(path)
    ids = [p.id for p in b.prompts]
    assert len(ids) == len(set(ids))
    tiers = {p.tier for p in b.prompts}
    assert tiers <= {"easy", "medium", "hard"}
    assert all(p.must_have for p in b.prompts)
    expected = {"static_objects_v1": 24, "articulated_v1": 12, "scenes_v1": 12}
    if b.name in expected:  # other batteries (compare_*, *_v2) are owned elsewhere; only the schema is checked
        assert len(tiers) == 3  # v1 batteries span all three tiers; v2 drops easy (it saturated)
        assert len(b.prompts) == expected[b.name]


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
        rec.status = RunStatus.PASSED if rec.final_score and rec.final_score >= 0.75 else RunStatus.PLATEAU
        ws.write_json(ws.record_path, rec)
        return rec
    return run


def test_run_battery_resume_and_report(tmp_path: Path):
    battery = REPO / "bench" / "prompts" / "static_objects_v1.yaml"
    out = tmp_path / "bench_out"
    scores = {"furn_easy_stool": (0.6, 0.85), "furn_med_dining_chair": (0.5, 0.7), "furn_hard_rolltop_desk": (0.4, 0.6)}
    opts = BenchOptions(parallel=2, limit=4, judge="gemini:gemini-3.7-flash", generator="api-agent:gemini:gemini-3.7-flash")
    res = run_battery(battery, out, opts, run_fn=_fake_run_fn(scores, fail_ids={"veh_easy_toy_car"}))
    assert len(res) == 4
    by_id = {r.id: r for r in res}
    assert by_id["furn_easy_stool"].score_final == 0.85 and by_id["furn_easy_stool"].passed is True
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
    assert tiers["easy"].n == 2 and tiers["easy"].final_mean == pytest.approx(0.85)  # toy car errored → unscored
    assert rep.overall is not None and rep.overall.errors == 1
    assert "| furn_easy_stool | easy |" in rep.markdown
    page = (out / "report.html").read_text()
    assert "data:image/jpeg;base64," in page and "furn_easy_stool" in page  # self-contained gallery
    assert "veh_easy_toy_car" in page and "boom" in page  # errored prompt still gets a card
