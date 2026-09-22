"""Skeleton generation from a StaticPlan."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.languages.threejs import ThreeJsRuntime, write_skeleton
from codeverse3d.prompts.catalog import language_text
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
    assert "RoundedBoxGeometry" in seat and "ExtrudeGeometry" in seat
    assert "Material: leather" in seat
    assert '"type": "module"' in (ws.src / "package.json").read_text()


def test_skeleton_never_overwrites(tmp_path: Path):
    ws = Workspace(tmp_path / "ws")
    write_skeleton(ws, _plan())
    (ws.src / "parts" / "seat_cushion.js").write_text("// mine\n")
    again = write_skeleton(ws, _plan())
    assert again == []
    assert (ws.src / "parts" / "seat_cushion.js").read_text() == "// mine\n"


def test_runtime_protocol_surface():
    rt = ThreeJsRuntime()
    assert rt.language.value == "threejs"
    assert rt.entry_globs == ("src/object.js", "src/parts/*.js")
    doc = language_text(rt.language, "contract.md")  # the contract is prompt material (prompts/catalog)
    assert "build" in doc and "three" in doc.lower()


def test_skeleton_rejects_non_static_plan(tmp_path: Path):
    with pytest.raises(TypeError):
        write_skeleton(Workspace(tmp_path), object())  # type: ignore[arg-type]
