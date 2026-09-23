"""The census warns about material slots the polygons never use (a judge-capped flat-colour run).

The real-Blender end-to-end build is test_blender_live_gates.py's census test."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

CENSUS = Path(__file__).resolve().parents[2] / "codeverse3d" / "languages" / "wrappers" / "_census.py"


def _census_module() -> Any:
    """``_census`` is executed BY Blender (it must not import codeverse3d), so load it by path."""
    spec = importlib.util.spec_from_file_location("census_under_test", CENSUS)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _mesh(name: str, n_slots: int, used: list[int]) -> dict[str, Any]:
    """One visible-mesh census record, exactly as ``_object_record`` writes it."""
    return {"name": name, "type": "MESH", "n_material_slots": n_slots, "material_indices_used": used}


def test_material_slot_warnings() -> None:
    warn = _census_module().material_slot_warnings
    assert warn([_mesh("Body", 1, [0])]) == []  # the normal case: one material, one colour
    assert warn([_mesh("Arm", 3, [0, 1, 2])]) == []  # every slot reached by some polygon
    assert warn([_mesh("Cover", 0, [])]) == []  # no material at all is the other warning's job
    (only,) = warn([_mesh("StandArm", 11, [0])])
    assert "'StandArm' (11 slots, 1 used)" in only and "material_index" in only
    (partial,) = warn([_mesh("Head", 4, [0, 2])])
    assert "'Head' (4 slots, 2 used)" in partial
    # an index past the last slot is its own warning
    (only,) = warn([_mesh("Tube", 2, [0, 1, 5])])
    assert "'Tube' (index 5, 2 slot(s))" in only and "clamps" in only
    # a clamped mesh is reported once, not twice: the unused-slot line would be noise here
    assert "slots no polygon uses" not in only
    # many offenders collapse into one capped warning: the build shows 10 warnings, a
    # systemic no-op must not evict the other nine
    (only,) = warn([_mesh(f"Part{i}", 4, [0]) for i in range(9)])
    assert "(+3 more)" in only and "'Part5'" in only and "'Part6'" not in only
