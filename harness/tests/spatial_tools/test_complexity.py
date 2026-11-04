"""The objective complexity vector: the index is the weighted sum of its axes, the bands
are ordered, and degenerate input never raises."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.spatial.complexity import (
    COMPLEXITY_WEIGHTS,
    band_of,
    complexity_of_glb,
)


def test_vector_shape_and_weights(stool_glb: Path) -> None:
    vec = complexity_of_glb(stool_glb)
    assert set(COMPLEXITY_WEIGHTS) == set(vec.components)
    assert sum(COMPLEXITY_WEIGHTS.values()) == pytest.approx(1.0)
    assert vec.part_count == 5 and vec.tri_count > 0
    assert 0.0 < vec.index < 1.0
    assert all(0.0 <= v <= 1.0 for v in vec.components.values())
    assert vec.band == band_of(vec.index)
    assert vec.index == pytest.approx(
        sum(COMPLEXITY_WEIGHTS[a] * v for a, v in vec.components.items()), abs=5e-4
    )
    labels = [band_of(x) for x in (0.0, 0.3, 0.45, 0.6, 0.9, 1.0)]
    assert labels == ["trivial", "simple", "moderate", "complex", "intricate", "intricate"]
