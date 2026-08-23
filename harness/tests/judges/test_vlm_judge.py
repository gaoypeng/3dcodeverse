import json

import pytest

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.judges.rubrics import load_rubric
from codeverse.judges.scoring import is_degraded
from codeverse.judges.vlm_judge import VlmJudge
from codeverse.models.base import ModelError
from tests.judges.conftest import FakeChatModel, good_reply, image_parts

R = load_rubric("static_object_v1")
IDS = ["A1", "A2"]


def _judge(model, **kw):
    return VlmJudge("static_object_v1", model_id="fake:fake-1", chat_model=model, **kw)


def test_full_path_single_sample(judge_input, cache_dir):
    model = FakeChatModel([good_reply(R, IDS, 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and abs(j.overall - 0.8) < 1e-6
    assert j.scores["intent_fidelity"] == 0.8 and j.n_samples == 1 and j.score_std == 0.0
    assert j.acceptance_results == {"A1": True, "A2": True}
    assert j.improvement_plan[0].instruction.startswith("bevel")
    assert j.usage.cost_usd == pytest.approx(0.001)
    assert j.judge_backend == "fake:fake-1" and j.rubric == "static_object_v1"
    raw = json.loads(j.raw)
    assert raw["status"] == "ok" and raw["n_used"] == 1 and raw["rubric_hash"] == R.content_hash()
    assert "[verdict: overall 0.80 vs threshold 0.72 → PASS]" in j.summary
    req = model.requests[0]
    assert req.response_schema is not None and "Criteria" in req.response_schema["$defs"]
    assert req.label == "judge:static_object_v1:s0" and req.temperature == 0.2
    assert len(image_parts(req)) == 5
    assert "BLIND JUDGE" in req.system


def test_overall_is_code_computed_weighted_mean(judge_input, cache_dir):
    over = {"intent_fidelity": 1.0, "materials": 0.5}
    model = FakeChatModel([good_reply(R, IDS, 0.8, overrides=over)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    expected = sum(R.weights[c] * over.get(c, 0.8) for c in R.weights)
    assert j.overall == pytest.approx(round(expected, 4))


def test_n_samples_mean_std_and_shuffle(judge_input, cache_dir):
    model = FakeChatModel([good_reply(R, IDS, 0.7), good_reply(R, IDS, 0.9), good_reply(R, IDS, 0.8)])
    j = _judge(model, n_samples=3, cache_dir=cache_dir).judge(judge_input)
    assert j.n_samples == 3 and j.overall == pytest.approx(0.8) and j.score_std == pytest.approx(0.0816, abs=1e-3)
    assert j.usage.cost_usd == pytest.approx(0.003) and j.usage.input_tokens == 3000
    orders = [[p.label for p in image_parts(r)][1:] for r in model.requests]
    assert len({tuple(o) for o in orders}) >= 2  # view order differs between samples
    raw = json.loads(j.raw)
    assert raw["per_sample_overall"] == [0.7, 0.9, 0.8]
    # representative sample (closest to mean) supplies the narrative
    assert j.summary.startswith("A recognisable chair.")


def test_floor_fails_even_if_mean_high(judge_input, cache_dir):
    model = FakeChatModel([good_reply(R, IDS, 0.95, overrides={"intent_fidelity": 0.2})])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.overall > 0.72 and not j.passed
    assert "floors: intent_fidelity 0.20<0.30" in j.summary


def test_caps_from_gates(judge_input, cache_dir):
    judge_input.gates = [GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="LegBackLeft", message="part floating 30 mm above the rest", data={"kind": "floating"})])]
    model = FakeChatModel([good_reply(R, IDS, 0.9)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.overall == 0.6 and not j.passed
    raw = json.loads(j.raw)
    assert raw["caps"]["caps_applied"][0]["rule"] == "floating_part"
    assert raw["overall_uncapped"] == pytest.approx(0.9)
    assert "caps: floating_part≤0.60" in j.summary


def test_must_acceptance_missing_fails(judge_input, cache_dir):
    rep = good_reply(R, IDS, 0.9)
    rep["acceptance"]["A1"]["verified"] = False  # A1 is must
    j = _judge(FakeChatModel([rep]), cache_dir=cache_dir).judge(judge_input)
    assert not j.passed and j.overall == 0.6  # capped by missing_must_acceptance
    assert "must items unverified: A1" in j.summary
    rep2 = good_reply(R, IDS, 0.9)
    rep2["acceptance"]["A2"]["verified"] = False  # A2 is should → no effect on pass
    j2 = _judge(FakeChatModel([rep2]), cache_dir=cache_dir).judge(judge_input)
    assert j2.passed and j2.acceptance_results == {"A1": True, "A2": False}


def test_acceptance_majority_vote(judge_input, cache_dir):
    a = good_reply(R, IDS, 0.9)
    a["acceptance"]["A1"]["verified"] = False
    b = good_reply(R, IDS, 0.9)
    c = good_reply(R, IDS, 0.9)
    j = _judge(FakeChatModel([a, b, c]), n_samples=3, cache_dir=cache_dir).judge(judge_input)
    assert j.acceptance_results["A1"] is True and j.passed
    a2 = good_reply(R, IDS, 0.9)
    a2["acceptance"]["A1"]["verified"] = False
    b2 = good_reply(R, IDS, 0.9)
    b2["acceptance"]["A1"]["verified"] = False
    j2 = _judge(FakeChatModel([a2, b2, c]), n_samples=3, cache_dir=cache_dir).judge(judge_input)
    assert j2.acceptance_results["A1"] is False and not j2.passed


def test_retry_on_parse_failure_then_success(judge_input, cache_dir):
    model = FakeChatModel(["not json at all", good_reply(R, IDS, 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and len(model.requests) == 2
    assert model.requests[1].temperature > model.requests[0].temperature  # nudged
    assert j.usage.cost_usd == pytest.approx(0.002)  # failed attempt still charged


def test_retry_on_model_error_then_success(judge_input, cache_dir):
    model = FakeChatModel([ModelError("503", retryable=True), good_reply(R, IDS, 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and len(model.requests) == 2


def test_degraded_after_exhausting_attempts(judge_input, cache_dir):
    model = FakeChatModel(default=ModelError("boom", retryable=True))
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert not j.passed and j.overall == 0.0 and j.summary.startswith("judge_error:")
    assert is_degraded(j) and json.loads(j.raw)["status"] == "degraded"
    assert len(model.requests) == 3  # max_attempts
    assert j.n_samples == 0


def test_partial_samples_still_score(judge_input, cache_dir):
    # sample 0 fails all 3 attempts; sample 1 succeeds → judgment from 1 sample, errors recorded
    model = FakeChatModel(["bad", "bad", "bad", good_reply(R, IDS, 0.8)])
    j = _judge(model, n_samples=2, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and j.n_samples == 1
    raw = json.loads(j.raw)
    assert raw["n_requested"] == 2 and raw["n_used"] == 1 and len(raw["sample_errors"]) == 1


def test_non_retryable_error_stops_early(judge_input, cache_dir):
    model = FakeChatModel(default=ModelError("bad request", retryable=False))
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert is_degraded(j) and len(model.requests) == 2


def test_base_judge_rejects_measured_rubric(judge_input, cache_dir):
    with pytest.raises(ValueError, match="measured criteria"):
        VlmJudge("reference_v1", chat_model=FakeChatModel(), cache_dir=cache_dir).judge(judge_input)


def test_missing_render_is_loud(judge_input, cache_dir):
    from codeverse.judges.images import JudgeImageError
    judge_input.renders.views[0].path = "/nonexistent/x.png"
    with pytest.raises(JudgeImageError):
        _judge(FakeChatModel([good_reply(R, IDS)]), cache_dir=cache_dir).judge(judge_input)


def test_no_acceptance_items(judge_input, cache_dir):
    judge_input.acceptance = []
    model = FakeChatModel([good_reply(R, [], 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and j.acceptance_results == {}
    assert "acceptance" not in model.requests[0].response_schema["properties"]
