"""Offline tests for the bpy skeleton generator."""

from __future__ import annotations

import ast

import pytest

from codeverse3d.contracts.plan import BBox
from codeverse3d.languages.blender import (
    finish_for,
    instance_centers,
    lint_blender_source,
    part_file_source,
)


def test_part_file_source_is_self_contained(table_plan) -> None:
    leg = table_plan.parts[1]
    src = part_file_source(leg)
    tree = ast.parse(src)
    assert "def build_leg():" in src and "def make_material(" in src and "def add_box(" in src
    assert "LEG_CENTER = (0.210, 0.210, 0.280)" in src and "LEG_INSTANCES = 4" in src
    assert "tapered square leg" in src and "support" in src and "Attaches to: TableTop" in src and "oak" in src
    assert "(0.340, 0.200, 0.100)" in src  # oak: the hardwood base hint (texturing.materials)
    assert "add_empty" not in src and "obj.parent" not in src
    assert 'add_box(f"Leg_{i}"' in src and "return objs" in src and "TOP-LEVEL" in src
    # defines builders only — nothing is built at import time
    calls = [n.value.func for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    assert [getattr(f, "id", "") for f in calls if isinstance(f, ast.Name)] == []  # only random.seed(0)
    assert lint_blender_source(src, target="src/parts/leg.py").passed


def test_instance_centers_symmetry() -> None:
    bb = BBox(center=(0.2, 0.3, 0.5), extents=(0.1, 0.1, 1.0))
    assert instance_centers(bb, 1, "none") == [(0.2, 0.3, 0.5)]
    assert instance_centers(bb, 2, "mirror_x") == [(0.2, 0.3, 0.5), (-0.2, 0.3, 0.5)]
    four = instance_centers(bb, 4, "mirror_y")
    assert len(four) == 4 and {(round(x, 3), round(y, 3)) for x, y, _ in four} == {(0.2, -0.3), (-0.2, -0.3), (-0.2, 0.3), (0.2, 0.3)}
    rad = instance_centers(BBox(center=(0.5, 0.0, 0.1), extents=(0.1, 0.1, 0.1)), 3, "radial")
    assert len(rad) == 3 and all(abs((x * x + y * y) ** 0.5 - 0.5) < 1e-9 for x, y, _ in rad)


@pytest.mark.parametrize("text, family", [("solid oak", "hardwood"), ("brushed steel", "brushed_metal"),
                                          ("ABS plastic", "rough_plastic"), ("black", None)])
def test_placeholder_finish_is_the_pbr_table(text, family) -> None:
    """Audit 2026-09-24 N54: the skeleton's finishes had their own numbers (wood 0.55 vs the
    table's 0.45, steel 0.35 vs 0.22, plastic 0.40 vs 0.62)."""
    from codeverse3d.texturing.materials import MATERIALS

    rgb, rough, metal = finish_for(text)
    if family is None:
        assert (rough, metal) == (0.5, 0.0) and rgb == (0.05, 0.05, 0.05)
    else:
        pbr = MATERIALS[family]
        assert (rgb, rough, metal) == (pbr.base_hint, pbr.roughness, pbr.metallic)
