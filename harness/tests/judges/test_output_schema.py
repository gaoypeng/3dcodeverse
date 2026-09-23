
import pytest

from codeverse3d.judges.rubrics import (
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
    assert "overall" not in s["properties"] and "passed" not in s["properties"]  # law 3: scored in code
    assert "acceptance" not in wire_schema(R, [])["properties"]


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
