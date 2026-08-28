"""ReferenceJudge: the synthesized labelling, the mismatch pass, best-view IoU."""

from __future__ import annotations

import pytest

from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import ReferenceImage
from codeverse.judges.base import JudgeInput
from codeverse.judges.rubrics import load_rubric
from codeverse.judges.vlm_judge import ReferenceJudge
from codeverse.reference import SYNTH_NOTE
from tests.judges.conftest import (
    FakeChatModel,
    draw_chair,
    good_reply,
    image_parts,
    make_measurement,
    make_renders,
    make_spec,
)

REF = load_rubric("reference_v1")

DIFF = {"mismatches": [{"kind": "wrong_part_count", "target": "Legs",
                        "detail": "reference shows 4 legs, render shows 2", "severity": "critical"}],
        "matches": ["the backrest slats are right"]}


@pytest.fixture
def ref_input(tmp_path):
    png = draw_chair(tmp_path / "ref.png", legs=4, color=(30, 30, 30))
    spec = make_spec(references=[ReferenceImage(path=str(png), role="target",
                                                note=f"{SYNTH_NOTE} [front view, x]")])
    return JudgeInput(
        spec=spec, renders=make_renders(tmp_path / "renders"), measurement=make_measurement(),
        acceptance=[AcceptanceItem(id="A1", text="four legs", how="measure", priority="must"),
                    AcceptanceItem(id="A2", text="slatted back", how="visual", priority="should")],
        plan_summary="parts: Seat, Backrest, LegFrontLeft, LegFrontRight")


def _judge(model, **kw):
    return ReferenceJudge("fake:fake-1", chat_model=model, silhouette_fn=lambda a, b: {"iou": 0.6, "reliable": True},
                          **kw)


def test_diff_pass_reaches_the_scoring_call(ref_input, cache_dir):
    model = FakeChatModel(by_label={"reference_diff": [DIFF], "judge": [good_reply(REF, ["A1", "A2"], 0.8)]})
    j = _judge(model, cache_dir=cache_dir).judge(ref_input)
    assert j.overall > 0
    scoring = [r for r in model.requests if r.label.startswith("judge")][0]
    text = scoring.messages[0].parts[0].text
    assert "REFERENCE DIFF" in text and "reference shows 4 legs, render shows 2" in text
    assert "already matching: the backrest slats are right" in text


def test_synthesized_reference_is_labelled_and_caveated(ref_input, cache_dir):
    model = FakeChatModel(by_label={"reference_diff": [DIFF], "judge": [good_reply(REF, ["A1", "A2"], 0.8)]})
    _judge(model, cache_dir=cache_dir).judge(ref_input)
    scoring = [r for r in model.requests if r.label.startswith("judge")][0]
    assert image_parts(scoring)[0].label.startswith("SYNTHESIZED REFERENCE 1/1 (target)")
    assert "SYNTHESIZED from the brief" in scoring.messages[0].parts[0].text
    assert "the BRIEF is correct" in scoring.messages[0].parts[0].text


def test_a_failing_diff_never_breaks_the_verdict(ref_input, cache_dir):
    model = FakeChatModel(by_label={"reference_diff": [{"garbage": 1}],
                                    "judge": [good_reply(REF, ["A1", "A2"], 0.8)]})
    j = _judge(model, cache_dir=cache_dir).judge(ref_input)
    assert j.overall > 0 and j.rubric == "reference_v1"


def test_diff_can_be_switched_off(ref_input, cache_dir):
    model = FakeChatModel(by_label={"judge": [good_reply(REF, ["A1", "A2"], 0.8)]})
    _judge(model, cache_dir=cache_dir, diff=False).judge(ref_input)
    assert all(not r.label.startswith("reference_diff") for r in model.requests)


