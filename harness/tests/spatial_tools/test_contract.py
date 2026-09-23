from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.artifacts import GateFinding, Measurement, PartMeasure, Severity
from codeverse3d.contracts.plan import BBox, CameraPlan, PartPlan, ScenePlan, StaticPlan, ZonePlan
from codeverse3d.spatial.contract import (
    check_contract,
    match_parts,
    planned_joins,
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


def test_planned_joins_pair_a_spanning_child_with_every_touching_parent_copy() -> None:
    """An X-braced table: every parent copy the brace's box touches is a candidate (ties are not broken by order)."""
    plan = StaticPlan(
        object_name="Table", summary="s", overall_bbox=BBox(center=(0, 0, 0.375), extents=(1.0, 1.0, 0.75)),
        parts=[PartPlan(name="Top", role="top", description="d", bbox=BBox(center=(0, 0, 0.73), extents=(1.0, 1.0, 0.04))),
               PartPlan(name="Leg", role="leg", description="d", bbox=BBox(center=(0, 0, 0.355), extents=(0.05, 0.05, 0.71)), attach_to="Top", instances=4),
               PartPlan(name="Brace", role="brace", description="d", bbox=BBox(center=(0, 0, 0.2), extents=(0.9, 0.05, 0.03)), attach_to="Leg")])
    leg = lambda x, z: PartMeasure(name="", bbox_min=(x - 0.025, 0.0, z - 0.025), bbox_max=(x + 0.025, 0.71, z + 0.025))  # noqa: E731
    rows = [PartMeasure(name="Top", bbox_min=(-0.5, 0.71, -0.5), bbox_max=(0.5, 0.75, 0.5))]
    for i, (x, z) in enumerate([(0.45, 0.45), (0.45, -0.45), (-0.45, 0.5), (-0.45, -0.45)]):
        rows.append(leg(x, z).model_copy(update={"name": f"Leg_{i}"}))
    # a diagonal brace from Leg_1 (0.45, -0.45) to Leg_3 (-0.45, -0.45) with a lug rising toward Leg_0: one AABB
    # covers the whole span, so it overlaps Leg_0's box (z 0.425..0.475) and stops 3.5 cm short of Leg_2's (z ≥ 0.475)
    rows.append(PartMeasure(name="Brace", bbox_min=(-0.46, 0.185, -0.46), bbox_max=(0.46, 0.215, 0.44)))
    m = Measurement(bbox_min=(-0.5, 0, -0.5), bbox_max=(0.5, 0.75, 0.5), extents=(1, 0.75, 1), center=(0, 0.375, 0),
                    tri_count=12 * len(rows), n_meshes=len(rows), n_islands=len(rows), parts=rows)

    edges = planned_joins(plan, m)
    groups = {child: set(parents) for child, parents in edges}
    brace = groups["Brace"]  # one group: the copies the brace's box touches, measured by the gate, not guessed

    assert {"Leg_1", "Leg_3"} <= brace and "Leg_0" in brace     # the two it spans plus the grazed copy — measured, not guessed
    assert "Leg_2" not in brace                                  # clear of the AABB: never a candidate
    assert [e for e in edges if e[0].startswith("Leg")] == [(f"Leg_{i}", ("Top",)) for i in range(4)]


def test_an_exact_name_wins_over_another_part_instance_pattern() -> None:
    """An exact name wins over another part's instance pattern ('Slat1' is not an instance of Slat)."""
    plan = [PartPlan(name=n, role="r", description="d", bbox=BBox(center=(0, 0, 0), extents=(1, 1, 1)))
            for n in ("Slat", "Slat1")]
    rows = [PartMeasure(name=n, bbox_min=(0, 0, 0), bbox_max=(1, 1, 1))
            for n in ("Slat_0", "Slat1", "Slat", "Slat_1")]

    matched, extra = match_parts(plan, rows)

    assert {m.name for m in matched["Slat"]} == {"Slat", "Slat_0", "Slat_1"}
    assert [m.name for m in matched["Slat1"]] == ["Slat1"]
    assert extra == []


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
    """Sizes, deltas and centres in the hint are in the language's frame (Blender: Z-up)."""
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


# --------------------------------------------------------------------------- orientation
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


def test_a_standing_box_has_no_orientation_finding() -> None:
    # Blender plan 40×20 cm footprint, 120 cm tall → GLB (0.4, 1.2, 0.2)
    r = check_contract(_measurement_glb((0.4, 1.2, 0.2)), _one_part_plan((0.4, 0.2, 1.2), up=2), language="blender")
    assert r.passed and not _orientation(r.findings)


def test_a_box_rotated_90_about_x_is_lying_down() -> None:
    r = check_contract(_measurement_glb((0.4, 0.2, 1.2)), _one_part_plan((0.4, 0.2, 1.2), up=2), language="blender")
    o = _orientation(r.findings)
    assert len(o) == 1 and o[0].severity == Severity.ERROR and not r.passed
    assert o[0].data["best_axis"] == "y" and o[0].data["pose"] == "lying"
    assert o[0].data["planned_up_m"] == pytest.approx(1.2) and o[0].data["measured_up_m"] == pytest.approx(0.2)
    assert "lying down relative to the planned box: planned 120.0 cm tall (z), measured 20.0 cm tall" in o[0].message
    assert "rotate the whole object 90° about x so its height runs along z (blender frame: Z-up, -Y front)" in o[0].fix_hint


def test_the_same_in_the_threejs_frame() -> None:
    # Y-up plan (0.4, 1.2, 0.2); built with the height along z: GLB (0.4, 0.2, 1.2)
    r = check_contract(_measurement_glb((0.4, 0.2, 1.2)), _one_part_plan((0.4, 1.2, 0.2), up=1), language="threejs")
    o = _orientation(r.findings)
    assert len(o) == 1 and o[0].severity == Severity.ERROR and o[0].data["best_axis"] == "z"
    assert "planned 120.0 cm tall (y)" in o[0].message and "about x so its height runs along y (threejs frame" in o[0].fix_hint


def test_a_yaw_of_90_is_a_turned_warning() -> None:
    # Blender plan (0.4, 0.2, 1.2) turned about z: plan-frame (0.2, 0.4, 1.2) → GLB (0.2, 1.2, 0.4)
    r = check_contract(_measurement_glb((0.2, 1.2, 0.4)), _one_part_plan((0.4, 0.2, 1.2), up=2), language="blender")
    o = _orientation(r.findings)
    assert len(o) == 1 and o[0].severity == Severity.WARN and o[0].data["pose"] == "turned"
    assert "turned 90° about z" in o[0].message and o[0].data["best_axis"] == "x"


@pytest.mark.parametrize("glb, plan", [
    ((0.2, 0.6, 0.1), (0.4, 0.2, 1.2)),       # 2× too big all round, half as tall: a bbox ERROR only
    ((1.4, 1.2, 0.5), (1.0, 0.5, 1.5)),       # 1.25× shorter is a proportion error, not a rotation
    ((1.38, 0.458, 0.68), (0.48, 0.52, 0.81)),  # a turn about x cannot change x, which grew 2.9×
])
def test_a_sizing_error_is_a_contract_violation_not_an_orientation(glb, plan) -> None:
    r = check_contract(_measurement_glb(glb), _one_part_plan(plan, up=2), language="blender")
    assert not r.passed and not _orientation(r.findings)


def test_stood_on_end_needs_a_2x_change_in_height() -> None:
    """An under-sized tall plan built 1.46× taller is not 'stood on end'; a bench on its end is."""
    orrery = _one_part_plan((0.48, 0.32, 0.48), up=1)
    assert not _orientation(check_contract(_measurement_glb((0.374, 0.468, 0.351)), orrery, language="threejs").findings)
    bench = _one_part_plan((1.5, 0.5, 0.45), up=2)
    o = _orientation(check_contract(_measurement_glb((0.45, 1.5, 0.5)), bench, language="blender").findings)
    assert len(o) == 1 and o[0].severity == Severity.ERROR and o[0].data["pose"] == "stood" and o[0].data["best_axis"] == "x"


def test_the_brilliana_c_clamp_is_caught_with_the_real_planner_box() -> None:
    """A C-clamp lying flat against the real planner box: only the long axis must reappear."""
    plan = _one_part_plan((0.125, 0.096, 0.22), up=2)
    r = check_contract(_measurement_glb((0.137, 0.024, 0.183)), plan, language="cadquery")
    o = _orientation(r.findings)
    assert len(o) == 1 and o[0].severity == Severity.ERROR and o[0].data["best_axis"] == "y"
    assert o[0].data["measured_up_m"] == pytest.approx(0.024)
