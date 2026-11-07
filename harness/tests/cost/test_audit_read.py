"""Reading finished runs back: the ledger is the money, the record the facts."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.addons.costreport.audit import read_run
from codeverse3d.cost.ledger import load_ledger
from codeverse3d.cost.types import Stage


def test_a_run_reads_as_its_ledger_rows_and_its_record_facts(fake_run: Path, tmp_path: Path):
    led = read_run(fake_run)
    assert led.ledger_usd == pytest.approx(sum(r.cost_usd for r in load_ledger(fake_run)))
    assert {r.stage for r in led.rows} == {Stage.PLAN, Stage.BASELINE, Stage.JUDGE}
    assert led.track == "static_object" and led.stop_reason == "max_rounds" and led.round_built == [None]
    assert led.minutes == pytest.approx((20.0 + 60.0) / 60), "the run's minutes, not its span"
    assert led.model_s == pytest.approx(47.0)
