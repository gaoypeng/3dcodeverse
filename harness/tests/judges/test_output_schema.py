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


def test_parse_accepts_bare_values_defaults_missing_acceptance_and_rejects_bad_criteria():
    rep = good_reply(R, IDS)
    rep["acceptance"] = {"A1": True, "must-2": False}
    rep["criteria"] = {k: v["score"] for k, v in rep["criteria"].items()}
    out = parse_judge_output(rep, R, IDS)
    assert out.acceptance_bools == {"A1": True, "must-2": False}
    assert out.criteria["materials"].evidence == ""
    # a missing acceptance item is unverified; an unknown criterion is dropped
    rep = good_reply(R, IDS)
    rep["acceptance"].pop("must-2")
    rep["criteria"]["bogus"] = {"score": 1.0, "evidence": ""}
    out = parse_judge_output(rep, R, IDS)
    assert out.acceptance_bools["must-2"] is False
    assert "bogus" not in out.criteria
    # a missing criterion, or a score out of range, is a parse error (the judge retries)
    rep = good_reply(R, IDS)
    rep["criteria"].pop("materials")
    with pytest.raises(JudgeParseError, match="missing criteria"):
        parse_judge_output(rep, R, IDS)
    rep = good_reply(R, IDS)
    rep["criteria"]["materials"]["score"] = 1.4
    with pytest.raises(JudgeParseError):
        parse_judge_output(rep, R, IDS)
