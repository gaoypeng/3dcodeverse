"""``bench/stats.py``: the one interval, sign test and correlation every bench report states."""

from __future__ import annotations

import math

import pytest

from bench.stats import correlation, mean_ci, n_to_resolve, sign_test, t975


def test_t_quantiles_are_monotone_and_end_at_normal():
    assert t975(1) > t975(5) > t975(30) > t975(59) > t975(1000) == 1.96
    assert t975(0) != t975(0)  # nan for no degrees of freedom


def test_the_interval_is_t_times_se_and_separation_means_it_excludes_zero():
    ci = mean_ci([0.10, 0.11, 0.09, 0.12])
    assert ci.se == pytest.approx(ci.sd / 2) and ci.half == pytest.approx(t975(3) * ci.se)
    assert ci.separated
    assert not mean_ci([0.3, -0.2, 0.1, -0.1]).separated
    one = mean_ci([0.344])
    assert (one.sd, one.se, one.half, one.separated) == (None, None, None, False), "one pair has no interval"


def test_n_to_resolve_is_the_first_n_whose_interval_fits():
    n = n_to_resolve(0.226, 0.02)
    assert t975(n - 1) * 0.226 / math.sqrt(n) <= 0.02 < t975(n - 2) * 0.226 / math.sqrt(n - 1)
    assert n_to_resolve(1.1547, 1.0) == 8 and n_to_resolve(0.0, 0.02) == 2


def test_the_sign_test_is_exact_two_sided_and_drops_zeros():
    assert sign_test([0.1] * 8) == (8, 0, round(2 / 2 ** 8, 4))
    assert sign_test([0.2, 0.1, -0.1, 0.0]) == (2, 1, 1.0)
    assert sign_test([0.0, 0.0]) == (0, 0, None)


def test_correlation_needs_three_points_and_a_spread():
    assert correlation([1, 2, 3], [2, 4, 6.5]) == pytest.approx(4.5 / math.sqrt(2 * 61 / 6))
    assert correlation([1, 2, 3], [3, 1, 2], ranked=True) == pytest.approx(-0.5)
    assert correlation([1, 2], [1, 2]) is None and correlation([1, 1, 1], [1, 2, 3]) is None


@pytest.mark.parametrize("deltas, outcome", [
    ((0.25, 0.01, 0.00, 0.02), "inconclusive"),   # N84: keep / unsupported / inconclusive before
    ((0.10, 0.11, 0.09, 0.12), "better"),
    ((-0.10, -0.12, -0.09), "worse"),
    ((0.30, 0.30), "inconclusive"),               # below MIN_PAIRS: no winner, however consistent
])
def test_every_paired_report_decides_by_one_rule(deltas, outcome):
    """The A/B summary, the harness-vs-one-shot table and the blind A/B page reach one decision."""
    from bench._ab_report import PairOutcome, verdict_of
    from bench._compare_report import CellResult
    from bench.ab_view import Run
    from bench.ab_view import verdict as ab_view_verdict
    from bench.paired_compare import paired
    from bench.stats import decide

    assert decide({f"p{i}": d for i, d in enumerate(deltas)}).outcome == outcome
    ab = verdict_of([PairOutcome(prompt_id=f"p{i}", paired=True, delta=d) for i, d in enumerate(deltas)])
    assert ab.decision == {"better": "keep", "worse": "revert"}.get(outcome, "inconclusive")
    cells = {}
    for i, d in enumerate(deltas):
        for arm, score in (("harness:h", 0.5 + d), ("oneshot:o", 0.5)):
            cells[(f"p{i}", arm)] = CellResult(prompt_id=f"p{i}", arm=arm, kind=arm.split(":")[0], score=score, status="scored")
    assert paired(cells, "harness:h", "oneshot:o").verdict == (outcome if len(deltas) >= 3 else "too few pairs")
    a = [Run(slug=f"a{i}", arm="a", brief=f"b{i}", picked=0.5) for i in range(len(deltas))]
    b = [Run(slug=f"b{i}", arm="b", brief=f"b{i}", picked=0.5 + d) for i, d in enumerate(deltas)]
    head, _ = ab_view_verdict(a, b)
    assert head.startswith({"better": "B wins", "worse": "B loses"}.get(outcome, "Inconclusive")), head
