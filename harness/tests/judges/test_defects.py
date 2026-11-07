"""Defect checklist: rubric → wire schema → parse → majority vote → penalties/caps in code."""

import json

import pytest

from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.judges.rubrics import (
    JudgeParseError,
    aggregate_samples,
    apply_caps,
    load_rubric,
    parse_judge_output,
)
from codeverse3d.judges.vlm_judge import VlmJudge
from tests.judges.conftest import FakeChatModel, good_reply

R = load_rubric("static_object")
A = load_rubric("articulated")
IDS = ["A1", "A2"]


def _reply(score=0.9, defects=None):
    rep = good_reply(R, IDS, score)
    rep["defects"] = {d.id: {"present": d.id in (defects or ()), "evidence": "MONTAGE 1 top-left"} for d in R.defects}
    return rep


def test_rubrics_declare_defects_with_costs():
    assert R.defect("floating_part").penalty == 0.10 and R.defect("floating_part").cap == 0.60
    assert R.defect("wrong_object").penalty == 0.0


def test_defect_parse_variants():
    out = parse_judge_output(_reply(defects=["floating_part"]), R, IDS)
    assert out.defects["floating_part"] is True and out.defects["primitive_only"] is False
    assert out.defect_evidence["floating_part"] == "MONTAGE 1 top-left"
    # unknown ids dropped; missing ids → absent
    rep = good_reply(R, IDS, 0.8)
    rep["defects"] = {"interpenetration": {"present": True}, "made_up": {"present": True}}
    out2 = parse_judge_output(rep, R, IDS)
    assert out2.defects["interpenetration"] is True and "made_up" not in out2.defects
    assert out2.defects["floating_part"] is False and out2.defect_evidence["floating_part"] == "not answered by judge"
    rep4 = good_reply(R, IDS, 0.8)
    rep4["defects"] = {"floating_part": {"present": "yes"}}
    with pytest.raises(JudgeParseError):
        parse_judge_output(rep4, R, IDS)
    rep4["defects"] = [{"id": "floating_part", "present": True}]  # not the wire shape: a retry, never an AttributeError
    with pytest.raises(JudgeParseError):
        parse_judge_output(rep4, R, IDS)


def test_penalties_and_caps_computed_in_code(judge_input, cache_dir):
    # floating_part: -0.10 and cap 0.60; primitive_only: -0.08 no cap
    model = FakeChatModel([_reply(0.9, ["primitive_only"])])
    j = VlmJudge("static_object", chat_model=model, cache_dir=cache_dir).judge(judge_input)
    raw = json.loads(j.raw)
    assert raw["overall_uncapped"] == pytest.approx(0.9) and raw["defect_penalty"] == pytest.approx(0.08)
    assert j.overall == pytest.approx(0.82) and j.passed
    assert raw["defects"]["primitive_only"] is True and raw["caps"]["caps_applied"] == []
    assert "defects (-0.08): primitive_only" in j.summary
    model2 = FakeChatModel([_reply(0.9, ["floating_part", "primitive_only"])])
    j2 = VlmJudge("static_object", chat_model=model2, cache_dir=cache_dir).judge(judge_input)
    raw2 = json.loads(j2.raw)
    assert j2.overall == 0.6 and not j2.passed  # capped by defect:floating_part
    assert raw2["caps"]["caps_applied"][0]["rule"] == "defect:floating_part"
    # the checklist is per-sample data too
    assert raw2["samples"][0]["defects"]["floating_part"] is True


def test_defect_vote_ties_are_absent(judge_input, cache_dir, caplog):
    """D36: an exact defect-vote tie is ABSENT, whichever sample flagged it."""
    flagged_first = FakeChatModel(by_label={":s0": [_reply(0.9, ["render_artifacts"])], ":s1": [_reply(0.9)]})
    with caplog.at_level("WARNING", logger="codeverse3d.judges.vlm_judge"):
        j = VlmJudge("static_object", chat_model=flagged_first, n_samples=2, cache_dir=cache_dir).judge(judge_input)
    assert "n_samples=2 is even" in caplog.text
    raw = json.loads(j.raw)
    assert raw["defects"]["render_artifacts"] is False and j.overall == pytest.approx(0.9)
    assert raw["scoring_version"] == 4 and raw["overridden"] == []  # 4: the kind-scoped veto (scene, 2026-10-14)


def test_missing_views_cap_for_articulated():
    rest = [RenderView(name=n, path="x") for n in ("front_right_high", "top")]
    res = apply_caps(A, 0.9, [], {}, [], views=rest)
    assert res.overall == 0.5 and res.caps_applied[0].rule == "missing_pose_sheet"
    res2 = apply_caps(A, 0.9, [], {}, [], views=rest + [RenderView(name="articulation_sheet", path="s")])
    assert res2.overall == 0.9 and not res2.caps_applied
    res3 = apply_caps(A, 0.9, [], {}, [], views=rest + [RenderView(name="pose_J@upper", path="s")])
    assert res3.overall == 0.9
    # the judge pipeline passes the views through
    samples = [parse_judge_output(good_reply(A, [], 0.9), A, [])]
    j = aggregate_samples(A, samples, gates=[], acceptance_items=[], views=rest)
    assert j.overall == 0.5 and "missing_pose_sheet≤0.50" in j.summary
    j2 = aggregate_samples(A, samples, gates=[], acceptance_items=[], views=list(RenderSet(views=rest + [RenderView(name="pose_rest", path="p")]).views))
    assert j2.overall == pytest.approx(0.9)
