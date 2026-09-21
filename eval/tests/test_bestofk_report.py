"""``bench/bestofk_report.py``: the compute-matched baseline a harness paper has to answer.

A loop that plans, builds, gates and refines costs ~28x one raw generation on `compare_v4`
($0.9221 against $0.0327 median generation cost).  Spend that on 28 one-shot samples and
keep the best — does the harness still win?  These pin the three things the answer depends
on: which sample the curve takes, how a maximum over noisy scores is reported, and which
direction the difference is measured in.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from bench.bestofk_report import (
    JUDGE_SIGMA,
    best_of,
    curve,
    equal_compute_k,
    gen_costs,
    harness_scores,
    one_shot_samples,
    report,
)


def _rep(root: Path, name: str, scores: dict[str, float | None], *, extra: list[dict] | None = None) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"prompt_id": p, "arm": "oneshot:x", "score": s} for p, s in scores.items()]
    rows += extra or []
    (d / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


def test_best_of_takes_the_first_k_and_refuses_to_pad():
    """The reps are independent and unordered, so best-of-k is the best of the FIRST k —
    taking the best of everything available would report best-of-28 as best-of-4."""
    assert best_of([0.1, 0.9, 0.5], 2) == 0.9
    assert best_of([0.1, 0.9, 0.5], 3) == 0.9
    assert best_of([0.1, 0.9], 3) is None, "fewer samples than k is not a best-of-k"
    # order matters: the same multiset with the high draw late is a different best-of-2
    assert best_of([0.1, 0.5, 0.9], 2) == 0.5


def test_a_rep_that_scored_a_prompt_twice_counts_once(tmp_path: Path):
    """`results.jsonl` is append-only and a resumed cell writes a second row; the last one
    wins, exactly as `paired_compare` dedupes."""
    _rep(tmp_path, "rep01", {"a": 0.2}, extra=[{"prompt_id": "a", "arm": "oneshot:x", "score": 0.7}])
    assert one_shot_samples(tmp_path) == {"a": [0.7]}


def test_an_unscored_cell_is_dropped_not_zeroed(tmp_path: Path):
    """A cell with no score tests nothing about the model; scoring it 0 would make
    best-of-k look worse for free."""
    _rep(tmp_path, "rep01", {"a": None, "b": 0.4})
    assert one_shot_samples(tmp_path) == {"b": [0.4]}


def test_only_the_harness_arm_is_read_from_the_recorded_battery(tmp_path: Path):
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"prompt_id": "a", "arm": "oneshot:gemini:x", "score": 0.1},
        {"prompt_id": "a", "arm": "harness:api-agent:gemini:x", "score": 0.6},
        {"prompt_id": "b", "arm": "oneshot+repair:gemini:x", "score": 0.3},
    ]))
    assert harness_scores(tmp_path) == {"a": 0.6}


def test_the_difference_is_harness_minus_baseline_and_the_curse_is_reported():
    one = {"a": [0.1, 0.9], "b": [0.2, 0.3]}
    harness = {"a": 0.5, "b": 0.5}
    rows = {r["k"]: r for r in curve(one, harness, [1, 2])}

    # k=1 takes the first sample of each: 0.1 and 0.2, so the harness leads by 0.35
    assert rows[1]["best_of_k"] == pytest.approx(0.15) and rows[1]["delta"] == pytest.approx(0.35)
    assert rows[1]["wins"] == 2 and rows[1]["losses"] == 0
    # k=2 lets `a` draw its 0.9 and the harness LOSES that prompt
    assert rows[2]["best_of_k"] == pytest.approx(0.6) and rows[2]["delta"] == pytest.approx(-0.10)
    assert rows[2]["wins"] == 1 and rows[2]["losses"] == 1
    # the curse bound grows with k and is stated so a reader can discount the baseline
    assert rows[1]["curse"] == 0.0
    assert rows[2]["curse"] == pytest.approx(JUDGE_SIGMA * math.sqrt(2 * math.log(2)))


def test_a_prompt_the_harness_never_ran_is_not_paired(tmp_path: Path):
    _rep(tmp_path / "reps", "rep01", {"a": 0.2, "orphan": 0.9})
    (tmp_path / "results.jsonl").write_text(json.dumps(
        {"prompt_id": "a", "arm": "harness:x", "score": 0.6}))
    rows = curve(one_shot_samples(tmp_path / "reps"), harness_scores(tmp_path), [1])
    assert rows[0]["n"] == 1, "only prompts both arms ran are a pair"
    text = report(tmp_path / "reps", tmp_path)
    assert "| 1 | 1 |" in text


def test_equal_compute_k_is_taken_per_prompt_not_per_row(tmp_path: Path):
    """k is the whole experiment's parameter, and the first pass got it wrong: taking the
    median over every appended row (resumed cells, failed cells) gave 28 where the per-prompt
    basis — the one `paired_compare` pairs on — gives 40.  Understating k gives the baseline
    less money than the harness, an error in the harness's favour."""
    rows = [
        # prompt a: a cheap failed first attempt, then the real one.  Per row the median
        # would be dragged down; per prompt only the last row counts.
        {"prompt_id": "a", "arm": "harness:x", "gen_cost_usd": 0.10},
        {"prompt_id": "a", "arm": "harness:x", "gen_cost_usd": 2.00},
        {"prompt_id": "b", "arm": "harness:x", "gen_cost_usd": 2.00},
        {"prompt_id": "a", "arm": "oneshot:x", "gen_cost_usd": 0.05},
        {"prompt_id": "b", "arm": "oneshot:x", "gen_cost_usd": 0.05},
    ]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    assert sorted(gen_costs(tmp_path, "harness:")) == [2.00, 2.00], "last row per prompt"
    h, b, k = equal_compute_k(tmp_path, "oneshot:")
    assert (h, b, k) == (2.00, 0.05, 40)


