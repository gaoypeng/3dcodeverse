import pytest

from codeverse3d.judges.rubrics import RubricError, load_rubric, rubric_from_dict


def test_unknown_and_invalid_rubric():
    with pytest.raises(RubricError):
        load_rubric("nope_v9")
    bad = {"name": "x", "pass_threshold": 0.7, "criteria": [
        {"id": "a", "weight": 0.5, "description": "d", "anchors": {"1.0": "", "0.7": "", "0.4": "", "0.1": ""}}]}
    with pytest.raises(RubricError):  # weights must sum to 1
        rubric_from_dict(bad)
    bad["criteria"][0]["weight"] = 1.0
    bad["criteria"][0]["anchors"].pop("0.4")
    with pytest.raises(RubricError):  # anchors incomplete
        rubric_from_dict(bad)


