"""Skeleton generation from a StaticPlan."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.languages.threejs import write_skeleton
from codeverse3d.workspace import Workspace


def _plan() -> StaticPlan:
    return StaticPlan(
        object_name="bar stool", summary="s", overall_bbox=BBox(center=(0, 0.35, 0), extents=(0.4, 0.7, 0.4)),
        parts=[
            PartPlan(name="Seat Cushion", role="seat", description="round seat", bbox=BBox(center=(0, 0.68, 0), extents=(0.36, 0.04, 0.36)), material="leather"),
            PartPlan(name="LegFrame", role="legs", description="four legs", bbox=BBox(center=(0, 0.33, 0), extents=(0.34, 0.66, 0.34)), attach_to="Seat Cushion"),
        ],
    )


def test_skeleton_files_and_contents(tmp_path: Path):
    ws = Workspace(tmp_path / "ws")
    written = write_skeleton(ws, _plan())
    rel = sorted(str(p.relative_to(ws.root)) for p in written)
    assert rel == ["src/object.js", "src/package.json", "src/parts/leg_frame.js", "src/parts/seat_cushion.js"]
    obj = (ws.src / "object.js").read_text()
    assert "export function build(THREE_)" in obj
    assert "import { buildSeatCushion } from './parts/seat_cushion.js';" in obj
    assert "root.name = 'BarStool';" in obj
    seat = (ws.src / "parts" / "seat_cushion.js").read_text()
    assert "export function buildSeatCushion(THREE_)" in seat
    assert "group.name = 'SeatCushion';" in seat
    # the stub is a plain box at the plan bbox plus the measured requirements — no recipe to copy
    # (D106's rule for every track, 2026-10-15): nothing in it says what the part should be made of
    assert "new THREE.BoxGeometry(size[0], size[1], size[2])" in seat
    assert "mesh.position.set(0, 0.68, 0);" in seat
    for recipe in ("RoundedBoxGeometry", "ExtrudeGeometry", "LatheGeometry", "Copyable", "roughness", "tick"):
        assert recipe not in seat, recipe
    assert "max(10 mm, 10 % of the plan extent)" in seat and "gap <= 2 mm" in seat
    assert "Material: leather" in seat
    assert "userData.tick is recorded by the census but never called" in obj
    assert '"type": "module"' in (ws.src / "package.json").read_text()
    # never overwrites
    (ws.src / "parts" / "seat_cushion.js").write_text("// mine\n")
    again = write_skeleton(ws, _plan())
    assert again == []
    assert (ws.src / "parts" / "seat_cushion.js").read_text() == "// mine\n"
    with pytest.raises(TypeError):
        write_skeleton(Workspace(tmp_path), object())  # type: ignore[arg-type]
