from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.artifacts import GateFinding, Measurement, PartMeasure, Severity
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.spatial.contract import (
    check_contract,
)
from codeverse3d.spatial.measure import measure_glb


def _stool_plan(**overrides) -> StaticPlan:
    """Blender-frame (Z-up) plan matching the synthetic stool."""
    parts = [
        PartPlan(name="Seat", role="seat", description="d", bbox=BBox(center=(0, 0, 0.43), extents=(0.4, 0.4, 0.04))),
        PartPlan(name="Leg", role="leg", description="d", bbox=BBox(center=(0, 0, 0.205), extents=(0.04, 0.04, 0.41)), instances=4),
    ]
    data = dict(object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0, 0.225), extents=(0.4, 0.4, 0.45)), parts=parts)
    data.update(overrides)
    return StaticPlan(**data)


def test_bbox_deviation_warn_then_error(solid_stool_glb: Path) -> None:
    m = measure_glb(solid_stool_glb)
    plan = _stool_plan()
    plan.parts[0].bbox = BBox(center=(0, 0, 0.43), extents=(0.46, 0.4, 0.04))  # 6 cm wider than built (tol 4.6 cm)
    r = check_contract(m, plan, language="blender")
    seat = [f for f in r.findings if f.target == "Seat"]
    assert seat and seat[0].severity == Severity.WARN and "x-6.0cm" in seat[0].fix_hint
    plan.parts[0].bbox = BBox(center=(0, 0, 0.43), extents=(1.2, 0.4, 0.04))
    r = check_contract(m, plan, language="blender")
    seat = [f for f in r.findings if f.target == "Seat"]
    assert seat and seat[0].severity == Severity.ERROR


def _one_part_plan(extents_plan: tuple[float, float, float], *, up: int) -> StaticPlan:
    """A plan whose single part IS the overall box, standing on the ground of a frame with
    up axis ``up`` (2 for Z-up languages, 1 for Y-up)."""
    c = [0.0, 0.0, 0.0]
    c[up] = extents_plan[up] / 2
    box = BBox(center=tuple(c), extents=extents_plan)
    return StaticPlan(object_name="Thing", summary="s", overall_bbox=box,
                      parts=[PartPlan(name="Body", role="body", description="d", bbox=box)])


def _measurement_glb(extents_glb: tuple[float, float, float]) -> Measurement:
    """A GLB-frame (Y-up) measurement of one part ``Body`` standing on the ground."""
    ex, ey, ez = extents_glb
    lo, hi = (-ex / 2, 0.0, -ez / 2), (ex / 2, ey, ez / 2)
    return Measurement(bbox_min=lo, bbox_max=hi, extents=extents_glb, center=(0.0, ey / 2, 0.0),
                       tri_count=12, n_meshes=1, n_islands=1, parts=[PartMeasure(name="Body", bbox_min=lo, bbox_max=hi)])


def _orientation(findings: list[GateFinding]) -> list[GateFinding]:
    return [f for f in findings if f.data.get("kind") == "orientation"]


def test_a_box_rotated_90_about_x_is_lying_down() -> None:
    # Blender plan 40×20 cm footprint, 120 cm tall → GLB (0.4, 1.2, 0.2): standing, no orientation finding
    r = check_contract(_measurement_glb((0.4, 1.2, 0.2)), _one_part_plan((0.4, 0.2, 1.2), up=2), language="blender")
    assert r.passed and not _orientation(r.findings)
    r = check_contract(_measurement_glb((0.4, 0.2, 1.2)), _one_part_plan((0.4, 0.2, 1.2), up=2), language="blender")
    o = _orientation(r.findings)
    assert len(o) == 1 and o[0].severity == Severity.ERROR and not r.passed
    assert o[0].data["best_axis"] == "y" and o[0].data["pose"] == "lying"
    assert "lying down relative to the planned box: planned 120.0 cm tall (z), measured 20.0 cm tall" in o[0].message
    assert "rotate the whole object 90° about x so its height runs along z (blender frame: Z-up, -Y front)" in o[0].fix_hint
    # the same in the threejs frame
    r = check_contract(_measurement_glb((0.4, 0.2, 1.2)), _one_part_plan((0.4, 1.2, 0.2), up=1), language="threejs")
    assert [f.data["best_axis"] for f in _orientation(r.findings)] == ["z"]


@pytest.mark.parametrize("glb, plan", [
    ((0.2, 0.6, 0.1), (0.4, 0.2, 1.2)),       # 2× too big all round, half as tall: a bbox ERROR only
    ((1.4, 1.2, 0.5), (1.0, 0.5, 1.5)),       # 1.25× shorter is a proportion error, not a rotation
    ((1.38, 0.458, 0.68), (0.48, 0.52, 0.81)),  # a turn about x cannot change x, which grew 2.9×
])
def test_a_sizing_error_is_a_contract_violation_not_an_orientation(glb, plan) -> None:
    r = check_contract(_measurement_glb(glb), _one_part_plan(plan, up=2), language="blender")
    assert not r.passed and not _orientation(r.findings)
