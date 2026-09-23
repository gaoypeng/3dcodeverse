"""Offline smoke tests: extraction, metrics arithmetic, suite registry, and (if tools exist) one executor each.

    ~/miniconda3/envs/cv3d-eval/bin/python -m pytest llm/tests -q
"""
from __future__ import annotations

import importlib
import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

PKG = "llm"
extract = importlib.import_module(f"{PKG}.extract")
metrics = importlib.import_module(f"{PKG}.metrics")
suites = importlib.import_module(f"{PKG}.suites")
config = importlib.import_module(f"{PKG}.config")
executors = importlib.import_module(f"{PKG}.executors")


def test_extract_comment_first_line_not_penalised():
    text = "```python\n# build a cube\nimport bpy\nbpy.ops.mesh.primitive_cube_add()\n```"
    code, meta = extract.extract(text, "blender")
    assert code.startswith("# build a cube") and meta["valid"] and meta["score"] >= 10


def test_extract_prefers_last_dialect_block():
    text = "```bash\npip install cadquery\n```\n```python\nimport cadquery as cq\nresult = cq.Workplane('XY').box(1,2,3)\n```"
    code, meta = extract.extract(text, "cadquery")
    assert "Workplane" in code and meta["chosen"] == 1


def test_pass_at_k():
    assert metrics.pass_at_k(4, 0, 1) == 0.0
    assert metrics.pass_at_k(4, 4, 1) == 1.0
    assert math.isclose(metrics.pass_at_k(4, 1, 1), 0.25)
    assert math.isclose(metrics.pass_at_k(4, 1, 4), 1.0)


def test_chamfer_identity():
    p = np.random.default_rng(0).random((500, 3))
    cd, fs = metrics.chamfer_f(p, p)
    assert cd == 0.0 and fs["f@0.05"] == 1.0


def test_suite_registry():
    s = suites.get_suite("3dcodebench_text")
    assert s.dialect == "blender" and "geometry" in s.metrics
    assert "heldout_glsl" in suites.expand(["long-output"])


@pytest.mark.skipif(config.find_blender() is None, reason="no blender")
def test_blender_executor_empty_vs_ok():
    with tempfile.TemporaryDirectory() as d:
        rep = executors.run("blender", "import bpy\nbpy.ops.mesh.primitive_cube_add()\n", d + "/ok")
        assert rep["status"] == "OK" and rep["mesh"]
        rep = executors.run("blender", "import bpy\n", d + "/empty")
        assert rep["status"] == "EMPTY"
        rep = executors.run("blender", "import bpy\nbpy.ops.scene.clear()\n", d + "/fail")
        assert rep["status"] == "FAIL"


def test_cadquery_executor():
    pytest.importorskip("cadquery")
    with tempfile.TemporaryDirectory() as d:
        rep = executors.run("cadquery", "import cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)\n", d)
        assert rep["status"] == "OK" and Path(rep["mesh"]).exists()


@pytest.mark.skipif(config.find_openscad() is None, reason="no openscad")
def test_openscad_executor():
    with tempfile.TemporaryDirectory() as d:
        assert executors.run("openscad", "cube(10);", d)["status"] == "OK"


@pytest.mark.skipif(config.find_glslang() is None, reason="no glslang")
def test_glsl_executor_compile_only():
    with tempfile.TemporaryDirectory() as d:
        rep = executors.glsl.run("void mainImage(out vec4 c, in vec2 f){ c = vec4(f/iResolution.xy, 0.5+0.5*sin(iTime), 1.); }", d, render=False)
        assert rep["status"] == "OK"
        rep = executors.glsl.run("void mainImage(out vec4 c, in vec2 f){ c = vec4(1.0) }", d + "/bad", render=False)
        assert rep["status"] == "FAIL"


def test_a_torn_results_line_does_not_crash_a_resume():
    """A stage killed while rewriting its jsonl leaves a torn last line; resume re-runs that row."""
    execute = importlib.import_module(f"{PKG}.execute")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "exec_results.jsonl"
        out.write_text('{"id": "a", "status": "OK"}\n\n{"id": "b", "sta')
        res = execute.execute_dir(Path(d), "blender")
        assert [r["id"] for r in res] == ["a"]
        assert out.read_text() == '{"id": "a", "status": "OK"}\n'


def test_a_torn_results_line_does_not_crash_the_render_stage():
    """render_views / score read exec_results.jsonl strictly: one torn line crashed the stage."""
    render_views = importlib.import_module(f"{PKG}.render_views")
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "exec_results.jsonl").write_text('{"id": "a", "status": "FAIL"}\n{"id": "b", "sta')
        assert render_views.render_dir(Path(d)) == {}
