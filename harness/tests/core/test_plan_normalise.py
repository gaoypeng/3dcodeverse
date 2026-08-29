"""ArticulatedPlan._normalise_raw: the three planner slips that cost compare_art_v2 5 of 14 prompts."""

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


@pytest.mark.parametrize("lower,upper", [(0.0, 4 * math.pi), (-4 * math.pi, 4 * math.pi), (0.0, 6.5), (0.0, 10.0)])
def test_a_revolute_joint_over_two_pi_in_radians_becomes_continuous(lower: float, upper: float):
    """A radian range over 2π — including the ambiguous 2π..30 band (0..6.5, 0..10), which is read
    as radians (contracts/plan.py DEGREES_MIN_SPAN) — is a free axle, never a degrees conversion."""
    d = _raw()
    j = _revolute(d, lower, upper)
    p = ArticulatedPlan.model_validate(d)
    fixed = _joint(p, j)
    assert fixed.type == "continuous" and fixed.lower == fixed.upper == 0.0
    assert any("> 2π → continuous" in n for n in p.normalisations)
    assert not any("degrees" in n for n in p.normalisations)


def test_a_door_planned_in_degrees_stays_a_quarter_turn_hinge():
    """The review's case: `0..90` is degrees for radians; the old repair made it a free 360° axle
    and the sweep drove the door through its cabinet.  It stays revolute, 0..1.5708 rad."""
    d = _raw()
    j = _revolute(d, 0.0, 90.0)
    p = ArticulatedPlan.model_validate(d)
    fixed = _joint(p, j)
    assert fixed.type == "revolute"
    assert fixed.lower == 0.0 and fixed.upper == pytest.approx(1.5708, abs=1e-4) and fixed.rest == 0.0
    assert any("looked like degrees (0..90) → radians" in n for n in p.normalisations)
    assert not any("continuous" in n for n in p.normalisations)


@pytest.mark.parametrize("lower,upper,rest", [(-180.0, 0.0, 0.0), (0.0, 90.0, 90.0), (-45.0, 45.0, 0.0), (0.0, 30.0, 0.0)])
def test_degree_shaped_limits_and_rest_are_converted_together(lower: float, upper: float, rest: float):
    d = _raw()
    j = _revolute(d, lower, upper, rest)
    p = ArticulatedPlan.model_validate(d)
    fixed = _joint(p, j)
    assert fixed.type == "revolute"
    assert fixed.lower == pytest.approx(math.radians(lower)) and fixed.upper == pytest.approx(math.radians(upper))
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
    """articulated_v2 scissor_mirror (2026-08-26): the planner wrote two lines of design prose into
    the harness-only field; they must not read as harness repairs in the record."""
    from codeverse.contracts.plan import ArticulatedPlan

    raw = ArticulatedPlan._normalise_raw({"parts": [{"name": "A", "bbox": {"center": [0, 0, 0], "extents": [1, 1, 1]}}],
                                          "joints": [{"name": "j", "parent": "A", "child": "A", "type": "revolute", "lower": 0, "upper": 1}],
                                          "normalisations": ["rest pose set to 0.15 m (planner prose)"]})
    assert raw["normalisations"] == []


def test_a_joint_naming_a_part_by_a_unique_fragment_is_resolved():
    """art_med_tool_chest, 2026-08-27: the planner named parts one way and referenced
    them another in the joints, then repeated the identical mistake through both
    re-asks — the run died at the planner with 0 rounds in 8 min."""
    d = _raw()
    d["parts"][1]["name"] = "ChestDrawer"        # the plan's own name ...
    d["joints"][0]["child"] = "Drawer"           # ... referenced by one word of it
    part = "ChestDrawer"
    plan = ArticulatedPlan.model_validate(d)
    assert _joint(plan, d["joints"][0]).child == part
    assert any("resolved to the one part it names" in n for n in plan.normalisations)


def test_an_ambiguous_joint_reference_is_rejected_and_names_the_real_parts():
    """Two parts match the fragment → no safe repair; the message must then tell the
    planner what the real names are, which is what the two re-asks were missing."""
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


def test_a_root_link_that_names_no_part_becomes_the_one_link_no_joint_moves():
    """ab_repairs grand_piano, 2026-08-29: flash wrote a UUID as root_link three times and
    the pinned plan died with 'root_link … is not a part'."""
    d = _raw()
    d["root_link"] = "040fbba3-b0f3-42e1-85b3-f61b0c034293"
    plan = ArticulatedPlan.model_validate(d)
    assert plan.root_link == "Cabinet"
    assert any("is not a part" in n and "Cabinet" in n for n in plan.normalisations)


def test_an_ambiguous_root_is_still_rejected():
    d = _raw()
    d["root_link"] = "nowhere"
    d["parts"].append({"name": "Pedestal", "role": "base", "description": "a plinth", "material": "oak",
                       "bbox": {"center": [0, 0, -0.05], "extents": [0.5, 0.6, 0.1]}})
    with pytest.raises(ValidationError, match="root_link nowhere is not a part"):
        ArticulatedPlan.model_validate(d)
