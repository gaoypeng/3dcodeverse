"""Offline tests for the multi-file layout: part-file mapping, workspace lint rules, and the
wrapper's error→file:line mapping (the wrapper is importable from host python; no bpy needed)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from codeverse.languages.blender import (
    WRAPPER,
    build_fn_name,
    lint_workspace,
    part_file_rel,
    part_files,
    source_files,
)

SEAT = '''import bpy, bmesh

def build_seat():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.3)
    me = bpy.data.meshes.new("Seat"); bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new("Seat", me)
    bpy.context.scene.collection.objects.link(obj)
    return obj
'''
MODEL = '''import bpy
from parts.seat import build_seat
import parts.leg

def main():
    build_seat()
    parts.leg.build_leg()

main()
'''
LEG = SEAT.replace("build_seat", "build_leg").replace("Seat", "Leg")


def _write(ws, rel: str, text: str) -> Path:
    p = ws.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_part_file_mapping_uses_the_name_normaliser() -> None:
    assert part_file_rel("Seat Cushion") == "src/parts/seat_cushion.py"
    assert part_file_rel("LeftFrontLeg") == "src/parts/left_front_leg.py"
    assert build_fn_name("SeatCushion") == "build_seat_cushion"


def test_file_listing_excludes_helpers_and_orders_entry_first(tmp_ws) -> None:
    _write(tmp_ws, "src/model.py", MODEL)
    _write(tmp_ws, "src/parts/seat.py", SEAT)
    _write(tmp_ws, "src/parts/leg.py", LEG)
    _write(tmp_ws, "src/parts/_common.py", "def helper():\n    return 1\n")
    assert [p.name for p in part_files(tmp_ws)] == ["leg.py", "seat.py"]
    rel = [p.relative_to(tmp_ws.root).as_posix() for p in source_files(tmp_ws)]
    assert rel == ["src/model.py", "src/parts/_common.py", "src/parts/leg.py", "src/parts/seat.py"]


def test_lint_workspace_passes_clean_multi_file_layout(tmp_ws) -> None:
    _write(tmp_ws, "src/model.py", MODEL)
    _write(tmp_ws, "src/parts/seat.py", SEAT)
    _write(tmp_ws, "src/parts/leg.py", LEG)
    _write(tmp_ws, "src/parts/_common.py", "def helper():\n    return 1\n")  # no bpy import is fine for helpers
    rep = lint_workspace(tmp_ws)
    assert rep.passed, [(f.target, f.message) for f in rep.errors]
    # the entry only imports + calls → no PascalCase-name warning for it
    assert not [f for f in rep.findings if f.target == "src/model.py" and f.severity.value == "warn"]
    assert {f.target for f in rep.findings} <= {"src/model.py", "src/parts/seat.py", "src/parts/leg.py", "src/parts/_common.py"}


def test_lint_workspace_reports_a_source_too_deep_to_parse_instead_of_crashing(tmp_ws) -> None:
    """The layout rules re-parsed every file with a bare ``ast.parse`` — a literal nested past the
    parser's stack raised ``MemoryError`` out of the lint and killed the round."""
    deep = "x = " + "-" * 20_000 + "1\n"
    _write(tmp_ws, "src/model.py", deep)
    _write(tmp_ws, "src/parts/seat.py", deep)
    rep = lint_workspace(tmp_ws)
    assert not rep.passed and {f.target for f in rep.errors} >= {"src/model.py", "src/parts/seat.py"}


