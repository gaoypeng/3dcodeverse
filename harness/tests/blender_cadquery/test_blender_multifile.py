"""Multi-file blender models on the real binary: the 4-part skeleton builds; a hand-written
2-part model builds and its errors map to the failing PART file + line; the prompt
examples (contract.md example A, cookbook "File layout") build and export."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from codeverse.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse.languages.blender import BlenderRuntime
from codeverse.prompts import PROMPTS_DIR
from codeverse.workspace import Workspace

_LABELLED_PY = re.compile(r"`((?:src|public)/[^`]+)`\n```py\n(.*?)```", re.DOTALL)


def labelled_py_files(rel: str) -> dict[str, str]:
    """``src/...`` labelled ```py blocks of a prompt file → {path: content} (multi-file examples)."""
    return dict(_LABELLED_PY.findall((PROMPTS_DIR / rel).read_text()))


def _write_files(ws: Workspace, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = ws.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


SEAT = '''import bpy, bmesh

SEAT_D, SEAT_T, SEAT_Z = 0.34, 0.04, 0.45

def build_seat():
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=SEAT_D / 2, radius2=SEAT_D / 2, depth=SEAT_T)
    me = bpy.data.meshes.new("Seat"); bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new("Seat", me)
    obj.location = (0, 0, SEAT_Z - SEAT_T / 2)
    bpy.context.scene.collection.objects.link(obj)
    return obj
'''
LEG = '''import bpy, bmesh, math

LEG_R, LEG_N, LEG_RING, LEG_H = 0.02, 3, 0.12, 0.414

def build_leg():
    parent = bpy.data.objects.new("Legs", None)
    bpy.context.scene.collection.objects.link(parent)
    for i in range(LEG_N):
        a = 2 * math.pi * i / LEG_N
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=LEG_R, radius2=LEG_R, depth=LEG_H)
        me = bpy.data.meshes.new(f"Leg_{i}"); bm.to_mesh(me); bm.free()
        obj = bpy.data.objects.new(f"Leg_{i}", me)
        obj.location = (LEG_RING * math.cos(a), LEG_RING * math.sin(a), LEG_H / 2)
        bpy.context.scene.collection.objects.link(obj)
        obj.parent = parent
    return parent
'''
MODEL = '''import bpy
from parts.seat import build_seat
import parts.leg

def main():
    build_seat()
    parts.leg.build_leg()

main()
'''


@pytest.fixture
def four_part_plan() -> StaticPlan:
    return StaticPlan(
        object_name="Stool", summary="A bar stool with a round seat, a ring footrest and four legs.",
        overall_bbox=BBox(center=(0, 0, 0.375), extents=(0.4, 0.4, 0.75)),
        parts=[
            PartPlan(name="Seat", role="seat", description="round padded seat", material="brown leather",
                     bbox=BBox(center=(0, 0, 0.73), extents=(0.36, 0.36, 0.04))),
            PartPlan(name="Leg", role="support", description="steel tube leg", material="chrome steel",
                     bbox=BBox(center=(0.15, 0.15, 0.355), extents=(0.025, 0.025, 0.71)), attach_to="Seat",
                     instances=4, symmetry="mirror_x"),
            PartPlan(name="FootRing", role="footrest", description="ring connecting the legs", material="chrome steel",
                     bbox=BBox(center=(0, 0, 0.25), extents=(0.33, 0.33, 0.02)), attach_to="Leg"),
            PartPlan(name="SeatPlate", role="mount", description="plate under the seat", material="black steel",
                     bbox=BBox(center=(0, 0, 0.705), extents=(0.3, 0.3, 0.01)), attach_to="Seat"),
        ],
    )


@pytest.mark.blender
def test_live_four_part_multifile_skeleton_builds(tmp_ws, four_part_plan, blender_bin) -> None:
    trimesh = pytest.importorskip("trimesh")
    rt = BlenderRuntime(blender=blender_bin)
    paths = rt.skeleton(tmp_ws, four_part_plan)
    assert [p.relative_to(tmp_ws.root).as_posix() for p in paths] == [
        "src/model.py", "src/parts/seat.py", "src/parts/leg.py", "src/parts/foot_ring.py", "src/parts/seat_plate.py"]
    assert rt.file_for_part("FootRing") == "src/parts/foot_ring.py"
    lint = rt.lint(tmp_ws)
    assert lint.passed, [(f.target, f.message) for f in lint.errors]
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message, r.error_file, r.error_line, r.stderr_tail)
    scene = trimesh.load(r.glb_path)
    assert set(scene.graph.nodes_geometry) == {"Seat", "Leg_0", "Leg_1", "Leg_2", "Leg_3", "FootRing", "SeatPlate"}
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-4 and abs(hi[1] - 0.75) < 1e-3
    assert "[selfcheck]" in r.stdout_tail


@pytest.mark.blender
def test_live_multifile_errors_map_to_part_file_and_line(tmp_ws, blender_bin) -> None:
    rt = BlenderRuntime(blender=blender_bin)
    _write_files(tmp_ws, {"src/model.py": MODEL, "src/parts/seat.py": SEAT, "src/parts/leg.py": LEG})
    assert rt.lint(tmp_ws).passed
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    assert {o["name"] for o in r.census["objects"]} == {"Seat", "Legs", "Leg_0", "Leg_1", "Leg_2"}

    leg = tmp_ws.src / "parts" / "leg.py"
    # (a) syntax error in the part file → src/parts/leg.py:8
    leg.write_text(LEG.replace("    for i in range(LEG_N):", "    for i in range(LEG_N)"))
    lint = rt.lint(tmp_ws)
    assert not lint.passed and any(f.target == "src/parts/leg.py" and "SyntaxError" in f.message for f in lint.errors)
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "SyntaxError" and r.error_file == "src/parts/leg.py" and r.error_line == 8
    bj = json.loads((tmp_ws.artifacts / "build.json").read_text())
    assert (bj["error_file"], bj["error_line"], bj["error_source"]) == ("src/parts/leg.py", 8, "for i in range(LEG_N)")

    # (b) runtime error deep in the part file → that frame, not model.py's import/call line
    leg.write_text(LEG.replace("        obj.parent = parent", "        obj.parent = parent\n        obj.data.materials.append(None).foo"))
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "AttributeError" and r.error_file == "src/parts/leg.py" and r.error_line == 17
    assert r.census["build_report"]["error_source"] == "obj.data.materials.append(None).foo"
    bj = json.loads((tmp_ws.artifacts / "build.json").read_text())
    assert bj["error_file"] == "src/parts/leg.py" and bj["error_line"] == 17 and "leg.py" in bj["traceback"]

    # (c) missing builder → lint error; build reports model.py's import line with a hint
    leg.write_text(LEG.replace("def build_leg", "def build_legs"))
    (tmp_ws.src / "model.py").write_text(MODEL.replace("import parts.leg", "from parts.leg import build_leg").replace("parts.leg.build_leg()", "build_leg()"))
    lint = rt.lint(tmp_ws)
    assert any(f.target == "src/parts/leg.py" and "does not define `def build_leg()`" in f.message for f in lint.errors)
    r = rt.build(tmp_ws, timeout_s=120)
    assert not r.ok and r.error_type == "ImportError" and r.error_file == "src/model.py" and r.error_line == 3
    assert "def build_leg()" in r.error_message


@pytest.mark.blender
def test_live_contract_multifile_example_builds(tmp_ws, blender_bin) -> None:
    trimesh = pytest.importorskip("trimesh")
    files = labelled_py_files("blender/contract.md")
    assert set(files) == {"src/parts/seat.py", "src/parts/leg.py", "src/model.py"}
    _write_files(tmp_ws, files)
    rt = BlenderRuntime(blender=blender_bin)
    lint = rt.lint(tmp_ws)
    assert lint.passed, [(f.target, f.message) for f in lint.errors]
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message, r.error_file, r.error_line)
    scene = trimesh.load(r.glb_path)
    assert set(scene.graph.nodes_geometry) == {"Seat", "Leg_0", "Leg_1", "Leg_2"}
    lo, hi = scene.bounds
    assert abs(lo[1]) < 1e-3 and abs(hi[1] - 0.45) < 1e-3  # Y-up GLB, on the ground, seat top at 0.45


@pytest.mark.blender
def test_live_cookbook_layout_example_builds(tmp_ws, blender_bin) -> None:
    files = labelled_py_files("blender/cookbook.md")
    assert set(files) == {"src/parts/mug_body.py", "src/model.py"}
    _write_files(tmp_ws, files)
    rt = BlenderRuntime(blender=blender_bin)
    assert rt.lint(tmp_ws).passed
    r = rt.build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message, r.error_file, r.error_line)
    assert {o["name"] for o in r.census["objects"] if o["type"] == "MESH"} == {"MugBody"}
    assert Path(r.glb_path).stat().st_size > 500
