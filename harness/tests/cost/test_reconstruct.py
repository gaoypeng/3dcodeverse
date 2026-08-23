"""Rebuilding a ledger from a run directory: reconcile, never double count."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.cost import audit_runs, find_runs, reconstruct
from codeverse.cost.types import Role, Stage

REPO = Path(__file__).resolve().parents[2]


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


@pytest.mark.skipif(not (REPO / "runs" / "e2e_chair_blender" / "record.json").is_file(),
                    reason="recorded reference runs are not present")
def test_reference_runs_reconcile():
    """Every recorded run reconciles to its own record (or explains the gap)."""
    audit = audit_runs([REPO / "runs"])
    assert audit.n_runs >= 5
    for led in audit.runs:
        gap = led.ledger_usd - led.recorded_usd
        assert gap > -0.01, f"{led.run}: ledger lost ${-gap:.4f}"
        if gap > 0.01:
            assert led.notes, f"{led.run}: unexplained extra ${gap:.4f}"


@pytest.mark.skipif(not (REPO / "runs" / "e2e_chair_blender" / "record.json").is_file(),
                    reason="recorded reference runs are not present")
def test_gemini_cli_recheck_finds_the_stale_parse():
    """The threejs run was recorded before ``tokens.prompt`` was the total prompt:
    re-pricing from the raw CLI stats must show the under-billing, not hide it."""
    led = reconstruct(REPO / "runs" / "e2e_bench_threejs", recheck=True)
    assert led.ledger_usd > 2.0 > led.recorded_usd
    assert any("differs from record.total_usage" in n for n in led.notes)


def test_best_of_n_losers_are_attributed_and_counted_as_waste(fake_run: Path, tmp_path: Path):
    """A ``_cand/c<k>`` sub-workspace is the run's, not a run of its own; the
    candidates that lost are money spent on artifacts nobody kept."""
    import json
    import shutil

    ws = tmp_path / "cands"
    shutil.copytree(fake_run, ws)
    for k in (0, 1):
        dst = ws / "_cand" / f"c{k}" / "trajectories" / "baseline_r00"
        dst.parent.mkdir(parents=True)
        shutil.copytree(fake_run / "trajectories" / "baseline_r00", dst)
    (ws / "rounds").mkdir(exist_ok=True)
    (ws / "rounds" / "candidates.json").write_text(json.dumps({"n": 2, "selected": 1}))

    from codeverse.cost import audit_runs

    led = reconstruct(ws)
    assert find_runs(ws) == [ws], "a candidate sub-workspace is not a separate run"
    cand_rows = [r for r in led.rows if r.stage is Stage.CANDIDATE]
    assert len(cand_rows) == 4 and {r.label.split(":")[0] for r in cand_rows} == {"c0", "c1"}
    waste = audit_runs([ws]).waste_by_kind()
    assert "lost_candidate" in waste and waste["lost_candidate"][1] > 0
