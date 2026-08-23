from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.contracts.artifacts import Measurement, PartMeasure, Severity
from codeverse.contracts.plan import BBox, CameraPlan, PartPlan, ScenePlan, StaticPlan, ZonePlan
from codeverse.spatial.contract import check_contract, match_parts, plan_bbox_to_glb
from codeverse.spatial.measure import measure_glb


def _stool_plan(**overrides) -> StaticPlan:
    """Blender-frame (Z-up) plan matching the synthetic stool."""
    parts = [
        PartPlan(name="Seat", role="seat", description="d", bbox=BBox(center=(0, 0, 0.43), extents=(0.4, 0.4, 0.04))),
        PartPlan(name="Leg", role="leg", description="d", bbox=BBox(center=(0, 0, 0.205), extents=(0.04, 0.04, 0.41)), instances=4),
    ]
    data = dict(object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0, 0.225), extents=(0.4, 0.4, 0.45)), parts=parts)
    data.update(overrides)
    return StaticPlan(**data)


def test_frame_conversion() -> None:
    b = plan_bbox_to_glb(BBox(center=(1, 2, 3), extents=(4, 5, 6)), "blender")
    assert b.center == (1, 3, -2) and b.extents == (4, 6, 5)
    same = plan_bbox_to_glb(BBox(center=(1, 2, 3), extents=(4, 5, 6)), "threejs")
    assert same.center == (1, 2, 3)


def test_stool_matches_plan(solid_stool_glb: Path) -> None:
    r = check_contract(measure_glb(solid_stool_glb), _stool_plan(), language="blender")
    assert r.passed, [f.message for f in r.findings]
    assert not [f for f in r.findings if f.severity == Severity.WARN]


def test_missing_part_is_error(solid_stool_glb: Path) -> None:
    plan = _stool_plan()
    plan.parts.append(PartPlan(name="Backrest", role="back", description="d", bbox=BBox(center=(0, 0.18, 0.6), extents=(0.4, 0.03, 0.3))))
    r = check_contract(measure_glb(solid_stool_glb), plan, language="blender")
    assert not r.passed
    miss = [f for f in r.errors if f.target == "Backrest"]
    assert miss and "missing" in miss[0].message and "40.0×3.0×30.0" in miss[0].fix_hint


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


def test_instance_count_and_extras(solid_stool_glb: Path) -> None:
    m = measure_glb(solid_stool_glb)
    plan = _stool_plan()
    plan.parts[1].instances = 3
    r = check_contract(m, plan, language="blender")
    inst = [f for f in r.findings if f.target == "Leg" and "instance" in f.message]
    assert inst and inst[0].severity == Severity.WARN
    # extra part → INFO
    plan2 = _stool_plan(parts=[_stool_plan().parts[0]])
    r2 = check_contract(m, plan2, language="blender")
    assert [f for f in r2.findings if f.severity == Severity.INFO and "not in the plan" in f.message]


def test_ground_and_footprint_warnings() -> None:
    m = Measurement(bbox_min=(0.1, 0.02, 0.1), bbox_max=(0.5, 0.47, 0.5), extents=(0.4, 0.45, 0.4), center=(0.3, 0.245, 0.3),
                    tri_count=10, n_meshes=1, n_islands=1, ground_gap_m=0.02, footprint_offset_m=0.42,
                    parts=[PartMeasure(name="Seat", bbox_min=(0.1, 0.41, 0.1), bbox_max=(0.5, 0.45, 0.5)),
                           *[PartMeasure(name=f"Leg_{i}", bbox_min=(0.1, 0.02, 0.1), bbox_max=(0.14, 0.43, 0.14)) for i in range(4)]])
    r = check_contract(m, _stool_plan(), language="blender")
    msgs = " | ".join(f.message for f in r.findings)
    assert "above the ground" in msgs and "footprint centre" in msgs


def test_match_parts_instances() -> None:
    rows = [PartMeasure(name=n, bbox_min=(0, 0, 0), bbox_max=(1, 1, 1)) for n in ("Leg_0", "Leg_1", "leg2", "Seat", "Extra")]
    matched, extra = match_parts(_stool_plan().parts, rows)
    assert {m.name for m in matched["Leg"]} == {"Leg_0", "Leg_1", "leg2"}
    assert [e.name for e in extra] == ["Extra"]


