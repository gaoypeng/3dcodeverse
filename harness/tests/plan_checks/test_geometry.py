"""Plan-time geometry checks (tracks/plan_checks.py) on synthetic articulated plans."""

import math

from codeverse.contracts.artifacts import GateFinding, Severity
from codeverse.contracts.common import Track
from codeverse.contracts.plan import ArticulatedPlan
from codeverse.spatial.joints_sweep import MAX_PAIR_FINDINGS, aggregate_findings
from codeverse.tracks.plan_checks import (
    ATTACH_GAP_M,
    COLLISION_MIN_M,
    HOUSING_FRACTION,  # noqa: I001
    PIVOT_TOL_M,
    Box,
    geometry_complaints,
    plan_geometry_complaint,
)
from codeverse.tracks.planner import plan_example


def _part(name, center, extents, attach_to=None, **kw):
    return {"name": name, "role": f"{name} of the cabinet", "description": f"{name} body",
            "bbox": {"center": list(center), "extents": list(extents)}, "attach_to": attach_to, "material": "wood", **kw}


def _weld(name, parent, child):
    return {"name": name, "type": "fixed", "parent": parent, "child": child, "axis": [0, 0, 1],
            "pivot": [0, 0, 0], "lower": 0.0, "upper": 0.0, "rest": 0.0, "motion": "rigid"}


def _cabinet_with_door(pivot=(-0.2, -0.25, 0.3), axis=(0, 0, 1), lower=-math.pi / 2, upper=0.0, extra=(), welds=()):
    parts = [
        _part("Cabinet", (0, 0, 0.3), (0.4, 0.5, 0.6)),
        # door hangs on the cabinet's front-left edge (x=-0.2, y=-0.25)
        _part("Door", (0, -0.26, 0.3), (0.4, 0.02, 0.6), attach_to="Cabinet"),
        *extra,
    ]
    return ArticulatedPlan.model_validate({
        "object_name": "SideCabinet", "summary": "a cabinet with one hinged door", "root_link": "Cabinet",
        "overall_bbox": {"center": [0, -0.05, 0.3], "extents": [0.6, 0.7, 0.6]}, "parts": parts,
        "joints": [{"name": "DoorHinge", "type": "revolute", "parent": "Cabinet", "child": "Door",
                    "axis": list(axis), "pivot": list(pivot), "lower": lower, "upper": upper,
                    "rest": 0.0, "motion": "the door swings open to the front-left"}, *welds],
    })


def test_example_and_good_door_are_clean():
    assert geometry_complaints(ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))) == []
    assert plan_geometry_complaint(_cabinet_with_door()) == ""


def test_pivot_far_from_both_links_is_a_complaint():
    plan = _cabinet_with_door(pivot=(0.5, -0.25, 0.3))  # 300 mm right of the cabinet
    items = geometry_complaints(plan)
    assert len(items) == 1 and "pivot" in items[0] and "DoorHinge" in items[0]
    assert "300 mm" in items[0]


def _plan(parts, joints, root="Base"):
    return ArticulatedPlan.model_validate({
        "object_name": "Rig", "summary": "test rig", "root_link": root,
        "overall_bbox": {"center": [0, 0, 0.5], "extents": [1.5, 1.5, 1.5]}, "parts": parts, "joints": joints})


def _joint(name, typ, parent, child, axis, pivot, lower, upper):
    return {"name": name, "type": typ, "parent": parent, "child": child, "axis": list(axis), "pivot": list(pivot),
            "lower": lower, "upper": upper, "rest": 0.0, "motion": "moves"}


