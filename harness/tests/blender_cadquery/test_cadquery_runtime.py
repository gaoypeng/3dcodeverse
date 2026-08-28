"""CadQueryRuntime: skeleton + build (real cadquery when importable; fake interpreter otherwise)."""

from __future__ import annotations

import ast
import importlib.util
import json
import stat
import sys
import time
from pathlib import Path

import pytest

from codeverse.languages import get_runtime
from codeverse.languages.base import LanguageRuntime
from codeverse.languages.cadquery import (
    WRAPPER,
    CadQueryRuntime,
    cadquery_env,
    cadquery_skeleton_source,
    lint_cadquery_source,
)
from tests.blender_cadquery.conftest import has_cadquery

needs_cq = pytest.mark.skipif(not has_cadquery(), reason="cadquery not importable")


def test_protocol_and_contract() -> None:
    rt = get_runtime("cadquery")
    assert isinstance(rt, CadQueryRuntime) and isinstance(rt, LanguageRuntime)
    assert "cq.Assembly" in rt.contract_doc() and rt.entry_globs == ("src/model.py",)
    cmd = rt.build_command(__import__("codeverse.workspace", fromlist=["Workspace"]).Workspace("/tmp/x"), seed=1)
    assert cmd[0] == sys.executable and cmd[1] == str(WRAPPER) and "--seed" in cmd


def test_skeleton_source_parses_and_lints(table_plan) -> None:
    src = cadquery_skeleton_source(table_plan)
    ast.parse(src)
    assert "codeverse" not in src
    assert 'name="TableTop"' in src and 'name=f"Leg_{_i}"' in src and "result = cq.Assembly(" in src
    assert lint_cadquery_source(src).passed


def test_missing_entry(tmp_ws) -> None:
    r = CadQueryRuntime().build(tmp_ws)
    assert not r.ok and r.error_type == "MissingEntryFile"


def test_missing_entry_invalidates_previous_outputs(tmp_ws) -> None:
    """Same invariant as BlenderRuntime: the invalidation runs BEFORE the
    missing-entry early return and build.json agrees with the returned result."""
    for name in ("object.glb", "object.step", "object.stl"):
        (tmp_ws.artifacts / name).write_bytes(b"stale")
    tmp_ws.write_json(tmp_ws.artifacts / "build.json", {"ok": True})
    tmp_ws.write_json(tmp_ws.artifacts / "census.json", {"tri_count": 3})
    r = CadQueryRuntime().build(tmp_ws)
    assert not r.ok and r.error_type == "MissingEntryFile"
    for name in ("object.glb", "object.step", "object.stl", "census.json"):
        assert not (tmp_ws.artifacts / name).exists(), name
    disk = json.loads((tmp_ws.artifacts / "build.json").read_text())
    assert disk["ok"] is False and disk["error_type"] == "MissingEntryFile"


def test_fake_python_wrapper_crash(tmp_ws, tmp_path) -> None:
    fake = tmp_path / "py"
    fake.write_text("#!/bin/sh\necho 'ImportError: no cadquery' >&2\nexit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nresult = cq.Workplane().box(1, 1, 1)\n")
    r = CadQueryRuntime(python=str(fake)).build(tmp_ws, timeout_s=20)
    assert not r.ok and r.error_type == "WrapperCrash" and "no cadquery" in r.stderr_tail


@needs_cq
def test_skeleton_builds_named_coloured_glb(tmp_ws, table_plan) -> None:
    trimesh = pytest.importorskip("trimesh")
    rt = CadQueryRuntime()
    rt.skeleton(tmp_ws, table_plan)
    assert rt.lint(tmp_ws).passed
    t0 = time.monotonic()
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message, r.stderr_tail)
    assert time.monotonic() - t0 < 60
    assert set(r.extra_paths) == {"step", "stl"} and Path(r.extra_paths["step"]).stat().st_size > 0
    scene = trimesh.load(r.glb_path)
    assert set(scene.graph.nodes_geometry) == {"TableTop", "Shelf", "Leg_0", "Leg_1", "Leg_2", "Leg_3"}
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-6 and abs(hi[1] - 0.6) < 1e-6  # Y-up, on the ground
    assert abs(lo[2] + 0.25) < 1e-6 and abs(hi[0] - 0.25) < 1e-6
    top = scene.geometry[scene.graph["TableTop"][1]]
    assert top.visual.material.baseColorFactor[0] > top.visual.material.baseColorFactor[2]
    c = r.census
    assert c["result_kind"] == "assembly" and c["n_parts"] == 6 and c["tri_count"] == 6 * 12
    leg = next(p for p in c["parts"] if p["name"] == "Leg_1")
    assert leg["valid"] and abs(leg["volume_m3"] - 0.04 * 0.04 * 0.56) < 1e-9 and leg["bbox_min"][2] == 0.0


