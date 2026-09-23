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


def test_cap_rules_are_not_part_of_the_judge_prompt():
    """D37: cap rules move ``content_hash`` but not ``judge_prompt_hash`` — they are scored in code."""
    import yaml

    from codeverse3d.judges.prompt_builder import judge_prompt_hash
    from codeverse3d.judges.rubrics import RUBRICS_DIR, rubric_from_dict

    r = load_rubric("static_object_v1")
    data = yaml.safe_load((RUBRICS_DIR / "static_object_v1.yaml").read_text())
    data["caps"] = []
    stripped = rubric_from_dict(data)
    assert stripped.content_hash() != r.content_hash(), "content_hash hashes what the YAML declares"
    # …and only that: a schema default the YAML never wrote does not re-key recorded verdicts
    assert r.content_hash() == rubric_from_dict(yaml.safe_load((RUBRICS_DIR / "static_object_v1.yaml").read_text())).content_hash()
    assert judge_prompt_hash(stripped) == judge_prompt_hash(r)
    assert "interpenetration" in judge_prompt_hash.__globals__["build_system_prompt"](r), "the checklist defect IS rendered"
