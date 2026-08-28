import json

import pytest

from codeverse.judges.rubrics import (
    JudgeParseError,
    load_rubric,
    parse_judge_output,
    wire_schema,
)
from tests.judges.conftest import good_reply

R = load_rubric("static_object_v1")
IDS = ["A1", "must-2"]


def test_wire_schema_has_fixed_keys_and_no_overall():
    s = wire_schema(R, IDS)
    crit = s["$defs"]["Criteria"]
    assert set(crit["required"]) == {c.id for c in R.criteria}
    acc = s["$defs"]["Acceptance"]
    assert set(acc["properties"]) == set(IDS)
    assert "overall" not in s["properties"] and "passed" not in s["properties"]


def test_wire_schema_without_acceptance_omits_block():
    s = wire_schema(R, [])
    assert "acceptance" not in s["properties"]


def test_parse_dict_form():
    out = parse_judge_output(good_reply(R, IDS, 0.8), R, IDS)
    assert set(out.scores) == {c.id for c in R.criteria}
    assert out.acceptance_bools == {"A1": True, "must-2": True}
    assert out.improvement_plan[0].target == "Seat"


def test_parse_list_form_and_fenced_text():
    rep = good_reply(R, IDS, 0.7)
    rep["criteria"] = [{"id": k, **v} for k, v in rep["criteria"].items()]
    rep["acceptance"] = [{"id": k, "verified": v["verified"]} for k, v in rep["acceptance"].items()]
    text = "Here you go:\n```json\n" + json.dumps(rep) + "\n```"
    out = parse_judge_output(text, R, IDS)
    assert out.scores["materials"] == 0.7
    assert out.acceptance_bools["A1"] is True


def test_parse_bare_bool_acceptance_and_numeric_scores():
    rep = good_reply(R, IDS)
    rep["acceptance"] = {"A1": True, "must-2": False}
    rep["criteria"] = {k: v["score"] for k, v in rep["criteria"].items()}
    out = parse_judge_output(rep, R, IDS)
    assert out.acceptance_bools == {"A1": True, "must-2": False}
    assert out.criteria["materials"].evidence == ""


def test_missing_criterion_raises():
    rep = good_reply(R, IDS)
    rep["criteria"].pop("materials")
    with pytest.raises(JudgeParseError, match="missing criteria"):
        parse_judge_output(rep, R, IDS)


def test_missing_acceptance_defaults_false_and_unknown_criteria_dropped():
    rep = good_reply(R, IDS)
    rep["acceptance"].pop("must-2")
    rep["criteria"]["bogus"] = {"score": 1.0, "evidence": ""}
    out = parse_judge_output(rep, R, IDS)
    assert out.acceptance_bools["must-2"] is False
    assert "bogus" not in out.criteria


def test_out_of_range_score_raises():
    rep = good_reply(R, IDS)
    rep["criteria"]["materials"]["score"] = 1.4
    with pytest.raises(JudgeParseError):
        parse_judge_output(rep, R, IDS)


def test_measured_injection():
    ref = load_rubric("reference_v1")
    rep = good_reply(ref, [], 0.8)  # visual criteria only
    out = parse_judge_output(rep, ref, [], measured_scores={"silhouette_match": 0.55})
    assert out.scores["silhouette_match"] == 0.55
    with pytest.raises(JudgeParseError):
        parse_judge_output(rep, ref, [])  # measured criterion missing


def test_text_reply_without_json_raises_judge_parse_error():
    """String payloads go through the shared lenient parser; its JsonParseError is
    re-raised as JudgeParseError so callers keep one exception surface."""
    with pytest.raises(JudgeParseError, match="no JSON object"):
        parse_judge_output("no json here", R, IDS)