def test_telescoping_leg_and_spinning_wheel_are_not_collisions():
    # a lower leg sliding 400 mm up INTO its upper leg (prismatic: the parent is never a candidate)
    plan = _plan([_part("Base", (0, 0, 1.0), (0.1, 0.1, 0.1)),
                  _part("UpperLeg", (0, 0, 0.7), (0.04, 0.04, 0.6), attach_to="Base"),
                  _part("LowerLeg", (0, 0, 0.25), (0.03, 0.03, 0.5), attach_to="UpperLeg")],
                 [_joint("UpperWeld", "fixed", "Base", "UpperLeg", (0, 0, 1), (0, 0, 0.95), 0, 0),
                  _joint("LegSlide", "prismatic", "UpperLeg", "LowerLeg", (0, 0, 1), (0, 0, 0.5), 0.0, 0.4)])
    assert geometry_complaints(plan) == []
    # a wheel spinning on an axle through its centre, flanked by a fork it would "sweep" as a box
    plan = _plan([_part("Base", (0, 0, 0.3), (0.1, 0.1, 0.1)),
                  _part("Fork", (0, 0, 0.15), (0.12, 0.02, 0.3), attach_to="Base"),
                  _part("Wheel", (0, -0.03, 0.05), (0.1, 0.04, 0.1), attach_to="Fork")],
                 [_joint("ForkWeld", "fixed", "Base", "Fork", (0, 0, 1), (0, 0, 0.25), 0, 0),
                  _joint("Axle", "continuous", "Fork", "Wheel", (0, 1, 0), (0, -0.03, 0.05), 0, 0)])
    assert geometry_complaints(plan) == []


def test_parts_interlocked_at_rest_are_left_to_the_joint_sweep():
    # two crossing scissor arms whose boxes already intersect at rest
    plan = _plan([_part("Base", (0, 0, 0.05), (0.3, 0.1, 0.1)),
                  _part("ArmA", (0, 0, 0.3), (0.3, 0.02, 0.4), attach_to="Base"),
                  _part("ArmB", (0, 0.01, 0.3), (0.3, 0.02, 0.4), attach_to="ArmA")],
                 [_joint("ArmAHinge", "revolute", "Base", "ArmA", (0, 1, 0), (-0.15, 0, 0.1), -0.8, 0.8),
                  _joint("Scissor", "revolute", "ArmA", "ArmB", (0, 1, 0), (0, 0, 0.3), -0.8, 0.8)])
    assert not any("sweeps" in i and "ArmB" in i and "ArmA" in i for i in geometry_complaints(plan))


def _with_knob(y):
    return _cabinet_with_door(extra=[_part("Knob", (0, y, 0.3), (0.03, 0.03, 0.03), attach_to="Door")],
                              welds=[_weld("KnobWeld", "Door", "Knob")])


def test_floating_child_is_a_complaint():
    items = geometry_complaints(_with_knob(-0.40))  # 115 mm in front of the door face
    assert len(items) == 1 and "Knob" in items[0] and "away from its parent Door" in items[0]
    assert geometry_complaints(_with_knob(-0.285)) == []  # touching the door face


def test_door_swinging_into_its_own_cabinet_is_a_complaint():
    """+90° about +z takes the door (which lies along +x from the hinge) to +y: into the carcass."""
    items = geometry_complaints(_cabinet_with_door(lower=0.0, upper=math.pi / 2))
    assert len(items) == 1 and "sweeps" in items[0] and "into Cabinet" in items[0] and "q=1.57 rad" in items[0]


def _with_wall(x):
    wall = _part("Wall", (x, -0.45, 0.3), (0.06, 0.4, 0.6), attach_to="Cabinet")
    return _cabinet_with_door(extra=[wall], welds=[_weld("WallWeld", "Cabinet", "Wall")])


def test_door_swinging_into_a_side_wall_is_a_complaint():
    # a wall slab standing outside the cabinet's left face, where the open door (−90°) ends up
    items = geometry_complaints(_with_wall(-0.23))
    assert len(items) == 1 and "sweeps" in items[0] and "into Wall" in items[0], items
    # the same wall on the right face (door never reaches it) is fine
    assert geometry_complaints(_with_wall(0.23)) == []


