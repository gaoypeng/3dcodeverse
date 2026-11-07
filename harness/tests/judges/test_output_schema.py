import pytest

from codeverse3d.judges.rubrics import (
    JudgeParseError,
    load_rubric,
    parse_judge_output,
)
from tests.judges.conftest import good_reply

R = load_rubric("static_object")
IDS = ["A1", "must-2"]


def test_parse_defaults_missing_acceptance_drops_unknown_ids_and_rejects_bad_criteria():
    # a missing acceptance item is unverified; an unknown acceptance id or criterion is dropped
    rep = good_reply(R, IDS)
    rep["acceptance"]["zzz"] = {"verified": True, "evidence": ""}
    rep["acceptance"].pop("must-2")
    rep["criteria"]["bogus"] = {"score": 1.0, "evidence": ""}
    out = parse_judge_output(rep, R, IDS)
    assert out.acceptance_bools["must-2"] is False
    assert "bogus" not in out.criteria and "zzz" not in out.acceptance
    # a missing criterion, or a score out of range, is a parse error (the judge retries)
    rep = good_reply(R, IDS)
    rep["criteria"].pop("materials")
    with pytest.raises(JudgeParseError, match="missing criteria"):
        parse_judge_output(rep, R, IDS)
    rep = good_reply(R, IDS)
    rep["criteria"]["materials"]["score"] = 1.4
    with pytest.raises(JudgeParseError):
        parse_judge_output(rep, R, IDS)
