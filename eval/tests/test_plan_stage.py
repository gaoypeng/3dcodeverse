"""The plan-stage report: the Fisher p-value D52 cites and the readouts computed from the recorded calls."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from bench.plan_stage_report import failure_class, fisher_exact, outcome, report

DATA = Path(__file__).resolve().parents[1] / "bench" / "data" / "plan_stage"


@pytest.mark.parametrize("table,expected", [
    ((13, 264, 2, 274), 0.0067),  # the number D52 cites
    ((5, 95, 5, 95), 1.0),        # identical arms
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
    """The 2026-09-03 three-arm readout turns on a failure CLASS; this pins the number the docs quote."""
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
    """``provider`` leaves the denominator, so a budget ceiling or harness exception must stay a loss."""
    rows = [json.loads(x) for x in (DATA / "trigger_narrow.jsonl").read_text().splitlines() if x.strip()]
    budget = [r for r in rows if "BudgetExceeded" in r.get("error", "")]
    assert len(budget) == 1
    assert outcome(budget[0]) == "planning_error" and failure_class(budget[0]) == "other"

    assert outcome({"ok": False, "error": "ModelError: Gemini prompt blocked: BlockedReason.OTHER"}) == "provider"
    assert outcome({"ok": False, "error": "KeyError: 'parts'"}) == "planning_error"
    # one outage rule with the bench (N76): a model's own bad answer is a loss, a spent key pool is weather
    for err, want in (("ModelError: Gemini returned no JSON (finish_reason=MAX_TOKENS)", "planning_error"),
                      ("ModelError: HTTP 400 invalid argument", "planning_error"),
                      ("KeyPoolExhausted: all 15 keys cooling", "provider")):
        assert outcome({"ok": False, "error": err}) == want, err


def test_a_killed_arm_file_still_reports(tmp_path: Path, capsys) -> None:
    """A half-written last row from a killed bench does not make the arm unreadable."""
    from bench.plan_stage_report import main

    arm = tmp_path / "arm.jsonl"
    arm.write_text(json.dumps({"prompt": "p", "rep": 0, "ok": True, "error": ""}) + '\n{"prompt": "p", "re')
    assert main([str(arm)]) == 0
    assert "| arm | 1 | 1 | 0 | 0 | 0.000 |" in capsys.readouterr().out
