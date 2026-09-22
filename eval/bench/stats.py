"""The statistics every bench report states, one rule each (docs/EVAL.md §8).

The interval is Student-t, ``mean ± t(0.975, n−1)·sd/√n``: a battery is 3–40 pairs, where
1.96 or 2 understate the 95 % half-width by 15–17 % at n = 8 and 37–38 % at n = 4, and t
tends to 1.96 as n grows.  *Separated* means the interval excludes zero.  The sign test is
exact and two-sided; correlation is ``statistics.correlation``.  (Wilson and Fisher stay in
``plan_stage_report.py``, the one report that states a rate.)
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

# two-sided 97.5 % Student-t quantiles by degrees of freedom (df 1..30, then 40/60/120, ∞);
# a table, not scipy: the [urdf] extra is optional and this must run on a [dev] install
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
         10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
         18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
         26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980}


def t975(df: int) -> float:
    """The 97.5 % Student-t quantile (between table rows, the next row up: conservative)."""
    if df <= 0:
        return math.nan
    if df in _T975:
        return _T975[df]
    for bound in (40, 60, 120):
        if df < bound:
            return _T975[bound]
    return 1.960


@dataclass(frozen=True)
class MeanCI:
    """A mean with its 95 % t-interval; ``sd`` / ``se`` / ``half`` are None below two values."""

    n: int
    mean: float
    sd: float | None = None
    se: float | None = None
    half: float | None = None

    @property
    def separated(self) -> bool:
        """The interval excludes zero: the change is outside the run-to-run noise."""
        return self.half is not None and abs(self.mean) > self.half


def mean_ci(xs: Sequence[float]) -> MeanCI:
    """The mean of ``xs`` (at least one value) and its 95 % t-interval."""
    mean = statistics.fmean(xs)
    if len(xs) < 2:
        return MeanCI(n=len(xs), mean=mean)
    sd = statistics.stdev(xs)
    se = sd / math.sqrt(len(xs))
    return MeanCI(n=len(xs), mean=mean, sd=sd, se=se, half=t975(len(xs) - 1) * se)


def n_to_resolve(sd: float, width: float) -> int:
    """How many pairs a spread of ``sd`` needs before the t-interval's half-width fits
    inside ``width`` — the honest answer to "would one more prompt settle this?"."""
    if sd <= 0:
        return 2
    n = max(2, math.ceil((1.96 * sd / width) ** 2))  # the large-n answer; t only adds to it
    while t975(n - 1) * sd / math.sqrt(n) > width:
        n += 1
    return n


def sign_test(deltas: Sequence[float]) -> tuple[int, int, float | None]:
    """``(up, down, p)``: the exact two-sided sign test on the non-zero deltas, ``p``
    rounded to 4 places and None when every delta is zero."""
    up = sum(d > 0 for d in deltas)
    down = sum(d < 0 for d in deltas)
    n = up + down
    if n == 0:
        return 0, 0, None
    tail = sum(math.comb(n, i) for i in range(max(up, down), n + 1)) / 2 ** n
    return up, down, round(min(1.0, 2 * tail), 4)


def correlation(xs: Sequence[float], ys: Sequence[float], *, ranked: bool = False) -> float | None:
    """Pearson r, or Spearman ρ with ``ranked`` (ties share their average rank)."""
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return statistics.correlation(xs, ys, method="ranked" if ranked else "linear")
