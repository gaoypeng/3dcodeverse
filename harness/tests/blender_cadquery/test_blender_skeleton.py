"""Offline tests for the bpy skeleton generator."""

from __future__ import annotations

import ast

from codeverse.contracts.plan import BBox
from codeverse.languages.blender.lint import lint_blender_source
from codeverse.languages.blender.skeleton import (
    blender_skeleton_source,
    finish_for,
    instance_centers,
    write_blender_skeleton,
)


def test_skeleton_parses_lints_and_names_parts(table_plan) -> None:
    src = blender_skeleton_source(table_plan)
    ast.parse(src)
    assert "import codeverse" not in src and "from codeverse" not in src
    for fn in ("def build_table_top(", "def build_leg(", "def build_shelf(", "def main():"):
        assert fn in src
    assert 'name = "TableTop"' in src and 'name = f"Leg_{index}"' in src and 'add_empty("Legs")' in src
    assert "Z is up, -Y is the FRONT" in src and "[a1]" in src
    r = lint_blender_source(src)
    assert r.passed, [f.message for f in r.findings]


def test_write_skeleton(tmp_ws, table_plan) -> None:
    paths = write_blender_skeleton(tmp_ws, table_plan)
    assert paths == [tmp_ws.src / "model.py"] and paths[0].is_file()


def test_instance_centers_symmetry() -> None:
    bb = BBox(center=(0.2, 0.3, 0.5), extents=(0.1, 0.1, 1.0))
    assert instance_centers(bb, 1, "none") == [(0.2, 0.3, 0.5)]
    assert instance_centers(bb, 2, "mirror_x") == [(0.2, 0.3, 0.5), (-0.2, 0.3, 0.5)]
    four = instance_centers(bb, 4, "mirror_y")
    assert len(four) == 4 and {(round(x, 3), round(y, 3)) for x, y, _ in four} == {(0.2, -0.3), (-0.2, -0.3), (-0.2, 0.3), (0.2, 0.3)}
    rad = instance_centers(BBox(center=(0.5, 0.0, 0.1), extents=(0.1, 0.1, 0.1)), 3, "radial")
    assert len(rad) == 3 and all(abs((x * x + y * y) ** 0.5 - 0.5) < 1e-9 for x, y, _ in rad)


def test_finish_for_keywords() -> None:
    assert finish_for("brushed steel")[2] == 1.0
    assert finish_for("oak wood")[0] == (0.55, 0.36, 0.20)
    assert finish_for("something unknown")[0] == (0.6, 0.6, 0.6)
