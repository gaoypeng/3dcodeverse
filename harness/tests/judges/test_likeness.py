"""LikenessJudge: the reference photos ride beside the frames; nothing is silhouette-matched."""

from __future__ import annotations

from codeverse.contracts.common import Language, Track
from codeverse.contracts.spec import ReferenceImage
from codeverse.judges.reference import LIKENESS_NOTE, LikenessJudge
from codeverse.judges.rubrics import load_rubric
from tests.judges.conftest import FakeChatModel, draw_chair, good_reply, image_parts, make_spec


def test_likeness_context_attaches_the_photos_and_the_note(judge_input, tmp_path, cache_dir):
    """Measured 2026-08-26 (teaser aurora ×3, flash + codex): 0.78–0.94 with empty issues while
    nothing looked like an aurora — the rubric had no photo to hold the frames against."""
    ref = tmp_path / "aurora_green_spiral_sea.png"
    draw_chair(ref)
    judge_input.spec = make_spec(track=Track.GRAPHICS, language=Language.GLSL_SHADER,
                                 references=[ReferenceImage(path=str(ref), role="likeness", note="aurora green spiral sea")])
    model = FakeChatModel([good_reply(load_rubric("shader_v1"), ["A1", "A2"], 0.8)])
    j = LikenessJudge("fake:fake-1", rubric="shader_v1", chat_model=model, cache_dir=cache_dir)
    ctx = j.context(judge_input)
    assert ctx.extra_images == [("REAL-WORLD REFERENCE 1/1 — aurora green spiral sea", str(ref))]
    assert ctx.extra_text == LIKENESS_NOTE and "evenly spaced bars" in LIKENESS_NOTE
    assert ctx.measured_scores == {}, "nothing is measured: a shader has no silhouette"
    verdict = j.judge(judge_input)
    assert verdict.overall > 0.7
    req = model.requests[0]
    labels = [p.label for p in image_parts(req)]
    assert labels[0].startswith("REAL-WORLD REFERENCE 1/1"), labels
    assert "REAL-WORLD REFERENCE PHOTOS are attached" in req.messages[0].parts[0].text


def test_no_photos_means_a_plain_judge(judge_input, cache_dir):
    judge_input.spec = make_spec(track=Track.GRAPHICS, language=Language.GLSL_SHADER)
    j = LikenessJudge("fake:fake-1", rubric="shader_v1", chat_model=FakeChatModel([good_reply(load_rubric("shader_v1"), ["A1", "A2"], 0.8)]), cache_dir=cache_dir)
    ctx = j.context(judge_input)
    assert ctx.extra_images == [] and ctx.extra_text == ""
