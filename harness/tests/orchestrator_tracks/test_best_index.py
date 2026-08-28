"""``orchestrator.rounds.best_index`` — the ranking rule BestSelector delegates to
(moved here from tests/judges/test_metrics.py when judges/metrics.py was deleted)."""

from __future__ import annotations

import pytest

from codeverse.orchestrator.rounds import best_index


def test_best_index():
    assert best_index([(0.5, 0), (0.7, 2), (0.7, 1), (0.6, 0)]) == 2
    assert best_index([(0.7, 1), (0.7, 1)]) == 1  # later wins ties
    assert best_index([(0.9, 3), (0.8, 0)]) == 0
    with pytest.raises(ValueError):
        best_index([])
