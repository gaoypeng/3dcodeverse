import pytest

from codeverse.contracts.spec import ReferenceImage
from codeverse.judges.reference import ReferenceJudge, ReferenceJudgeError, iou_to_score
from codeverse.judges.rubrics import load_rubric
from tests.judges.conftest import FakeChatModel, draw_chair, good_reply, image_parts

REF = load_rubric("reference_v1")


def test_iou_mapping():
    assert iou_to_score(0.9) == 1.0 and iou_to_score(0.1) == 0.0
    assert iou_to_score(0.55) == pytest.approx(0.5)


def test_reference_judge_measures_silhouette_and_adds_reference_images(judge_input, tmp_path, cache_dir):
    ref_png = draw_chair(tmp_path / "ref.png", legs=4, color=(30, 30, 30))
    judge_input.spec = judge_input.spec.model_copy(update={"references": [ReferenceImage(path=str(ref_png), note="target chair")]})
    calls = []

    def fake_sil(render, reference):
        calls.append((render, reference))
        return {"iou": 0.7, "aspect_ratio_delta": 0.05}

    model = FakeChatModel([good_reply(REF, ["A1", "A2"], 0.8)])
    j = ReferenceJudge("fake:fake-1", chat_model=model, silhouette_fn=fake_sil, cache_dir=cache_dir).judge(judge_input)
    assert calls and calls[0][0].endswith("view_front.png") and calls[0][1] == str(ref_png)
    assert j.scores["silhouette_match"] == pytest.approx(0.75)
    expected = sum(REF.weights[c] * (0.75 if c == "silhouette_match" else 0.8) for c in REF.weights)
    assert j.overall == pytest.approx(round(expected, 4))
    req = model.requests[0]
    labels = [p.label for p in image_parts(req)]
    assert labels[0].startswith("REFERENCE 1/1 (target) — target chair") and labels[1].startswith("MONTAGE 1/1 — SHADED views")
    assert "IoU 0.700" in req.messages[0].parts[0].text
    assert "silhouette_match" not in req.response_schema["$defs"]["Criteria"]["properties"]


def test_reference_judge_requires_reference(judge_input, cache_dir):
    model = FakeChatModel([good_reply(REF, ["A1", "A2"], 0.8)])
    with pytest.raises(ReferenceJudgeError):
        ReferenceJudge("fake:fake-1", chat_model=model, silhouette_fn=lambda a, b: {"iou": 1.0}, cache_dir=cache_dir).judge(judge_input)
