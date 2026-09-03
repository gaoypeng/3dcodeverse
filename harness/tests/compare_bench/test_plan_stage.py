"""The loss-event channel's arithmetic: the p-value a paper cites is computed here.

``bench/data/plan_stage/*.jsonl`` carries the 560 recorded plan calls behind D52; this
pins the statistic that turns them into 0.0067, and the report that prints it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.plan_stage_report import fisher_exact, report

DATA = Path(__file__).resolve().parents[2] / "bench" / "data" / "plan_stage"


def test_fisher_exact_reproduces_the_number_d52_cites() -> None:
    assert round(fisher_exact(13, 264, 2, 274), 4) == 0.0067


@pytest.mark.parametrize("table,expected", [
    ((0, 100, 0, 100), 1.0),      # nothing happened in either arm
    ((5, 95, 5, 95), 1.0),        # identical arms
    ((10, 90, 0, 100), 0.00154),  # a difference this size at n=100 is not noise
    ((1, 9, 8, 2), 0.0055),       # the textbook 2x2, small and lopsided
])
def test_fisher_exact_against_known_tables(table, expected) -> None:
    assert fisher_exact(*table) == pytest.approx(expected, abs=5e-5)


def test_the_recorded_arms_still_read_out_the_way_d52_says() -> None:
    arms = {p.stem: [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
            for p in sorted(DATA.glob("restart_o*.jsonl"))}
    assert set(arms) == {"restart_off", "restart_on"} and all(len(v) == 280 for v in arms.values())
    text = report(arms)
    assert "Fisher exact two-sided p = 0.0067" in text
    assert "fired on 40/280 calls, recovered 39" in text
