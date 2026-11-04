"""Reading finished runs back: the ledger is the money, the record the facts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.addons.costreport.audit import audit_runs, read_run
from codeverse3d.contracts.common import Usage
from codeverse3d.cost.ledger import load_ledger, record_call
from codeverse3d.cost.types import Stage


def test_a_run_reads_as_its_ledger_rows_and_its_record_facts(fake_run: Path, tmp_path: Path):
    led = read_run(fake_run)
    assert led.ledger_usd == pytest.approx(sum(r.cost_usd for r in load_ledger(fake_run)))
    assert {r.stage for r in led.rows} == {Stage.PLAN, Stage.BASELINE, Stage.JUDGE}
    assert led.track == "static_object" and led.stop_reason == "max_rounds" and led.round_built == [None]
    assert led.minutes == pytest.approx((20.0 + 60.0) / 60), "the run's minutes, not its span"
    assert led.model_s == pytest.approx(47.0)


def test_a_losing_candidate_is_waste(fake_run: Path, tmp_path: Path):
    ws = tmp_path / "live_cands"
    shutil.copytree(fake_run, ws)
    (ws / "rounds").mkdir(exist_ok=True)
    (ws / "rounds" / "candidates.json").write_text(json.dumps({"n": 2, "selected": 1}))
    usage = Usage(backend="gemini-cli", model="gemini-3.8-flash", input_tokens=10_000, output_tokens=500)
    for k in (0, 1):
        record_call(usage, stage=Stage.CANDIDATE, round=0, label=f"baseline_c{k}", source="session",
                    ledger=ws / "telemetry" / "cost.jsonl")
    assert read_run(ws).selected_candidate == "c1"
    (item,) = [w for w in audit_runs([ws]).waste if w.kind == "lost_candidate"]
    assert item.usd > 0 and "c0 lost to c1" in item.detail
