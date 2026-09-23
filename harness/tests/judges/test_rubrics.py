import pytest

from codeverse3d.judges.rubrics import RubricError, load_rubric, rubric_from_dict


def test_reference_rubric_has_measured_silhouette():
    r = load_rubric("reference_v1")
    assert [c.id for c in r.measured_criteria()] == ["silhouette_match"]
    assert r.criterion("silhouette_match").weight == 0.25


def test_weighted_overall_and_floors():
    r = load_rubric("static_object_v1")
    scores = {c.id: 0.8 for c in r.criteria}
    assert abs(r.weighted_overall(scores) - 0.8) < 1e-9
    scores["intent_fidelity"] = 0.2
    assert r.floors_hit(scores) == [("intent_fidelity", 0.2, 0.30)]
    with pytest.raises(RubricError):
        r.weighted_overall({"intent_fidelity": 1.0})


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


def test_cap_rule_measures_defaults_to_its_own_id_and_the_object_rubrics_name_interpenetration():
    """The object rubrics say which checklist defect each gate rule measures (the veto matches on it)."""
    from codeverse3d.judges.rubrics import CapRule

    assert CapRule(id="floating_part", cap=0.6).measures == ["floating_part"]
    assert CapRule(id="penetration_error", cap=0.7, measures=["interpenetration"]).measures == ["interpenetration"]
    for name in ("static_object_v1", "reference_v1", "asset_v1", "articulated_v1"):
        r = load_rubric(name)
        rule = next(c for c in r.caps if c.id == "penetration_error")
        assert "interpenetration" in rule.measures, name
        assert next(c for c in r.caps if c.id == "missing_must_acceptance").graded, name
    for name in ("static_object_v1", "reference_v1", "asset_v1"):
        r = load_rubric(name)
        assert {c.gate for c in r.caps if c.id in ("floating_part", "penetration_error")} == {"connectivity"}, name
    # articulated keeps gate "*": connectivity AND joint_sweep both report floating / penetration there
    a = load_rubric("articulated_v1")
    assert {c.gate for c in a.caps if c.id in ("floating_part", "penetration_error")} == {"*"}
    for name in ("scene_v1", "shader_v1", "shader_v2"):  # not object rubrics: the flat cap stays
        assert not next(c for c in load_rubric(name).caps if c.id == "missing_must_acceptance").graded, name


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
