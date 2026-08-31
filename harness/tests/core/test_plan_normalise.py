"""Articulated-plan normalization and safe repair."""

from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from codeverse.contracts.common import Track
from codeverse.contracts.plan import ArticulatedPlan
from codeverse.tracks.planner import plan_example


def _raw() -> dict:
    d = copy.deepcopy(plan_example(Track.ARTICULATED_OBJECT))
    assert ArticulatedPlan.model_validate(d).normalisations == []
    return d


def test_a_valid_plan_is_untouched():
    d = _raw()
    p = ArticulatedPlan.model_validate(copy.deepcopy(d))
    assert p.normalisations == [] and [x.name for x in p.parts] == [x["name"] for x in d["parts"]]


def test_a_joint_that_moves_a_sub_part_promotes_it_to_a_link():
    d = _raw()
    parent = d["parts"][0]
    sash = {"name": "UpperSash", "role": "sliding sash", "description": "a 0.60 x 0.55 m frame with a pane",
            "bbox": {"center": list(parent["bbox"]["center"]), "extents": [e * 0.5 for e in parent["bbox"]["extents"]]},
            "material": "painted pine"}
    parent.setdefault("children", []).append(sash)
    d["joints"].append({"name": "UpperSashSlide", "type": "prismatic", "parent": parent["name"], "child": "UpperSash",
                        "axis": [0, 0, 1], "pivot": [0, 0, 0.5], "lower": 0.0, "upper": 0.3, "rest": 0.0,
                        "motion": "upper sash slides up"})
    p = ArticulatedPlan.model_validate(d)
    names = [x.name for x in p.parts]
    assert "UpperSash" in names
    promoted = next(x for x in p.parts if x.name == "UpperSash")
    assert promoted.attach_to == parent["name"] and promoted.material == "painted pine"
    assert all(c.name != "UpperSash" for x in p.parts for c in x.children)
    assert any("promoted sub-part" in n and "UpperSash" in n for n in p.normalisations)


def _revolute(d: dict, lower: float, upper: float, rest: float = 0.0) -> dict:
    j = d["joints"][0]
    j.update(type="revolute", lower=lower, upper=upper, rest=rest)
    return j


def _joint(p: ArticulatedPlan, j: dict):
    return next(x for x in p.joints if x.name == j["name"])


def test_a_revolute_joint_over_two_pi_in_radians_becomes_continuous():
    for lower, upper in ((0.0, 4 * math.pi), (-4 * math.pi, 4 * math.pi), (0.0, 6.5), (0.0, 10.0)):
        d = _raw()
        j = _revolute(d, lower, upper)
        p = ArticulatedPlan.model_validate(d)
        fixed = _joint(p, j)
        assert fixed.type == "continuous" and fixed.lower == fixed.upper == 0.0
        assert any("> 2π → continuous" in n for n in p.normalisations)
        assert not any("degrees" in n for n in p.normalisations)


def test_a_door_planned_in_degrees_stays_a_quarter_turn_hinge():
    d = _raw()
    j = _revolute(d, 0.0, 90.0)
    p = ArticulatedPlan.model_validate(d)
    fixed = _joint(p, j)
    assert fixed.type == "revolute"
    assert fixed.lower == 0.0 and fixed.upper == pytest.approx(1.5708, abs=1e-4) and fixed.rest == 0.0
    assert any("looked like degrees (0..90) → radians" in n for n in p.normalisations)
    assert not any("continuous" in n for n in p.normalisations)


def test_degree_shaped_limits_and_rest_are_converted_together():
    cases = ((-180.0, 0.0, 0.0), (0.0, 90.0, 90.0), (-45.0, 45.0, 0.0), (0.0, 30.0, 0.0))
    for lower, upper, rest in cases:
        d = _raw()
        j = _revolute(d, lower, upper, rest)
        p = ArticulatedPlan.model_validate(d)
        fixed = _joint(p, j)
        assert fixed.type == "revolute"
        assert fixed.lower == pytest.approx(math.radians(lower))
        assert fixed.upper == pytest.approx(math.radians(upper))
        assert fixed.rest == pytest.approx(math.radians(rest))
        assert sum("looked like degrees" in n for n in p.normalisations) == 1


def test_a_two_turn_degree_range_falls_through_to_continuous():
    d = _raw()
    j = _revolute(d, -360.0, 360.0)
    p = ArticulatedPlan.model_validate(d)
    fixed = _joint(p, j)
    assert fixed.type == "continuous"
    assert any("looked like degrees" in n for n in p.normalisations) and any("> 2π → continuous" in n for n in p.normalisations)


def test_a_sub_part_outside_its_parent_grows_the_parent_bbox():
    d = _raw()
    parent = d["parts"][0]
    c, e = parent["bbox"]["center"], parent["bbox"]["extents"]
    sill = {"name": "ThresholdSill", "role": "sill", "description": "a 40 mm deep sill proud of the frame",
            # 0.20 m proud of the frame: far outside any slack (max(SUBPART_SLACK_M, 10 % of the extent))
            "bbox": {"center": [c[0], c[1] - e[1] / 2 - 0.20, c[2] - e[2] / 2 + 0.02], "extents": [e[0], 0.06, 0.04]}}
    parent.setdefault("children", []).append(sill)
    p = ArticulatedPlan.model_validate(d)
    grown = next(x for x in p.parts if x.name == parent["name"])
    assert grown.bbox.min[1] <= sill["bbox"]["center"][1] - 0.03 + 1e-9  # the parent now encloses the sill
    assert grown.bbox.max[1] == pytest.approx(c[1] + e[1] / 2)  # and did not grow where it did not need to
    assert any("bbox grown on y" in n for n in p.normalisations)


