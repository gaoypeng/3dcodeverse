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
