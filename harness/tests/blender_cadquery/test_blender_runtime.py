"""BlenderRuntime: offline tests with a fake blender + integration tests on the real binary."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from codeverse.contracts.artifacts import BuildResult
from codeverse.languages import get_runtime
from codeverse.languages.base import LanguageRuntime
from codeverse.languages.blender.lint import lint_blender_file
from codeverse.languages.blender.runtime import (
    WRAPPER,
    BlenderNotFoundError,
    BlenderRuntime,
    blender_env,
)

FAKE_BLENDER = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[sys.argv.index("--") + 1:]
out = args[args.index("--out") + 1]
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "object.glb"), "wb").write(b"glTF" + b"\\0" * 16)
json.dump({"ok": True, "error_type": "", "error_message": "", "duration_ms": 7, "warnings": []}, open(os.path.join(out, "build.json"), "w"))
json.dump({"tri_count": 12, "objects": []}, open(os.path.join(out, "census.json"), "w"))
print("fake blender ran", " ".join(args))
"""


def _fake_blender(tmp_path: Path) -> str:
    p = tmp_path / "blender"
    p.write_text(FAKE_BLENDER)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_protocol_and_registry() -> None:
    rt = get_runtime("blender")
    assert isinstance(rt, BlenderRuntime) and isinstance(rt, LanguageRuntime)
    assert rt.entry_globs == ("src/model.py", "src/parts/*.py") and rt.language.value == "blender"
    assert rt.file_for_part("Seat Cushion") == "src/parts/seat_cushion.py"  # tracks call this via getattr
    assert "bpy" in rt.contract_doc() and "Z is up" in rt.contract_doc()
    assert rt.cookbook_path().name == "cookbook.md"


def test_missing_binary_raises(tmp_ws) -> None:
    rt = BlenderRuntime(blender="/nonexistent/blender")
    (tmp_ws.src / "model.py").write_text("import bpy\n")
    with pytest.raises(BlenderNotFoundError):
        rt.build(tmp_ws)


def test_missing_entry_file(tmp_ws, tmp_path) -> None:
    rt = BlenderRuntime(blender=_fake_blender(tmp_path))
    r = rt.build(tmp_ws)
    assert not r.ok and r.error_type == "MissingEntryFile"


def test_build_command_and_env(tmp_ws, tmp_path) -> None:
    rt = BlenderRuntime(blender=_fake_blender(tmp_path))
    cmd = rt.build_command(tmp_ws, stl=True, seed=3, tri_limit=1000)
    assert cmd[1:5] == ["-b", "--factory-startup", "--python", str(WRAPPER)] and "--" in cmd
    assert cmd[cmd.index("--script") + 1] == str(tmp_ws.src / "model.py") and "--stl" in cmd and "--seed" in cmd
    os.environ["PYTHONPATH"] = "/tmp/x"
    try:
        assert "PYTHONPATH" not in blender_env() and blender_env()["PYTHONNOUSERSITE"] == "1"
    finally:
        del os.environ["PYTHONPATH"]


def test_build_with_fake_blender(tmp_ws, tmp_path) -> None:
    rt = BlenderRuntime(blender=_fake_blender(tmp_path))
    (tmp_ws.src / "model.py").write_text("import bpy\n")
    r = rt.build(tmp_ws, timeout_s=30)
    assert isinstance(r, BuildResult) and r.ok and r.glb_path == str(tmp_ws.artifacts / "object.glb")
    assert r.census["tri_count"] == 12 and r.duration_ms == 7 and "fake blender ran" in r.stdout_tail


