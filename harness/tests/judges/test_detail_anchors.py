"""Guard rail (eval/docs/COMPLEXITY.md): the geometry-detail anchors stay countable and caps stay put."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.judges.prompt_builder import build_system_prompt
from codeverse3d.judges.rubrics import _rule_hit, load_rubric

#: rubric -> the id of its geometry-detail criterion
DETAIL_CRITERION = {
    "static_object_v1": "geometry_detail",
    "articulated_v1": "geometry_detail",
    "asset_v1": "detail_level",
    "reference_v1": "detail",
}


@pytest.mark.parametrize(("rubric", "criterion"), sorted(DETAIL_CRITERION.items()))
def test_detail_anchors_are_a_graded_countable_geometry_ladder(rubric: str, criterion: str) -> None:
    r = load_rubric(rubric)
    c = r.criterion(criterion)
    levels = sorted((float(k) for k in c.anchors), reverse=True)
    assert len(levels) >= 5 and levels[0] == 1.0 and levels[-1] == 0.1, levels
    top = c.anchors["1.0"].lower()
    assert any(w in top for w in ("five or more", "nearly every")), top
    assert any(w in top for w in ("kind", "detail")), top
    text = (c.description + " " + r.extra_instructions).lower()
    assert "colour" in text and ("not" in text or "never" in text), "colour is not geometry"


def test_static_notes_carry_the_refinement_kinds_and_the_majority_primitive_rule() -> None:
    r = load_rubric("static_object_v1")
    prompt = build_system_prompt(r)
    for kind in ("REFINEMENT KINDS", "bevel", "taper", "cut-out", "wall thickness", "surface relief", "hardware",
                 "DETAIL AND SIZE", "box-stack"):
        assert kind in prompt, kind
    text = r.defect("primitive_only").text.lower()
    assert "two thirds" in text and "colour" in text
    for rubric in ("static_object_v1", "articulated_v1"):
        c = load_rubric(rubric).criterion("craftsmanship_no_artifacts")
        assert "detail scale" in c.anchors["1.0"].lower() and "0.85" in c.anchors


def test_physical_plausibility_caps_are_untouched() -> None:
    """The complexity wave may sharpen detail; it may never soften the gates."""
    caps = {c.id: c for c in load_rubric("static_object_v1").caps}
    assert caps["build_error"].cap == 0.0
    assert caps["floating_part"].cap == 0.6
    assert caps["penetration_error"].cap == 0.7
    assert caps["contract_violation"].cap == 0.75
    assert caps["missing_must_acceptance"].cap == 0.6
    defects = {d.id: d for d in load_rubric("static_object_v1").defects}
    assert defects["floating_part"].cap == 0.6 and defects["interpenetration"].cap == 0.7
    assert defects["wrong_object"].cap == 0.25


@pytest.mark.parametrize("rubric", ["static_object_v1", "articulated_v1"])
def test_acceptance_cap_is_graded_with_the_floor_pinned(rubric: str) -> None:
    """D46 b: the acceptance cap is graded, 0.6 + 0.4·verified/total — the flag and the floor pinned."""
    rule = next(c for c in load_rubric(rubric).caps if c.id == "missing_must_acceptance")
    assert rule.when == "acceptance" and rule.cap == 0.6 and rule.graded is True
    items = [AcceptanceItem(id=f"M{i}", text=f"must {i}", priority="must") for i in range(10)]

    def cap_at(verified: int) -> float:
        results = {a.id: i < verified for i, a in enumerate(items)}
        hit = _rule_hit(rule, [], results, items, [], [])
        assert hit is not None
        return hit.cap

    assert cap_at(0) == pytest.approx(0.6)
    assert cap_at(9) == pytest.approx(0.96)
    assert _rule_hit(rule, [], {a.id: True for a in items}, items, [], []) is None
