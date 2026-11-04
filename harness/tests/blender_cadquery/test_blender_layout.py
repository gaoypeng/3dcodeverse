"""Offline tests for the multi-file layout: part-file mapping, workspace lint rules, and the
wrapper's error→file:line mapping (the wrapper is importable from host python; no bpy needed)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from codeverse3d.languages.blender import (
    WRAPPER,
    lint_workspace,
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

    def run(script):
        exc, _ = wrapper.run_script(script)
        return wrapper.script_error(exc, script)

    err = run(str(src / "model.py"))
    assert err["error_type"] == "KeyError" and err["error_file"] == "src/parts/leg.py" and err["error_line"] == 3
    assert err["error_source"] == "return x['missing']"
    # syntax error inside a part file imported by model.py → reported against the part file
    (src / "parts" / "leg.py").write_text("def build_leg(:\n    pass\n")
    for name in [m for m in sys.modules if m.startswith("parts")]:
        del sys.modules[name]
    err = run(str(src / "model.py"))
    assert err["error_type"] == "SyntaxError" and err["error_file"] == "src/parts/leg.py" and err["error_line"] == 1
    (src / "model.py").write_text("import sys\nsys.exit(0)\n")  # not a failure: the scene is what was built
    assert wrapper.run_script(str(src / "model.py")) == (None, None)
    for name in [m for m in sys.modules if m == "parts" or m.startswith("parts.")]:
        del sys.modules[name]
