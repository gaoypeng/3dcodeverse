"""The loss-event channel's arithmetic: the p-value a paper cites is computed here.

``bench/data/plan_stage/*.jsonl`` carries the 560 recorded plan calls behind D52; this
pins the statistic that turns them into 0.0067, and the report that prints it."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from bench.plan_stage_report import failure_class, fisher_exact, outcome, report

DATA = Path(__file__).resolve().parents[1] / "bench" / "data" / "plan_stage"


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


def test_the_failure_class_split_the_re_measurement_reports_is_computed_here() -> None:
    """The 2026-09-03 three-arm readout turns on a CLASS, not the total: the restart only
    ever addresses a plan that references links it never lists.  That split was derived by
    hand for the first write-up; it is the report's job, and this pins the number the docs
    quote."""
    arms = {p.stem.replace("trigger_", ""): [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
            for p in sorted(DATA.glob("trigger_*.jsonl"))}
    assert set(arms) == {"narrow", "off", "wide"}

    classes = {name: Counter(failure_class(r) for r in rows if outcome(r) == "planning_error")
               for name, rows in arms.items()}
    assert classes["off"]["dangling_link"] == 3 and classes["wide"]["dangling_link"] == 4
    assert classes["narrow"]["dangling_link"] == 0, "the class the mechanism addresses"
    assert [classes[a]["parent_eq_child"] for a in ("off", "wide", "narrow")] == [1, 2, 4]

    judged = {n: sum(1 for r in rows if outcome(r) != "provider") for n, rows in arms.items()}
    p = fisher_exact(3, judged["off"] - 3, 0, judged["narrow"])
    assert round(p, 4) == 0.0376

    text = report(arms)
    assert "| arm | dangling_link | parent_eq_child | other |" in text
    assert "dangling_link: narrow 0/276 vs off 3/140  Fisher p = 0.0376" in text


def test_a_harness_side_death_is_a_loss_not_provider_weather() -> None:
    """``provider`` drops a row from the denominator, so it has to name the provider
    failures rather than catch everything that is not a PlanningError.  A budget ceiling
    and a harness exception are losses of the run: trigger_narrow carries one
    BudgetExceeded row, and classing it as weather would have hidden it."""
    rows = [json.loads(x) for x in (DATA / "trigger_narrow.jsonl").read_text().splitlines() if x.strip()]
    budget = [r for r in rows if "BudgetExceeded" in r.get("error", "")]
    assert len(budget) == 1
    assert outcome(budget[0]) == "planning_error" and failure_class(budget[0]) == "other"

    assert outcome({"ok": False, "error": "ModelError: Gemini prompt blocked: BlockedReason.OTHER"}) == "provider"
    assert outcome({"ok": False, "error": "KeyError: 'parts'"}) == "planning_error"


def test_a_killed_arm_file_still_reports(tmp_path: Path, capsys) -> None:
    """The report's loader parsed every line strictly: the half-written row a killed
    ``plan_stage_bench`` leaves made the whole arm unreadable."""
    from bench.plan_stage_report import main

    arm = tmp_path / "arm.jsonl"
    arm.write_text(json.dumps({"prompt": "p", "rep": 0, "ok": True, "error": ""}) + '\n{"prompt": "p", "re')
    assert main([str(arm)]) == 0
    assert "| arm | 1 | 1 | 0 | 0 | 0.000 |" in capsys.readouterr().out
