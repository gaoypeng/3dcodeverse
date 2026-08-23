import pytest

from codeverse.judges.rubrics import RubricError, list_rubrics, load_rubric, rubric_from_dict

EXPECTED = {"static_object_v1", "articulated_v1", "scene_v1", "asset_v1", "reference_v1"}


def test_all_rubrics_load_and_validate():
    assert set(list_rubrics()) >= EXPECTED
    for name in EXPECTED:
        r = load_rubric(name)
        assert abs(sum(c.weight for c in r.criteria) - 1.0) < 0.02
        for c in r.criteria:
            assert set(c.anchors) >= {"1.0", "0.7", "0.4", "0.1"}
        assert any(c.id == "build_error" and c.cap == 0.0 for c in r.caps)
        assert any(c.when == "acceptance" for c in r.caps)


def test_static_rubric_shape():
    r = load_rubric("static_object_v1")
    assert r.pass_threshold == 0.72
    assert r.weights["intent_fidelity"] == 0.22
    assert r.criterion("intent_fidelity").floor == 0.30
    assert [c.id for c in r.criteria][:3] == ["intent_fidelity", "structure_plausibility", "geometry_detail"]


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


def test_content_hash_stable():
    assert load_rubric("scene_v1").content_hash() == load_rubric("scene_v1").content_hash()
    assert load_rubric("scene_v1").content_hash() != load_rubric("asset_v1").content_hash()
