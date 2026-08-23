"""Small, pure helpers over judgments and round scores (no I/O, no models)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from pydantic import BaseModel, Field

from codeverse.contracts.judgment import Judgment


class Agreement(BaseModel):
    mean: float
    std: float
    per_criterion_std: dict[str, float] = Field(default_factory=dict)
    n: int


def judge_agreement(judgments: Sequence[Judgment]) -> Agreement:
    """Mean/std of ``overall`` and per-criterion std across repeated judgments of the same thing."""
    if not judgments:
        raise ValueError("judge_agreement needs at least one judgment")
    overalls = [j.overall for j in judgments]
    crit: dict[str, list[float]] = {}
    for j in judgments:
        for k, v in j.scores.items():
            crit.setdefault(k, []).append(float(v))
    return Agreement(
        mean=round(statistics.fmean(overalls), 4),
        std=round(statistics.pstdev(overalls), 4) if len(overalls) > 1 else 0.0,
        per_criterion_std={k: (round(statistics.pstdev(v), 4) if len(v) > 1 else 0.0) for k, v in crit.items()},
        n=len(judgments),
    )


def plateau(scores: Sequence[float], window: int = 2, min_delta: float = 0.02) -> bool:
    """True when the best of the last ``window`` scores did not beat the best before them by ≥ ``min_delta``.

    Needs at least ``window + 1`` scores; fewer → False (keep going).
    """
    if window < 1:
        raise ValueError("window must be ≥ 1")
    if len(scores) < window + 1:
        return False
    before = max(scores[:-window])
    recent = max(scores[-window:])
    return (recent - before) < min_delta


def best_index(rounds: Sequence[tuple[float, int]]) -> int:
    """Index of the best round: higher score, tie → fewer errors, tie → later round."""
    if not rounds:
        raise ValueError("best_index needs at least one round")
    best = 0
    for i, (score, n_err) in enumerate(rounds):
        bs, be = rounds[best]
        if score > bs or (score == bs and n_err < be) or (score == bs and n_err == be):
            best = i
    return best