def test_scene_plan_bounds() -> None:
    plan = ScenePlan(title="t", summary="s", setting="x", bounds=BBox(center=(0, 0, 0), extents=(10, 10, 10)), environment="e",
                     zones=[ZonePlan(name="Harbour", description="d", bbox=BBox(center=(0, 0, 0), extents=(5, 5, 5)))],
                     cameras=[CameraPlan(name="main", position=(0, 2, 10), look_at=(0, 0, 0))])
    m = Measurement(bbox_min=(-3, 0, -3), bbox_max=(12, 3, 3), extents=(15, 3, 6), center=(4.5, 1.5, 0), tri_count=1, n_meshes=1, n_islands=1,
                    parts=[PartMeasure(name="Harbour", bbox_min=(-3, 0, -3), bbox_max=(3, 3, 3))])
    r = check_contract(m, plan, language="scene_threejs")
    assert r.passed  # scene findings are warnings
    assert any("exceeds the planned bounds" in f.message for f in r.findings)
    assert not any("zone group" in f.message for f in r.findings)


def test_hints_are_written_in_the_authoring_frame() -> None:
    """Blender plan (Z-up): Leg 5×5 cm footprint, 40 cm tall.  The build made the leg 25 cm
    DEEP (Blender y) — sizes, deltas and centres in the hint must be Z-up so the agent
    changes the right dimension (in the GLB frame the same error reads as 'z+20cm')."""
    plan = StaticPlan(object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0, 0.225), extents=(0.5, 0.5, 0.45)),
                      parts=[PartPlan(name="Seat", role="seat", description="d", bbox=BBox(center=(0, 0, 0.425), extents=(0.5, 0.5, 0.05))),
                             PartPlan(name="Leg", role="leg", description="d", bbox=BBox(center=(0.2, -0.2, 0.2), extents=(0.05, 0.05, 0.40)))])
    m = Measurement(bbox_min=(-0.25, 0, -0.25), bbox_max=(0.25, 0.45, 0.25), extents=(0.5, 0.45, 0.5), center=(0.1, 0.225, -0.05),
                    tri_count=24, n_meshes=2, n_islands=2, footprint_offset_m=0.3,
                    parts=[PartMeasure(name="Seat", bbox_min=(-0.25, 0.40, -0.25), bbox_max=(0.25, 0.45, 0.25)),
                           PartMeasure(name="Leg", bbox_min=(0.175, 0.0, 0.075), bbox_max=(0.225, 0.40, 0.325))])
    r = check_contract(m, plan, language="blender")
    leg = [f for f in r.findings if f.target == "Leg"][0]
    assert "size 5.0×25.0×40.0 vs planned 5.0×5.0×40.0 cm (Δ x+0.0cm, y+20.0cm, z+0.0cm)" in leg.fix_hint
    assert "centre off by (x+0.0cm, y+0.0cm, z+0.0cm)" not in leg.fix_hint
    assert "planned centre (0.200, -0.200, 0.200) m (blender frame: Z-up, -Y front)" in leg.fix_hint
    assert leg.data["delta_extents_m"] == pytest.approx([0.0, 0.2, 0.0], abs=1e-9) and leg.data["frame"] == "z_up_neg_y_front"
    foot = [f for f in r.findings if "footprint" in f.message][0]
    # GLB offset (+0.1, ·, -0.05) → move by (-0.1, 0, +0.05) GLB = (-0.1, -0.05, 0) in Blender's Z-up frame
    assert "translate everything by (-0.100, -0.050, +0.000) m (blender frame: Z-up, -Y front)" in foot.fix_hint
    # Y-up languages keep the GLB numbers verbatim
    plan_tjs = plan.model_copy(deep=True)
    plan_tjs.parts[1].bbox = BBox(center=(0.2, 0.2, 0.2), extents=(0.05, 0.40, 0.05))
    leg = [f for f in check_contract(m, plan_tjs, language="threejs").findings if f.target == "Leg"][0]
    assert "size 5.0×40.0×25.0 vs planned 5.0×40.0×5.0 cm (Δ x+0.0cm, y+0.0cm, z+20.0cm)" in leg.fix_hint
    assert "(threejs frame: Y-up, +Z front)" in leg.fix_hint