def test_drawer_inside_cabinet_is_not_a_collision():
    """A housing that holds the moving part at rest is never a collision candidate — even
    when the drawer front stands 20 mm proud of the carcass."""
    plan = ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))
    assert geometry_complaints(plan) == []
    d = plan_example(Track.ARTICULATED_OBJECT)
    drawer = next(p for p in d["parts"] if p["name"] == "Drawer")
    drawer["bbox"]["center"][1] -= 0.02  # front proud of the cabinet
    plan = ArticulatedPlan.model_validate(d)
    cab, drw = (Box.from_bbox(next(p for p in plan.parts if p.name == n).bbox) for n in ("Cabinet", "Drawer"))
    assert HOUSING_FRACTION <= drw.fraction_inside(cab) < 1.0
    assert geometry_complaints(plan) == []


def test_complaint_text_is_a_reask_with_numbers():
    text = plan_geometry_complaint(_cabinet_with_door(pivot=(0.5, -0.25, 0.3)))
    assert text.startswith("Your plan is valid but its geometry contradicts itself")
    assert "same schema" in text and "- joint DoorHinge" in text


def test_box_arithmetic():
    a = Box((0, 0, 0), (1, 1, 1))
    b = Box((0.9, 0.9, 0.9), (2, 2, 2))
    assert a.gap_to(b) == 0.0 and abs(a.overlap_depth(b) - 0.1) < 1e-9
    c = Box((1.1, 0, 0), (2, 1, 1))
    assert abs(c.gap_to(a) - 0.1) < 1e-9 and a.overlap_depth(c) < 0
    assert abs(a.dist_to_point((2, 0.5, 0.5)) - 1.0) < 1e-9
    assert ATTACH_GAP_M < COLLISION_MIN_M < PIVOT_TOL_M


# ------------------------------------------------------------------ sweep aggregation
def _pen(a, b, depth, pose, sev=Severity.ERROR):
    return GateFinding(gate="articulation", severity=sev, target=f"{a}|{b}", message=f"{a}/{b} {depth}",
                       fix_hint="shrink", data={"kind": "penetration", "pose": pose, "depth_m": depth})


def test_aggregate_merges_poses_per_pair_and_ranks_by_depth():
    fs = [_pen("door", "wall", 0.004, {"hinge": 0.5}), _pen("door", "wall", 0.031, {"hinge": 1.5}),
          _pen("door", "wall", 0.002, {}), _pen("lid", "box", 0.010, {"lid_j": 1.0}),
          GateFinding(gate="articulation", severity=Severity.ERROR, target="leg", message="floating",
                      data={"kind": "unattached", "pose": {}, "gap_m": 0.05})]
    out = aggregate_findings(fs)
    assert [f.target for f in out] == ["leg", "door|wall", "lid|box"]  # gap 0.05 > 0.031 > 0.010
    dw = out[1]
    assert dw.data["n_poses"] == 3 and dw.data["max_depth_m"] == 0.031
    assert "3 of the sampled poses" in dw.message and "31.0 mm" in dw.message and "hinge=1.50" in dw.message
    assert "1 at rest" in dw.message
    assert all(f.severity == Severity.ERROR for f in out)


def test_aggregate_caps_errors_and_summarises_the_rest():
    fs = [_pen(f"p{i}", "base", 0.001 * (i + 1), {"j": 1.0}) for i in range(MAX_PAIR_FINDINGS + 3)]
    out = aggregate_findings(fs)
    errors = [f for f in out if f.severity == Severity.ERROR]
    warns = [f for f in out if f.severity == Severity.WARN]
    assert len(errors) == MAX_PAIR_FINDINGS and errors[0].target == f"p{MAX_PAIR_FINDINGS + 2}|base"
    assert len(warns) == 1 and warns[0].data["kind"] == "penetration_summary" and "3 more" in warns[0].message


def test_aggregate_keeps_single_and_warn_findings():
    fs = [_pen("a", "b", 0.001, {}, sev=Severity.WARN)]
    assert aggregate_findings(fs) == fs
    assert aggregate_findings([]) == []