def test_diff_off_via_env(ref_input, cache_dir, monkeypatch):
    monkeypatch.setenv("CV3D_REFERENCE_DIFF", "off")
    model = FakeChatModel(by_label={"judge": [good_reply(REF, ["A1", "A2"], 0.8)]})
    _judge(model, cache_dir=cache_dir).judge(ref_input)
    assert all(not r.label.startswith("reference_diff") for r in model.requests)


def test_best_view_iou_beats_a_fixed_front_view(ref_input, cache_dir, tmp_path):
    """The default silhouette fn is the real one, so the judge scores the render view
    that best matches the reference's camera and says which one it used."""
    model = FakeChatModel(by_label={"reference_diff": [DIFF], "judge": [good_reply(REF, ["A1", "A2"], 0.8)]})
    j = ReferenceJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir)
    verdict = j.judge(ref_input)
    scoring = [r for r in model.requests if r.label.startswith("judge")][0]
    text = scoring.messages[0].parts[0].text
    assert "MEASURED SILHOUETTE" in text and verdict.scores["silhouette_match"] > 0
    info = j.measure_silhouette(ref_input)
    assert info["render"] in {v.name for v in ref_input.renders.views}
    assert info["iou"] == max(info["per_view"].values())


def test_reference_that_contradicts_the_brief_scores_neutral(tmp_path, cache_dir):
    """A picture whose proportions disagree with the stated dimensions must not drag
    the score down: the brief wins and the measured criterion goes neutral."""
    from codeverse.contracts.spec import Constraints
    from codeverse.judges.vlm_judge import NEUTRAL_SCORE

    png = draw_chair(tmp_path / "wide.png", legs=4)  # ~2:1 wide silhouette
    spec = make_spec(references=[ReferenceImage(path=str(png), role="target", note=f"{SYNTH_NOTE} [front, x]")],
                     constraints=Constraints(dimensions_m={"width": 0.45, "height": 2.0}))  # implies 0.22 w/h
    inp = JudgeInput(spec=spec, renders=make_renders(tmp_path / "r"), measurement=make_measurement(),
                     acceptance=[AcceptanceItem(id="A1", text="t", how="visual", priority="must")])
    model = FakeChatModel(by_label={"reference_diff": [DIFF], "judge": [good_reply(REF, ["A1"], 0.8)]})
    j = ReferenceJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir)
    verdict = j.judge(inp)
    assert verdict.scores["silhouette_match"] == NEUTRAL_SCORE
    text = [r for r in model.requests if r.label.startswith("judge")][0].messages[0].parts[0].text
    assert "REFERENCE PROPORTIONS DISAGREE WITH THE BRIEF" in text
    assert "NEUTRAL" in text


def test_part_names_are_read_from_both_plan_digest_shapes():
    from codeverse.judges.vlm_judge import _plan_part_names

    class One:
        plan_summary = ("Chair: a chair. Overall 0.5x0.5x0.9 m. "
                        "Parts: Seat, SeatFrameApron, FrontLeg×2, CurvedBackrest.")

    class Many:
        plan_summary = "- Seat · surface · walnut\n- BackRail · frame · walnut\n- (no parts)"

    assert _plan_part_names(One()) == ["Seat", "SeatFrameApron", "FrontLeg", "CurvedBackrest"]
    assert _plan_part_names(Many()) == ["Seat", "BackRail"]


def test_the_diff_call_is_charged_to_the_verdict(ref_input, cache_dir):
    """An uncharged model call would hide from BudgetGuard and record.total_usage."""
    model = FakeChatModel(by_label={"reference_diff": [DIFF], "judge": [good_reply(REF, ["A1", "A2"], 0.8)]},
                          cost=0.01)
    with_diff = _judge(model, cache_dir=cache_dir).judge(ref_input)
    model2 = FakeChatModel(by_label={"judge": [good_reply(REF, ["A1", "A2"], 0.8)]}, cost=0.01)
    without = _judge(model2, cache_dir=cache_dir, diff=False).judge(ref_input)
    assert with_diff.usage.cost_usd > without.usage.cost_usd
