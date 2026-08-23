"""CadQueryRuntime: skeleton + build (real cadquery when importable; fake interpreter otherwise)."""

from __future__ import annotations

import ast
import json
import stat
import sys
import time
from pathlib import Path

import pytest

from codeverse.languages import get_runtime
from codeverse.languages.base import LanguageRuntime
from codeverse.languages.cadquery.lint import lint_cadquery_source
from codeverse.languages.cadquery.runtime import WRAPPER, CadQueryRuntime
from codeverse.languages.cadquery.skeleton import cadquery_skeleton_source
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
