"""``bench/bestofk_report.py``: the compute-matched best-of-k baseline — which sample, which k, which sign."""

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
    """Best-of-k is the best of the FIRST k — never of everything available."""
    assert best_of([0.1, 0.9, 0.5], 2) == 0.9
    assert best_of([0.1, 0.9, 0.5], 3) == 0.9
    assert best_of([0.1, 0.9], 3) is None, "fewer samples than k is not a best-of-k"
    # order matters: the same multiset with the high draw late is a different best-of-2
    assert best_of([0.1, 0.5, 0.9], 2) == 0.5


def test_an_unscored_cell_is_dropped_not_zeroed(tmp_path: Path):
    """A cell with no score tests nothing: scoring it 0 would handicap best-of-k."""
    _rep(tmp_path, "rep01", {"a": None, "b": 0.4})
    assert one_shot_samples(tmp_path) == {"b": [0.4]}


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
    """The median over every appended row gave k=28 where the per-prompt basis gives 40."""
    rows = [
        # prompt a: a cheap failed first attempt, then the real one; only the last row counts
        {"prompt_id": "a", "arm": "harness:x", "gen_cost_usd": 0.10},
        {"prompt_id": "a", "arm": "harness:x", "gen_cost_usd": 2.00},
        {"prompt_id": "b", "arm": "harness:x", "gen_cost_usd": 2.00},
        {"prompt_id": "a", "arm": "oneshot:x", "gen_cost_usd": 0.05},
        {"prompt_id": "b", "arm": "oneshot:x", "gen_cost_usd": 0.05},
        # no recorded generation cost is missing data, not free: counting it would inflate k
        {"prompt_id": "c", "arm": "oneshot:x", "gen_cost_usd": 0},
        {"prompt_id": "d", "arm": "oneshot:x"},
    ]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    assert sorted(gen_costs(tmp_path, "harness:")) == [2.00, 2.00], "last row per prompt"
    assert gen_costs(tmp_path, "oneshot:") == [0.05, 0.05]
    h, b, k = equal_compute_k(tmp_path, "oneshot:")
    assert (h, b, k) == (2.00, 0.05, 40)


def test_the_report_says_when_k_has_not_been_reached(tmp_path: Path):
    """A curve short of equal compute says so, so the last row does not read as the verdict."""
    _rep(tmp_path / "reps", "rep01", {"a": 0.2})
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"prompt_id": "a", "arm": "harness:x", "score": 0.6, "gen_cost_usd": 2.00},
        {"prompt_id": "a", "arm": "oneshot:x", "score": 0.1, "gen_cost_usd": 0.05},
    ]))
    text = report(tmp_path / "reps", tmp_path)
    assert "**k = 40**" in text and "NOT reached yet — 1 of 40 reps" in text


def test_repeated_harness_rows_are_reported_as_duplicates_or_reruns(tmp_path: Path):
    """The report states whether repeated harness rows are duplicates (spread 0) or real re-runs."""
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