@needs_cq
def test_assembly_with_subassembly_locations_and_errors(tmp_ws) -> None:
    trimesh = pytest.importorskip("trimesh")
    src = '''import cadquery as cq
base = cq.Workplane("XY").box(0.4, 0.4, 0.02).translate((0, 0, 0.01))
peg = cq.Workplane("XY").cylinder(0.1, 0.02).translate((0, 0, 0.05))
result = cq.Assembly(name="Board")
result.add(base, name="Base", color=cq.Color(0.2, 0.2, 0.8))
pegs = cq.Assembly(name="Pegs", loc=cq.Location(cq.Vector(0, 0, 0.02)))
pegs.add(peg, name="Peg_0", loc=cq.Location(cq.Vector(0.1, -0.1, 0)), color=cq.Color(1, 0, 0))
pegs.add(peg, name="Peg_1", loc=cq.Location(cq.Vector(-0.1, 0.1, 0)))
result.add(pegs, name="Pegs", color=cq.Color(0, 1, 0))
'''
    (tmp_ws.src / "model.py").write_text(src)
    rt = CadQueryRuntime()
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    scene = trimesh.load(r.glb_path)
    assert set(scene.graph.nodes_geometry) == {"Base", "Peg_0", "Peg_1"}
    p0 = scene.geometry[scene.graph["Peg_0"][1]]
    lo, hi = p0.bounds
    # world: x=0.1, y=-0.1 → glb z=+0.1; z from 0.02 (sub-assembly loc) to 0.12
    assert abs((lo[0] + hi[0]) / 2 - 0.1) < 1e-6 and abs((lo[2] + hi[2]) / 2 - 0.1) < 1e-6
    assert abs(lo[1] - 0.02) < 1e-6 and abs(hi[1] - 0.12) < 1e-6
    assert list(p0.visual.material.baseColorFactor[:3]) == [255, 0, 0]
    p1 = scene.geometry[scene.graph["Peg_1"][1]]
    assert list(p1.visual.material.baseColorFactor[:3]) == [0, 255, 0]  # inherited from parent assembly

    # error mapping: bad fillet → OCC error mapped to line with hint
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\n\nresult = cq.Workplane('XY').box(0.1, 0.1, 0.01).edges('|Z').fillet(0.2)\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_line == 3 and "fillet" in r.error_message.lower()
    # missing result
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nx = cq.Workplane('XY').box(1, 1, 1)\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "MissingResult"
    # bare workplane → single node 'Object' + warning
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok and r.census["result_kind"] == "single"
    assert trimesh.load(r.glb_path).graph.nodes_geometry == ["Object"]
    assert any("bare Workplane" in w for w in r.census["build_report"]["warnings"])
    build_json = json.loads((tmp_ws.artifacts / "build.json").read_text())
    assert build_json["ok"] is True


