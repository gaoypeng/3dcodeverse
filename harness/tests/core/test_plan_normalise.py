"""Articulated-plan normalization and safe repair."""

from __future__ import annotations

import copy
import math

import pytest

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import ArticulatedPlan
from codeverse3d.tracks.planner import plan_example


def _raw() -> dict:
    d = copy.deepcopy(plan_example(Track.ARTICULATED_OBJECT))
    p = ArticulatedPlan.model_validate(d)
    assert p.normalisations == [] and [x.name for x in p.parts] == [x["name"] for x in d["parts"]]  # a valid plan is untouched
    return d


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


def test_degree_shaped_limits_convert_together_and_a_two_turn_range_becomes_continuous():
    d = _raw()
    j = _revolute(d, 0.0, 90.0, 90.0)
    p = ArticulatedPlan.model_validate(d)
    fixed = _joint(p, j)
    assert fixed.type == "revolute"
    assert (fixed.lower, fixed.upper, fixed.rest) == pytest.approx((0.0, math.radians(90), math.radians(90)))
    assert sum("looked like degrees" in n for n in p.normalisations) == 1
    assert not any("continuous" in n for n in p.normalisations)

    d = _raw()
    j = _revolute(d, -360.0, 360.0)
    p = ArticulatedPlan.model_validate(d)
    assert _joint(p, j).type == "continuous"
    assert any("looked like degrees" in n for n in p.normalisations) and any("> 2π → continuous" in n for n in p.normalisations)


def test_a_joint_naming_a_sub_part_by_word_subset_promotes_it():
    d = _raw()
    parent = d["parts"][0]
    parent.setdefault("children", []).append(
        {"name": "GlazedFrontDoor", "role": "hinged door", "description": "a glazed door",
         "bbox": {"center": list(parent["bbox"]["center"]), "extents": [e * 0.4 for e in parent["bbox"]["extents"]]},
         "material": "oak and glass"})
    d["joints"].append({"name": "CaseToGlazedDoor", "type": "revolute", "parent": parent["name"],
                        "child": "GlazedDoor", "axis": [0, 1, 0], "pivot": [0.2, 0.5, 0],
                        "lower": 0.0, "upper": 1.5, "rest": 0.0, "motion": "door swings open"})
    p = ArticulatedPlan.model_validate(d)
    names = [x.name for x in p.parts]
    assert "GlazedFrontDoor" in names
    j = next(x for x in p.joints if x.name == "CaseToGlazedDoor")
    assert j.child == "GlazedFrontDoor"
    assert any("resolved to sub-part GlazedFrontDoor" in n for n in p.normalisations)
