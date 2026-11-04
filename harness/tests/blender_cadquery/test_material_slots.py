"""The census warns about material slots the polygons never use (a judge-capped flat-colour run)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

CENSUS = Path(__file__).resolve().parents[2] / "codeverse3d" / "languages" / "wrappers" / "_census.py"


def _mesh(name: str, n_slots: int, used: list[int]) -> dict[str, Any]:
    return {"name": name, "type": "MESH", "n_material_slots": n_slots, "material_indices_used": used}


def test_material_slot_warnings() -> None:
    spec = importlib.util.spec_from_file_location("census_under_test", CENSUS)   # executed BY Blender: load by path
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    warn = mod.material_slot_warnings
    assert warn([_mesh("Body", 1, [0]), _mesh("Arm", 3, [0, 1, 2]), _mesh("Cover", 0, [])]) == []
    (only,) = warn([_mesh("StandArm", 11, [0])])
    assert "'StandArm' (11 slots, 1 used)" in only and "material_index" in only
    (clamped,) = warn([_mesh("Tube", 2, [0, 1, 5])])   # an index past the last slot is its own warning, reported once
    assert "'Tube' (index 5, 2 slot(s))" in clamped and "slots no polygon uses" not in clamped
    (many,) = warn([_mesh(f"Part{i}", 4, [0]) for i in range(9)])   # many offenders collapse into one capped warning
    assert "(+3 more)" in many and "'Part5'" in many and "'Part6'" not in many
