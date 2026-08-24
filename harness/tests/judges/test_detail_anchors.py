"""The geometry-detail anchors must stay *countable*.

The corpus study (docs/COMPLEXITY.md) found geometry_detail to be the weakest
criterion (static mean 0.667, 2 of 44 runs at ≥ 0.9) and, worse, almost blind to
objective geometry: r = +0.05 against the complexity index and +0.19 against
feature density.  The fix was to make the anchors count REFINEMENT KINDS and
coverage instead of asking for a vibe.  These tests are the guard rail: they fail
if the countable ladder, the "colour is not geometry" rule or the
"score the object BUILT" rule is quietly removed again.
"""

from __future__ import annotations

import pytest

from codeverse.judges.prompt_builder import build_system_prompt
from codeverse.judges.rubrics import load_rubric

#: rubric -> the id of its geometry-detail criterion
DETAIL_CRITERION = {
    "static_object_v1": "geometry_detail",
    "articulated_v1": "geometry_detail",
    "asset_v1": "detail_level",
    "reference_v1": "detail",
}


@pytest.mark.parametrize(("rubric", "criterion"), sorted(DETAIL_CRITERION.items()))
def test_detail_anchor_ladder_is_graded(rubric: str, criterion: str) -> None:
    """Enough rungs to place a partly-detailed artifact, and strictly ordered."""
    c = load_rubric(rubric).criterion(criterion)
    levels = sorted((float(k) for k in c.anchors), reverse=True)
    assert len(levels) >= 5, f"{rubric}.{criterion} needs intermediate anchors, got {levels}"
    assert levels[0] == 1.0 and levels[-1] == 0.1
    assert levels == sorted(levels, reverse=True)
    assert all(a > b for a, b in zip(levels, levels[1:], strict=False))


@pytest.mark.parametrize(("rubric", "criterion"), sorted(DETAIL_CRITERION.items()))
def test_top_anchor_is_reachable_and_countable(rubric: str, criterion: str) -> None:
    """0.9+ must be earnable by a stated, countable amount of geometry — not by
    'reads as a crafted asset'."""
    top = load_rubric(rubric).criterion(criterion).anchors["1.0"].lower()
    assert any(w in top for w in ("five or more", "nearly every")), top
    assert any(w in top for w in ("kind", "detail")), top


@pytest.mark.parametrize(("rubric", "criterion"), sorted(DETAIL_CRITERION.items()))
def test_detail_is_geometry_not_colour(rubric: str, criterion: str) -> None:
    """The strongest confound in the corpus: the judge read 'detail' off material
    variety.  The criterion text must say colour does not count."""
    text = (load_rubric(rubric).criterion(criterion).description
            + " " + load_rubric(rubric).extra_instructions).lower()
    assert "colour" in text
    assert "not" in text or "never" in text


def test_static_notes_carry_the_refinement_kinds_and_reach_the_judge() -> None:
    r = load_rubric("static_object_v1")
    prompt = build_system_prompt(r)
    assert "REFINEMENT KINDS" in prompt
    for kind in ("bevel", "taper", "cut-out", "wall thickness", "surface relief", "hardware"):
        assert kind in prompt, kind
    # the ceiling rule: a rich object must be allowed to outscore a clean box-stack
    assert "DETAIL AND SIZE" in prompt
    assert "box-stack" in prompt


def test_primitive_stack_defect_fires_on_a_majority_not_on_perfection() -> None:
    """The old text ('every part ... no refinement anywhere') let one token chamfer
    exempt a box stack.  The new one is a majority rule."""
    d = next(d for d in load_rubric("static_object_v1").defects if d.id == "primitive_only")
    assert "two thirds" in d.text.lower()
    assert "colour" in d.text.lower()
    assert d.penalty == pytest.approx(0.08)


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


def test_craftsmanship_top_anchor_is_detail_scale() -> None:
    for rubric in ("static_object_v1", "articulated_v1"):
        top = load_rubric(rubric).criterion("craftsmanship_no_artifacts").anchors["1.0"].lower()
        assert "detail scale" in top or "including at detail scale" in top
        assert "0.85" in load_rubric(rubric).criterion("craftsmanship_no_artifacts").anchors