# --------------------------------------------------------------------------- real Blender
@pytest.mark.blender
def test_live_skeleton_builds_and_exports_canonical_glb(tmp_ws, table_plan, blender_bin) -> None:
    trimesh = pytest.importorskip("trimesh")
    rt = BlenderRuntime(blender=blender_bin)
    rt.skeleton(tmp_ws, table_plan)
    assert rt.lint(tmp_ws).passed
    t0 = time.monotonic()
    r = rt.build(tmp_ws, timeout_s=120)
    dt = time.monotonic() - t0
    assert r.ok, (r.error_type, r.error_message, r.stderr_tail)
    assert dt < 60
    assert Path(r.extra_paths["stl"]).stat().st_size > 0
    scene = trimesh.load(r.glb_path)
    names = set(scene.graph.nodes_geometry)
    assert (tmp_ws.src / "parts" / "leg.py").is_file()  # multi-file skeleton
    assert names == {"TableTop", "Shelf", "Leg_0", "Leg_1", "Leg_2", "Leg_3"}
    assert "Legs" not in scene.graph.nodes  # instances are TOP-LEVEL: no parent Empty node
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-4 and abs(hi[1] - 0.6) < 1e-3  # Y-up, ground contact, height 0.6
    assert abs(lo[0] + 0.25) < 1e-3 and abs(hi[2] - 0.25) < 1e-3
    leg0 = scene.geometry[scene.graph["Leg_0"][1]]
    assert len(leg0.faces) > 12  # bevel modifier applied on export
    assert leg0.visual.material.baseColorFactor[0] > leg0.visual.material.baseColorFactor[2]  # oak-ish
    c = r.census
    assert c["tri_count"] == sum(o["tri_count"] for o in c["objects"] if o["type"] == "MESH")
    assert c["n_mesh_objects"] == 6 and c["frame"] == "z_up_neg_y_front" and not c["cameras"]
    assert abs(c["scene_bbox_min"][2]) < 1e-4 and abs(c["scene_bbox_max"][2] - 0.6) < 1e-3


BROKEN = '''import bpy
import bmesh

bpy.ops.mesh.primitive_cube_add(size=1)
body = bpy.context.object
body.name = "Body"
bpy.ops.object.light_add(type="SUN")
bm = bmesh.new()
bm.from_mesh(body.data)
v = bm.verts[0]
bm.free()
'''


@pytest.mark.blender
def test_live_error_maps_to_line_and_lint_catches_it(tmp_ws, blender_bin) -> None:
    rt = BlenderRuntime(blender=blender_bin)
    (tmp_ws.src / "model.py").write_text(BROKEN)
    lint = lint_blender_file(tmp_ws.src / "model.py")
    assert not lint.passed and any("ensure_lookup_table" in f.message and f.data.get("line") == 10 for f in lint.errors)
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "IndexError" and r.error_file == "src/model.py" and r.error_line == 10
    assert "ensure_lookup_table" in r.error_message
    assert r.census["build_report"]["error_source"] == "v = bm.verts[0]"
    assert any("light" in w for w in r.census["warnings"])
    assert r.glb_path is None or Path(r.glb_path).stat().st_size > 0  # partial export may still exist
    build_json = json.loads((tmp_ws.artifacts / "build.json").read_text())
    assert build_json["error_line"] == 10


@pytest.mark.blender
def test_live_timeout_and_memory_cap(tmp_ws, blender_bin) -> None:
    rt = BlenderRuntime(blender=blender_bin)
    (tmp_ws.src / "model.py").write_text("import bpy, time\nbpy.ops.mesh.primitive_cube_add(size=1)\nwhile True:\n    time.sleep(0.1)\n")
    t0 = time.monotonic()
    r = rt.build(tmp_ws, timeout_s=4)
    assert not r.ok and r.error_type == "BuildTimeout" and time.monotonic() - t0 < 20
    (tmp_ws.src / "model.py").write_text("import bpy\nimport numpy as np\nbpy.ops.mesh.primitive_cube_add(size=1)\nbig = np.ones((3_000_000_000,))\n")
    r = rt.build(tmp_ws, timeout_s=60)
    assert not r.ok and r.error_type == "MemoryError" and r.error_line == 4