def test_unfixable_plans_still_fail():
    d = _raw()
    d["joints"].append({"name": "Ghost", "type": "fixed", "parent": d["parts"][0]["name"], "child": "NoSuchLink",
                        "axis": [0, 0, 1], "pivot": [0, 0, 0]})
    with pytest.raises(ValidationError, match="unknown link"):
        ArticulatedPlan.model_validate(d)


def test_planner_text_in_normalisations_is_dropped():
    """Planner prose cannot masquerade as harness repair history."""
    from codeverse.contracts.plan import ArticulatedPlan

    raw = ArticulatedPlan._normalise_raw({"parts": [{"name": "A", "bbox": {"center": [0, 0, 0], "extents": [1, 1, 1]}}],
                                          "joints": [{"name": "j", "parent": "A", "child": "A", "type": "revolute", "lower": 0, "upper": 1}],
                                          "normalisations": ["rest pose set to 0.15 m (planner prose)"]})
    assert raw["normalisations"] == []


def test_a_joint_naming_a_part_by_a_unique_fragment_is_resolved():
    """A unique fragment is a safe repair for a mismatched joint reference."""
    d = _raw()
    d["parts"][1]["name"] = "ChestDrawer"        # the plan's own name ...
    d["joints"][0]["child"] = "Drawer"           # ... referenced by one word of it
    part = "ChestDrawer"
    plan = ArticulatedPlan.model_validate(d)
    assert _joint(plan, d["joints"][0]).child == part
    assert any("resolved to the one part it names" in n for n in plan.normalisations)


def test_an_ambiguous_joint_reference_is_rejected_and_names_the_real_parts():
    """An ambiguous fragment is rejected and names the available parts."""
    d = _raw()
    d["parts"][1]["name"] = "ChestDrawer"
    twin = dict(d["parts"][1])
    twin["name"] = "SpareDrawer"
    d["parts"].append(twin)
    fragment = "Drawer"
    d["joints"][0]["child"] = fragment
    with pytest.raises(ValidationError) as e:
        ArticulatedPlan.model_validate(d)
    msg = str(e.value)
    assert f"unknown link(s) {fragment}" in msg
    assert "chest_drawer" in msg and "spare_drawer" in msg, "the planner must be told the real names"


def test_swapped_limits_are_written_as_the_swap_they_are():
    d = _raw()
    j = d["joints"][0]
    j["lower"], j["upper"] = 1.2, 0.0
    j["rest"] = 0.5
    p = ArticulatedPlan.model_validate(d)
    fixed = next(x for x in p.joints if x.name == j["name"])
    assert (fixed.lower, fixed.upper) == (0.0, 1.2)
    assert any("swapped" in n for n in p.normalisations)


def test_a_rest_outside_the_limits_is_clamped_not_fatal():
    d = _raw()
    j = d["joints"][0]
    j["lower"], j["upper"], j["rest"] = 0.0, 1.0, 2.5
    p = ArticulatedPlan.model_validate(d)
    fixed = next(x for x in p.joints if x.name == j["name"])
    assert fixed.rest == pytest.approx(1.0)
    assert any("clamped" in n for n in p.normalisations)


def test_a_zero_axis_still_fails_validation():
    d = _raw()
    d["joints"][0]["axis"] = [0, 0, 0]
    with pytest.raises(ValidationError):
        ArticulatedPlan.model_validate(d)


def _rename_part(d: dict, old: str, new: str) -> None:
    for part in d["parts"]:
        if part["name"] == old:
            part["name"] = new
        if part.get("attach_to") == old:
            part["attach_to"] = new
    for j in d["joints"]:
        for side in ("parent", "child"):
            if j.get(side) == old:
                j[side] = new
    if d.get("root_link") == old:
        d["root_link"] = new


def test_a_root_link_naming_no_part_is_resolved_by_the_1b_word_rule():
    """af_excavator (2026-08-31, 3.7-flash): root_link 'chassis' over a part list that
    spelt it differently killed the run after two re-asks.  Same word-boundary match as
    the joint-fragment repair — never a bare substring."""
    d = _raw()
    real_root = d["root_link"]
    _rename_part(d, real_root, "TrackedChassis")
    d["root_link"] = "Chassis"
    p = ArticulatedPlan.model_validate(d)
    assert p.root_link == "TrackedChassis"
    assert any("root_link 'Chassis'" in n and "TrackedChassis" in n for n in p.normalisations)


def test_an_ambiguous_root_link_still_fails_validation():
    d = _raw()
    real_root = d["root_link"]
    _rename_part(d, real_root, "TrackedChassis")
    other = next(x["name"] for x in d["parts"] if x["name"] != "TrackedChassis")
    _rename_part(d, other, "ChassisMount")
    d["root_link"] = "Chassis"
    with pytest.raises(ValidationError, match="root_link"):
        ArticulatedPlan.model_validate(d)