def test_lint_workspace_layout_rules(tmp_ws) -> None:
    _write(tmp_ws, "src/model.py", MODEL)
    _write(tmp_ws, "src/parts/seat.py", SEAT.replace("def build_seat", "def build_seat_cushion"))  # wrong export
    _write(tmp_ws, "src/parts/leg.py", LEG + "\nbuild_leg()\n")  # builds at import time
    _write(tmp_ws, "src/parts/back_rest.py", SEAT.replace("build_seat", "build_back_rest"))  # never imported
    _write(tmp_ws, "src/parts/ArmRest.py", SEAT.replace("build_seat", "build_arm_rest"))  # not snake_case
    _write(tmp_ws, "src/parts/bad.py", "import bpy\ndef build_bad(:\n")  # syntax error
    rep = lint_workspace(tmp_ws)
    errors = {(f.target, f.message) for f in rep.errors}
    assert not rep.passed
    assert ("src/parts/seat.py", "src/parts/seat.py does not define `def build_seat()`") in errors
    assert any(t == "src/parts/ArmRest.py" and "snake_case" in m for t, m in errors)
    assert any(t == "src/parts/bad.py" and "SyntaxError" in m for t, m in errors)
    warns = {(f.target, f.message) for f in rep.findings if f.severity.value == "warn"}
    assert any(t == "src/parts/leg.py" and "import time" in m for t, m in warns)
    assert any(t == "src/parts/back_rest.py" and "never imported" in m for t, m in warns)
    assert all(f.fix_hint for f in rep.errors)


def test_lint_workspace_forbidden_calls_in_part_files_and_missing_entry(tmp_ws) -> None:
    assert not lint_workspace(tmp_ws).passed  # no model.py at all
    _write(tmp_ws, "src/model.py", MODEL)
    _write(tmp_ws, "src/parts/seat.py", SEAT + "\nbpy.ops.render.render()\n")
    _write(tmp_ws, "src/parts/leg.py", LEG)
    rep = lint_workspace(tmp_ws)
    assert any(f.target == "src/parts/seat.py" and "bpy.ops.render.render" in f.message for f in rep.errors)


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("run_bpy_under_test", WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_wrapper_maps_errors_to_the_failing_src_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))  # run_script edits sys.path; keep the test process clean
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    wrapper = _load_wrapper()
    src = tmp_path / "src"
    (src / "parts").mkdir(parents=True)
    (src / "model.py").write_text("from parts.leg import build_leg\nbuild_leg()\n")
    (src / "parts" / "leg.py").write_text("def build_leg():\n    x = {}\n    return x['missing']\n")
    err = wrapper.run_script(str(src / "model.py"))
    assert err["error_type"] == "KeyError" and err["error_file"] == "src/parts/leg.py" and err["error_line"] == 3
    assert err["error_source"] == "return x['missing']"
    # syntax error inside a part file imported by model.py → reported against the part file
    (src / "parts" / "leg.py").write_text("def build_leg(:\n    pass\n")
    for name in [m for m in sys.modules if m.startswith("parts")]:
        del sys.modules[name]
    err = wrapper.run_script(str(src / "model.py"))
    assert err["error_type"] == "SyntaxError" and err["error_file"] == "src/parts/leg.py" and err["error_line"] == 1
    # missing builder → hint names the file and the function
    (src / "parts" / "leg.py").write_text("def build_legs():\n    pass\n")
    err = wrapper.run_script(str(src / "model.py"))
    assert err["error_type"] == "ImportError" and err["error_file"] == "src/model.py" and err["error_line"] == 1
    assert "def build_leg()" in err["error_message"] and "parts/leg.py" in err["error_message"]
    # missing module → hint says to create the file
    (src / "model.py").write_text("import math\nfrom parts.armrest import build_armrest\n")
    err = wrapper.run_script(str(src / "model.py"))
    assert err["error_type"] in ("ModuleNotFoundError", "ImportError")
    assert err["error_line"] == 2 and "src/parts/armrest.py" in err["error_message"]
    assert wrapper.src_relative(str(src / "parts" / "x.py"), str(src)) == "src/parts/x.py"
    assert wrapper.src_relative(str(tmp_path / "other.py"), str(src)) is None
    for name in [m for m in sys.modules if m == "parts" or m.startswith("parts.")]:
        del sys.modules[name]
