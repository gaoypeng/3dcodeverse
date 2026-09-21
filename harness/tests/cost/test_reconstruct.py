"""Rebuilding a ledger from a run directory: reconcile, never double count."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.addons.costreport.audit import audit_runs
from codeverse.contracts.common import Usage
from codeverse.cost import find_runs, reconstruct
from codeverse.cost.types import Role, Stage


def test_reconstructs_and_reconciles(fake_run: Path):
    led = reconstruct(fake_run)
    assert abs(led.ledger_usd - led.recorded_usd) < 1e-6, "rows must reconcile to record.total_usage"
    stages = {r.stage for r in led.rows}
    assert Stage.BASELINE in stages and Stage.JUDGE in stages and Stage.PLAN in stages
    assert led.track == "static_object" and led.passed is True


def test_generate_event_does_not_double_count_its_session(fake_run: Path):
    led = reconstruct(fake_run)
    gen = [r for r in led.rows if r.stage is Stage.BASELINE]
    assert len(gen) == 2, "two transcript turns, not the session row and not the event"
    assert all(r.source == "transcript" for r in gen)
    assert not [r for r in led.rows if r.source == "event:generate.done"]


def test_a_retried_session_does_not_double_count_its_generate_event(fake_run: Path, tmp_path: Path):
    """Reconstruction counts a retried generation exactly once."""
    import shutil

    ws = tmp_path / "retried"
    shutil.copytree(fake_run, ws)
    # split the baseline into a first attempt and a retry, as the harness records them
    first = ws / "trajectories" / "baseline_r00"
    retry = ws / "trajectories" / "baseline.a2_r00"
    shutil.copytree(first, retry)
    res = json.loads((retry / "result.json").read_text())
    res["label"] = "baseline.a2"
    for key, factor in (("input_tokens", 0.5), ("output_tokens", 0.5), ("cached_tokens", 0.5),
                        ("cost_usd", 0.5)):
        res["usage"][key] = type(res["usage"][key])(res["usage"][key] * factor)
    (retry / "result.json").write_text(json.dumps(res))
    turns = [json.loads(line) for line in (retry / "transcript.jsonl").read_text().splitlines()]
    for t in turns:
        for key in ("input_tokens", "output_tokens", "cached_tokens", "cost_usd"):
            t["usage"][key] = type(t["usage"][key])(t["usage"][key] * 0.5)
    (retry / "transcript.jsonl").write_text("\n".join(json.dumps(t) for t in turns))
    # the event summarises the WHOLE job (first attempt + retry) under the job label
    events = [json.loads(line) for line in (ws / "events.jsonl").read_text().splitlines()]
    for ev in events:
        if ev.get("event") == "generate.done":
            ev["cost_usd"] = round(ev["cost_usd"] * 1.5, 6)
    (ws / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))

    led = reconstruct(ws)
    assert not [r for r in led.rows if r.source == "event:generate.done"], \
        "the retry's dollars cover the job event; nothing may be added on top"
    gen = [r for r in led.rows if r.stage is Stage.BASELINE]
    assert len(gen) == 4 and all(r.source == "transcript" for r in gen)  # 2 turns x 2 attempts


def test_judge_row_carries_the_judge_model(fake_run: Path):
    row = next(r for r in reconstruct(fake_run).rows if r.role is Role.JUDGE)
    assert row.model == "gemini-3.1-pro-preview" and row.provider == "gemini"
    assert row.price_source == "exact"


def test_missing_session_falls_back_to_the_event(fake_run: Path, tmp_path: Path):
    """A CLI backend that left no trajectory still shows up, from its event."""
    import shutil

    ws = tmp_path / "no_traj"
    shutil.copytree(fake_run, ws)
    shutil.rmtree(ws / "trajectories")
    led = reconstruct(ws)
    gen = [r for r in led.rows if r.stage is Stage.BASELINE]
    assert len(gen) == 1 and gen[0].source == "event:generate.done"
    assert abs(led.ledger_usd - led.recorded_usd) < 0.001


def test_wall_clock_ignores_time_queued_before_the_run_started(fake_run: Path):
    led = reconstruct(fake_run)
    assert led.wall_s == pytest.approx(120.0)  # budget elapsed_min=2.0, not the 51 s event span


def test_find_runs_skips_sub_workspaces(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "record.json").write_text("{}")
    sub = tmp_path / "a" / "_assets" / "x"
    sub.mkdir(parents=True)
    (sub / "record.json").write_text("{}")
    assert find_runs(tmp_path) == [tmp_path / "a"]


def test_best_of_n_losers_are_attributed_and_counted_as_waste(fake_run: Path, tmp_path: Path):
    """Candidate sub-workspaces attribute their spend to the parent run."""
    import shutil

    ws = tmp_path / "cands"
    shutil.copytree(fake_run, ws)
    for k in (0, 1):
        dst = ws / "_cand" / f"c{k}" / "trajectories" / "baseline_r00"
        dst.parent.mkdir(parents=True)
        shutil.copytree(fake_run / "trajectories" / "baseline_r00", dst)
    (ws / "rounds").mkdir(exist_ok=True)
    (ws / "rounds" / "candidates.json").write_text(json.dumps({"n": 2, "selected": 1}))

    led = reconstruct(ws)
    assert find_runs(ws) == [ws], "a candidate sub-workspace is not a separate run"
    cand_rows = [r for r in led.rows if r.stage is Stage.CANDIDATE]
    assert len(cand_rows) == 4 and {r.label.split(":")[0] for r in cand_rows} == {"c0", "c1"}
    waste = audit_runs([ws]).waste_by_kind()
    assert "lost_candidate" in waste and waste["lost_candidate"][1] > 0


def test_a_live_ledger_files_losing_candidates_as_waste(fake_run: Path, tmp_path: Path):
    """Live rows carry the candidate clone's label (``baseline_c<k>``, kind=candidate) —
    not the reconstructed ``c<k>:baseline`` — and the waste detector must read both."""
    import shutil

    from codeverse.cost.instrument import run_ledger
    from codeverse.cost.ledger import record_call

    ws = tmp_path / "live_cands"
    shutil.copytree(fake_run, ws)
    (ws / "rounds").mkdir(exist_ok=True)
    (ws / "rounds" / "candidates.json").write_text(json.dumps({"n": 2, "selected": 1}))
    usage = Usage(backend="gemini-cli", model="gemini-3.7-flash", input_tokens=10_000, output_tokens=500)
    with run_ledger(ws, run=ws.name):
        for k in (0, 1):
            record_call(usage, stage=Stage.CANDIDATE, round=0, label=f"baseline_c{k}", source="session")
    led = reconstruct(ws)
    assert led.source == "live" and led.selected_candidate == "c1"
    waste = audit_runs([ws]).waste_by_kind()
    assert waste["lost_candidate"][0] == 1 and waste["lost_candidate"][1] > 0
    (item,) = [w for w in audit_runs([ws]).waste if w.kind == "lost_candidate"]
    assert "c0 lost to c1" in item.detail
