"""ReferenceJudge: the reference labelling, the mismatch pass, best-view IoU."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.contracts.spec import ReferenceImage
from codeverse3d.judges.base import JudgeInput
from codeverse3d.judges.rubrics import load_rubric
from codeverse3d.judges.vlm_judge import ReferenceJudge
from tests.judges.conftest import (
    FakeChatModel,
    draw_chair,
    good_reply,
    make_measurement,
    make_renders,
    make_spec,
)

REF = load_rubric("reference")

DIFF = {"mismatches": [{"kind": "wrong_part_count", "target": "Legs",
                        "detail": "reference shows 4 legs, render shows 2", "severity": "critical"}],
        "matches": ["the backrest slats are right"]}


@pytest.fixture
def ref_input(tmp_path):
    png = draw_chair(tmp_path / "ref.png", legs=4, color=(30, 30, 30))
    spec = make_spec(references=[ReferenceImage(path=str(png), role="target",
                                                note="my photo")])
    return JudgeInput(
        spec=spec, renders=make_renders(tmp_path / "renders"), measurement=make_measurement(),
        acceptance=[AcceptanceItem(id="A1", text="four legs", how="measure", priority="must")])


def _judge(model, **kw):
    return ReferenceJudge("fake:fake-1", chat_model=model, silhouette_fn=lambda a, b: {"iou": 0.6, "reliable": True},
                          **kw)


@pytest.mark.parametrize(("thinking", "expected"), [(None, "medium"), ("off", "off")])
def test_reference_that_contradicts_the_brief_scores_neutral(tmp_path, switch, thinking, expected):
    from codeverse3d.contracts.spec import Constraints
    from codeverse3d.judges.vlm_judge import NEUTRAL_SCORE

    switch("C3D_JUDGE__THINKING", "medium")
    switch("C3D_REFERENCE_DIFF", "1")
    png = draw_chair(tmp_path / "wide.png", legs=4)  # ~2:1 wide silhouette
    spec = make_spec(references=[ReferenceImage(path=str(png), role="target", note="my photo")],
                     constraints=Constraints(dimensions_m={"width": 0.45, "height": 2.0}))  # implies 0.22 w/h
    inp = JudgeInput(spec=spec, renders=make_renders(tmp_path / "r"), measurement=make_measurement(),
                     acceptance=[AcceptanceItem(id="A1", text="t", how="visual", priority="must")])
    model = FakeChatModel(by_label={"reference_diff": [DIFF], "judge": [good_reply(REF, ["A1"], 0.8)]})
    j = ReferenceJudge("fake:fake-1", chat_model=model, cache_dir=tmp_path / "cache", thinking=thinking)
    verdict = j.judge(inp)
    assert {r.label: r.thinking for r in model.requests} == {
        "reference_diff": expected, "judge:reference:r00:s0": expected,
    }
    assert verdict.scores["silhouette_match"] == NEUTRAL_SCORE
    text = [r for r in model.requests if r.label.startswith("judge")][0].messages[0].parts[0].text
    assert "REFERENCE PROPORTIONS DISAGREE WITH THE BRIEF" in text
    assert "NEUTRAL" in text


