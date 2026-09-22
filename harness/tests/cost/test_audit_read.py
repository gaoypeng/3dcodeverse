"""Reading finished runs back: the ledger is the money, the record the facts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.addons.costreport.audit import audit_runs, find_runs, read_run
from codeverse3d.contracts.common import Usage
from codeverse3d.cost.ledger import load_ledger, record_call
from codeverse3d.cost.types import Stage


def test_a_run_reads_as_its_ledger_rows_and_its_record_facts(fake_run: Path):
    led = read_run(fake_run)
    assert led.ledger_usd == pytest.approx(sum(r.cost_usd for r in load_ledger(fake_run)))
    assert {r.stage for r in led.rows} == {Stage.PLAN, Stage.BASELINE, Stage.JUDGE}
    assert led.track == "static_object" and led.stop_reason == "max_rounds" and led.round_built == [None]
    assert led.minutes == pytest.approx((20.0 + 60.0) / 60), "the run's minutes, not its span"
    assert led.model_s == pytest.approx(47.0)


def test_a_run_without_a_ledger_is_left_out(fake_run: Path, tmp_path: Path):
    bare = tmp_path / "pre_ledger"
    shutil.copytree(fake_run, bare)
    shutil.rmtree(bare / "telemetry")
    assert audit_runs([tmp_path]).n_runs == 1


def test_find_runs_skips_sub_workspaces(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "record.json").write_text("{}")
    sub = tmp_path / "a" / "_assets" / "x"
    sub.mkdir(parents=True)
    (sub / "record.json").write_text("{}")
    assert find_runs(tmp_path) == [tmp_path / "a"]


def test_an_ab_batterys_control_and_variant_cells_stay_two_runs(fake_run: Path, tmp_path: Path):
    """ab_plan lays a prompt's cells out as ``arms/<arm>/cells/<id>/<generator>``: naming a
    cell by its last two segments gave the control and the variant ONE label."""
    for arm in ("control", "variant"):
        cell = tmp_path / "out" / "arms" / arm / "cells" / "chair" / "harness_gemini-cli"
        shutil.copytree(fake_run, cell / "run")
        (cell / "cell.json").write_text(json.dumps({"prompt_id": "chair", "arm": "harness:x", "kind": "harness"}))
    audit = audit_runs([tmp_path / "out"])
    assert sorted(r.run for r in audit.runs) == ["control__chair__harness_gemini-cli",
                                                  "variant__chair__harness_gemini-cli"]


def test_a_losing_candidate_is_waste(fake_run: Path, tmp_path: Path):
    ws = tmp_path / "live_cands"
    shutil.copytree(fake_run, ws)
    (ws / "rounds").mkdir(exist_ok=True)
    (ws / "rounds" / "candidates.json").write_text(json.dumps({"n": 2, "selected": 1}))
    usage = Usage(backend="gemini-cli", model="gemini-3.7-flash", input_tokens=10_000, output_tokens=500)
    for k in (0, 1):
        record_call(usage, stage=Stage.CANDIDATE, round=0, label=f"baseline_c{k}", source="session",
                    ledger=ws / "telemetry" / "cost.jsonl")
    assert read_run(ws).selected_candidate == "c1"
    (item,) = [w for w in audit_runs([ws]).waste if w.kind == "lost_candidate"]
    assert item.usd > 0 and "c0 lost to c1" in item.detail
