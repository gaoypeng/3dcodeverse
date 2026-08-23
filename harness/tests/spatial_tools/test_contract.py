from __future__ import annotations

from pathlib import Path

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
