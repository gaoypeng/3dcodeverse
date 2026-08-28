"""Offline tests for the bpy skeleton generator (multi-file default + single-file variant)."""

from __future__ import annotations

import ast

from codeverse.contracts.plan import BBox
from codeverse.languages.blender import (
    blender_skeleton_source,
    finish_for,
    instance_centers,
    lint_blender_source,
    lint_workspace,
    model_file_source,
    part_file_source,
    write_blender_skeleton,
)


def test_single_file_skeleton_parses_lints_and_names_parts(table_plan) -> None:
    src = blender_skeleton_source(table_plan)
    ast.parse(src)
    assert "import codeverse" not in src and "from codeverse" not in src and "from parts" not in src
    for fn in ("def build_table_top(", "def build_leg(", "def build_shelf(", "def main():", "def _selfcheck("):
        assert fn in src
    assert 'add_box("TableTop"' in src and 'add_box(f"Leg_{i}"' in src
    assert "add_empty" not in src and ".parent" not in src  # instances stay TOP-LEVEL (measured as parts)
    assert "Z is up, -Y is the FRONT" in src and "[a1]" in src and "LEG_EXTENTS = (0.040, 0.040, 0.560)" in src
    r = lint_blender_source(src)
    assert r.passed, [f.message for f in r.findings]


def test_part_file_source_is_self_contained(table_plan) -> None:
    leg = table_plan.parts[1]
    src = part_file_source(leg)
    tree = ast.parse(src)
    assert "def build_leg():" in src and "def make_material(" in src and "def add_box(" in src
    assert "LEG_CENTER = (0.210, 0.210, 0.280)" in src and "LEG_INSTANCES = 4" in src
    assert "tapered square leg" in src and "support" in src and "Attaches to: TableTop" in src and "oak" in src
    assert "(0.550, 0.360, 0.200)" in src  # oak colour hint
    # defines builders only — nothing is built at import time
    calls = [n.value.func for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
    assert [getattr(f, "id", "") for f in calls if isinstance(f, ast.Name)] == []  # only random.seed(0)
    assert lint_blender_source(src, target="src/parts/leg.py").passed


def test_model_file_source_imports_and_calls_in_plan_order(table_plan) -> None:
    src = model_file_source(table_plan)
    ast.parse(src)
    imports = [ln for ln in src.splitlines() if ln.startswith("from parts.")]
    assert imports == ["from parts.table_top import build_table_top", "from parts.leg import build_leg",
                       "from parts.shelf import build_shelf"]
    body = src[src.index("def main():"):]
    assert body.index("build_table_top()") < body.index("build_leg()") < body.index("build_shelf()") < body.index("_selfcheck()")
    assert "add_box(" not in src  # no geometry in the entry
    assert "[src/parts/leg.py]" in src  # parts table points at the files


def test_write_skeleton_multi_file_and_workspace_lint(tmp_ws, table_plan) -> None:
    paths = write_blender_skeleton(tmp_ws, table_plan)
    rel = [p.relative_to(tmp_ws.root).as_posix() for p in paths]
    assert rel == ["src/model.py", "src/parts/table_top.py", "src/parts/leg.py", "src/parts/shelf.py"]
    assert all(p.is_file() for p in paths)
    rep = lint_workspace(tmp_ws)
    assert rep.passed, [(f.target, f.message) for f in rep.errors]
    assert not [f for f in rep.findings if f.severity.value == "warn"], [f.message for f in rep.findings]


def test_write_skeleton_single_file(tmp_ws, table_plan) -> None:
    paths = write_blender_skeleton(tmp_ws, table_plan, multi_file=False)
    assert paths == [tmp_ws.src / "model.py"] and paths[0].is_file()
    assert lint_workspace(tmp_ws).passed


def test_instance_centers_symmetry() -> None:
    bb = BBox(center=(0.2, 0.3, 0.5), extents=(0.1, 0.1, 1.0))
    assert instance_centers(bb, 1, "none") == [(0.2, 0.3, 0.5)]
    assert instance_centers(bb, 2, "mirror_x") == [(0.2, 0.3, 0.5), (-0.2, 0.3, 0.5)]
    four = instance_centers(bb, 4, "mirror_y")
    assert len(four) == 4 and {(round(x, 3), round(y, 3)) for x, y, _ in four} == {(0.2, -0.3), (-0.2, -0.3), (-0.2, 0.3), (0.2, 0.3)}
    rad = instance_centers(BBox(center=(0.5, 0.0, 0.1), extents=(0.1, 0.1, 0.1)), 3, "radial")
    assert len(rad) == 3 and all(abs((x * x + y * y) ** 0.5 - 0.5) < 1e-9 for x, y, _ in rad)


def test_instances_are_top_level_no_empty_parent(table_plan) -> None:
    """Finding: an Empty parent merges instances into ONE measured part → contract 'missing'."""
    leg = table_plan.parts[1]
    src = part_file_source(leg)
    assert "add_empty" not in src and "obj.parent" not in src
    assert 'add_box(f"Leg_{i}"' in src and "return objs" in src
    assert "TOP-LEVEL" in src  # docstring warns the agent off the Empty pattern
    single = blender_skeleton_source(table_plan)
    assert "add_empty" not in single and "obj.parent" not in single


def test_finish_for_keywords() -> None:
    assert finish_for("brushed steel")[2] == 1.0
    assert finish_for("oak wood")[0] == (0.55, 0.36, 0.20)
    assert finish_for("something unknown")[0] == (0.6, 0.6, 0.6)
