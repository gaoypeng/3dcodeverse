"""The objective complexity vector: the index is the weighted sum of its axes, the bands
are ordered, and degenerate input never raises."""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from codeverse3d.spatial.complexity import (
    COMPLEXITY_WEIGHTS,
    band_of,
    complexity_of_glb,
    complexity_of_parts,
)
from codeverse3d.spatial.measure import part_meshes


def test_vector_shape_and_weights(stool_glb: Path) -> None:
    vec = complexity_of_glb(stool_glb)
    assert set(COMPLEXITY_WEIGHTS) == set(vec.components) == set(vec.axes)
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


def test_degenerate_input_never_raises() -> None:
    empty = complexity_of_parts({})
    assert empty.index == 0.0 and empty.extra["error"] == "no geometry"
    none_only = complexity_of_parts({"Ghost": None})
    assert none_only.part_count == 0


def test_symmetry_groups_count_geometric_families(solid_stool_glb: Path) -> None:
    parts = part_meshes(trimesh.load(str(solid_stool_glb), force="scene", process=False))
    vec = complexity_of_parts(parts)
    assert vec.symmetry_groups == 1  # the four legs are one family; the seat is alone
    assert vec.extra["repeated_parts"] == 4