def _wrapper_module():
    spec = importlib.util.spec_from_file_location("run_cq_under_test", WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_wrapper_error_file_is_workspace_relative(tmp_path) -> None:
    """error_file must be ``src/<file>`` (like every other runtime) and point at the innermost src/ frame."""
    mod = _wrapper_module()
    src = tmp_path / "src"
    src.mkdir()
    (src / "helpers.py").write_text("def make_part():\n    raise RuntimeError('boom')  # line 2\n")
    (src / "model.py").write_text("from helpers import make_part\nresult = make_part()\n")
    assert mod.src_relative(str(src / "model.py"), str(src)) == "src/model.py"
    assert mod.src_relative(str(src / "parts" / "leg.py"), str(src)) == "src/parts/leg.py"
    assert mod.src_relative(str(tmp_path / "other.py"), str(src)) is None
    assert mod.entry_relative(str(src / "model.py")) == "src/model.py"
    err, ns = mod.run_script(str(src / "model.py"))
    assert err is not None and ns == {}
    assert err["error_type"] == "RuntimeError" and err["error_file"] == "src/helpers.py" and err["error_line"] == 2
    assert "raise RuntimeError" in err["error_source"] and "helpers.py" in err["traceback"]
    # an error raised by model.py itself still maps to src/model.py (and so does a SyntaxError)
    (src / "model.py").write_text("import math\nx = 1 / 0\n")
    err, _ = mod.run_script(str(src / "model.py"))
    assert err["error_file"] == "src/model.py" and err["error_line"] == 2
    (src / "model.py").write_text("result = (\n")
    err, _ = mod.run_script(str(src / "model.py"))
    assert err["error_type"] == "SyntaxError" and err["error_file"] == "src/model.py"
    (src / "model.py").write_text("import sys\nsys.exit(0)\n")
    err, _ = mod.run_script(str(src / "model.py"))
    assert err["error_type"] == "SystemExit" and err["error_file"] == "src/model.py"


@needs_cq
def test_helper_module_error_and_missing_result_report_src_paths(tmp_ws) -> None:
    rt = CadQueryRuntime()
    (tmp_ws.src / "helpers.py").write_text("import cadquery as cq\n\n\ndef make_part():\n    wp = cq.Workplane('XY').box(1, 1, 1)\n"
                                           "    return wp.edges('|Z').fillet(5.0)  # line 6: OCC fail\n")
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nfrom helpers import make_part\n\nassy = cq.Assembly()\n"
                                         "assy.add(make_part(), name='Part')\nresult = assy\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "StdFail_NotDone"
    assert r.error_file == "src/helpers.py" and r.error_line == 6 and (tmp_ws.root / r.error_file).is_file()
    assert "helpers.py" in r.stderr_tail and "StdFail_NotDone" in r.stderr_tail  # traceback reaches the repair report
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nx = cq.Workplane('XY').box(1, 1, 1)\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "MissingResult" and r.error_file == "src/model.py"


@needs_cq
def test_trailing_selector_exports_parent_solid_with_warning(tmp_ws) -> None:
    """``body.faces('>Z')`` / ``.edges('|Z')`` on the stack must not become a 2-triangle sheet / nothing."""
    trimesh = pytest.importorskip("trimesh")
    rt = CadQueryRuntime()
    src = '''import cadquery as cq
body = cq.Workplane("XY").box(0.1, 0.1, 0.01).edges("|Z").fillet(0.002)
result = cq.Assembly()
result.add(body.faces(">Z"), name="Plate", color=cq.Color("gray"))
result.add(cq.Workplane("XY").box(0.02, 0.02, 0.02).edges("|Z"), name="Leg")
'''
    (tmp_ws.src / "model.py").write_text(src)
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    parts = {p["name"]: p for p in r.census["parts"]}
    assert parts["Plate"]["n_solids"] == 1 and abs(parts["Plate"]["volume_m3"] - 0.1 * 0.1 * 0.01) < 2e-6
    assert parts["Leg"]["n_solids"] == 1 and parts["Leg"]["tri_count"] == 12
    assert set(trimesh.load(r.glb_path).graph.nodes_geometry) == {"Plate", "Leg"}
    warns = r.census["build_report"]["warnings"]
    assert any("'Plate'" in w and "Face" in w and "trailing selector" in w for w in warns), warns
    assert any("'Leg'" in w and "Edge" in w for w in warns), warns
    # bare-Workplane result with a trailing selector: same fallback
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(0.1, 0.1, 0.01).faces('>Z')\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok and r.census["parts"][0]["n_solids"] == 1 and r.census["parts"][0]["tri_count"] == 12
    # no solid anywhere in the chain (2D wires only) → a typed error naming the stack, not a silent sheet
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nresult = cq.Workplane('XY').rect(0.1, 0.1)\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "ExportError" and "Wire" in r.error_message and "not a Solid" in r.error_message
    # a bare cq.Face added to an assembly stays a face but is flagged (n_solids == 0 surfaced)
    (tmp_ws.src / "model.py").write_text("import cadquery as cq\nf = cq.Workplane('XY').box(0.1, 0.1, 0.01).faces('>Z').val()\n"
                                         "result = cq.Assembly()\nresult.add(f, name='Sheet')\n")
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok and r.census["parts"][0]["n_solids"] == 0
    assert any("'Sheet'" in w and "contains no solid" in w for w in r.census["build_report"]["warnings"])


def test_cadquery_env_scrubs_secrets(monkeypatch) -> None:
    """src/model.py executes under this env: credential-shaped vars must not reach it."""
    monkeypatch.setenv("GEMINI_API_KEYS", "k")
    monkeypatch.setenv("FOO_API_KEY", "x")
    monkeypatch.setenv("PYTHONSTARTUP", "/tmp/s")
    env = cadquery_env()
    assert "GEMINI_API_KEYS" not in env and "FOO_API_KEY" not in env
    assert "PYTHONSTARTUP" not in env
    assert env["OMP_NUM_THREADS"] == "4" and "PATH" in env