def test_a_zero_cost_row_is_not_counted_as_free(tmp_path: Path):
    """A row with no recorded generation cost is missing data, not a free sample; averaging
    it in would inflate k without bound."""
    rows = [
        {"prompt_id": "a", "arm": "oneshot:x", "gen_cost_usd": 0.05},
        {"prompt_id": "b", "arm": "oneshot:x", "gen_cost_usd": 0},
        {"prompt_id": "c", "arm": "oneshot:x"},
    ]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    assert gen_costs(tmp_path, "oneshot:") == [0.05]


def test_the_report_says_when_k_has_not_been_reached(tmp_path: Path):
    """A curve that stops short of equal compute answers a different question, so the table
    says so instead of letting the last row read as the verdict."""
    _rep(tmp_path / "reps", "rep01", {"a": 0.2})
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"prompt_id": "a", "arm": "harness:x", "score": 0.6, "gen_cost_usd": 2.00},
        {"prompt_id": "a", "arm": "oneshot:x", "score": 0.1, "gen_cost_usd": 0.05},
    ]))
    text = report(tmp_path / "reps", tmp_path)
    assert "**k = 40**" in text and "NOT reached yet — 1 of 40 reps" in text


def test_repeated_harness_rows_are_reported_as_duplicates_or_reruns(tmp_path: Path):
    """Whether the aggregation choice can move the comparison is a fact about the data, so
    the report states it.  On `compare_v4` the 117 harness rows over 40 prompts have a
    within-prompt spread of exactly 0.000 — they are the same cell written by successive
    report passes, not re-runs — so last-row-wins is safe there."""
    from bench.bestofk_report import harness_repeat_spread

    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"prompt_id": "a", "arm": "harness:x", "score": 0.5},
        {"prompt_id": "a", "arm": "harness:x", "score": 0.5},   # duplicate
        {"prompt_id": "b", "arm": "harness:x", "score": 0.4},
        {"prompt_id": "c", "arm": "oneshot:x", "score": 0.9},   # other arms ignored
    ]))
    assert harness_repeat_spread(tmp_path) == (3, 2, 0.0)

    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"prompt_id": "a", "arm": "harness:x", "score": 0.5},
        {"prompt_id": "a", "arm": "harness:x", "score": 0.8},   # a real re-run
    ]))
    rows, prompts, spread = harness_repeat_spread(tmp_path)
    assert (rows, prompts) == (2, 1) and spread == pytest.approx(0.3)
