import pytest

from codeverse.contracts.judgment import Judgment
from codeverse.judges.metrics import best_index, judge_agreement, plateau


def _j(o, **sc):
    return Judgment(rubric="r", scores=sc or {"a": o}, overall=o, passed=o > 0.7)


def test_agreement():
    a = judge_agreement([_j(0.7, a=0.6, b=0.8), _j(0.9, a=0.8, b=1.0)])
    assert a.mean == 0.8 and a.std == pytest.approx(0.1) and a.per_criterion_std == {"a": 0.1, "b": 0.1} and a.n == 2
    assert judge_agreement([_j(0.5)]).std == 0.0
    with pytest.raises(ValueError):
        judge_agreement([])


def test_plateau():
    assert plateau([0.5, 0.6]) is False  # too few
    assert plateau([0.5, 0.6, 0.61, 0.6]) is True
    assert plateau([0.5, 0.6, 0.61, 0.7]) is False
    assert plateau([0.5, 0.52, 0.53], window=2, min_delta=0.02) is False  # 0.53-0.5 = 0.03 ≥ 0.02
    assert plateau([0.5, 0.51, 0.515], window=2, min_delta=0.02) is True


def test_best_index():
    assert best_index([(0.5, 0), (0.7, 2), (0.7, 1), (0.6, 0)]) == 2
    assert best_index([(0.7, 1), (0.7, 1)]) == 1  # later wins ties
    assert best_index([(0.9, 3), (0.8, 0)]) == 0
    with pytest.raises(ValueError):
        best_index([])
