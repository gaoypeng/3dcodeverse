"""The census sees material slots the polygons never use.

The h2h microscope run authored 31 materials, assigned them through a helper that read
``res['faces']`` from ``bmesh.ops.create_cube`` (which returns only ``{'verts'}``), and
shipped an 11-slot arm in one off-white: every material was present, every polygon was on
slot 0, and nothing in the build said so — the judge capped the run for ``untextured_flat``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from codeverse.languages.blender import BlenderRuntime

CENSUS = Path(__file__).resolve().parents[2] / "codeverse" / "languages" / "blender" / "wrappers" / "_census.py"


def _census_module() -> Any:
    """``_census`` is executed BY Blender (it must not import codeverse), so load it by path."""
    spec = importlib.util.spec_from_file_location("census_under_test", CENSUS)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _mesh(name: str, n_slots: int, used: list[int]) -> dict[str, Any]:
    """One visible-mesh census record, exactly as ``_object_record`` writes it."""
    return {"name": name, "type": "MESH", "n_material_slots": n_slots, "material_indices_used": used}


def test_unused_slots_warn_and_a_fully_painted_mesh_does_not() -> None:
    warn = _census_module().material_slot_warnings
    assert warn([_mesh("Body", 1, [0])]) == []  # the normal case: one material, one colour
    assert warn([_mesh("Arm", 3, [0, 1, 2])]) == []  # every slot reached by some polygon
    assert warn([_mesh("Cover", 0, [])]) == []  # no material at all is the other warning's job
    (only,) = warn([_mesh("StandArm", 11, [0])])
    assert "'StandArm' (11 slots, 1 used)" in only and "material_index" in only
    (partial,) = warn([_mesh("Head", 4, [0, 2])])
    assert "'Head' (4 slots, 2 used)" in partial


def test_index_past_the_last_slot_is_its_own_warning() -> None:
    warn = _census_module().material_slot_warnings
    (only,) = warn([_mesh("Tube", 2, [0, 1, 5])])
    assert "'Tube' (index 5, 2 slot(s))" in only and "clamps" in only
    # a clamped mesh is reported once, not twice: the unused-slot line would be noise here
    assert "slots no polygon uses" not in only


def test_many_offenders_collapse_into_one_capped_warning() -> None:
    """The build shows 10 warnings; a systemic no-op must not evict the other nine."""
    warn = _census_module().material_slot_warnings
    (only,) = warn([_mesh(f"Part{i}", 4, [0]) for i in range(9)])
    assert "(+3 more)" in only and "'Part5'" in only and "'Part6'" not in only


UNUSED_SLOT_MODEL = '''import bmesh
import bpy


def part(name, n_slots, location):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.2)
    bm.to_mesh(me)
    bm.free()
    for i in range(n_slots):
        me.materials.append(bpy.data.materials.new(f"{name}Mat{i}"))
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    bpy.context.collection.objects.link(obj)
    return obj


part("FlatArm", 3, (0, 0, 0.1))                       # 3 materials, every polygon on slot 0
painted = part("PaintedArm", 3, (0.5, 0, 0.1))
for i, poly in enumerate(painted.data.polygons):
    poly.material_index = i % 3
overrun = part("OverrunArm", 1, (1.0, 0, 0.1))
for poly in overrun.data.polygons:
    poly.material_index = 2                           # past the last slot: Blender clamps
'''


@pytest.mark.blender
def test_real_build_reports_the_slots_its_polygons_never_use(tmp_ws, blender_bin) -> None:
    """End to end: the indices come off the EVALUATED mesh inside Blender, not a fixture."""
    (tmp_ws.src / "model.py").write_text(UNUSED_SLOT_MODEL)
    r = BlenderRuntime(blender=blender_bin).build(tmp_ws, timeout_s=120)
    assert r.ok, (r.error_type, r.error_message)
    rec = {o["name"]: o for o in r.census["objects"]}
    assert rec["FlatArm"]["material_indices_used"] == [0]
    assert rec["PaintedArm"]["material_indices_used"] == [0, 1, 2]
    warns = r.census["warnings"]
    (unused,) = [w for w in warns if "slots no polygon uses" in w]
    assert "'FlatArm' (3 slots, 1 used)" in unused and "PaintedArm" not in unused
    (clamped,) = [w for w in warns if "past the last slot" in w]
    assert "'OverrunArm'" in clamped
