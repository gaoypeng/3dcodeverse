import json

import pytest

from codeverse3d.judges.rubrics import load_rubric
from codeverse3d.judges.vlm_judge import VlmJudge
from codeverse3d.models.base import ModelError
from tests.judges.conftest import FakeChatModel, good_reply, image_parts

R = load_rubric("static_object")
IDS = ["A1", "A2"]


def _judge(model, **kw):
    return VlmJudge("static_object", model_id="fake:fake-1", chat_model=model, **kw)


def test_full_path_single_sample(judge_input, cache_dir, judge_settings):
    model = FakeChatModel([good_reply(R, IDS, 0.8)])
    j = VlmJudge("static_object", chat_model=model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and abs(j.overall - 0.8) < 1e-6
    assert j.scores["intent_fidelity"] == 0.8 and j.n_samples == 1 and j.score_std == 0.0
    assert j.acceptance_results == {"A1": True, "A2": True}
    assert j.improvement_plan[0].instruction.startswith("bevel")
    assert j.usage.cost_usd == pytest.approx(0.001)
    assert j.judge_backend == judge_settings.backends().judge == "gemini:gemini-4-argon"
    assert j.rubric == "static_object"
    raw = json.loads(j.raw)
    assert raw["status"] == "ok" and raw["rubric_hash"] == R.content_hash()
    assert "[verdict: overall 0.80 vs threshold 0.72 → PASS]" in j.summary
    req = model.requests[0]
    assert req.thinking == "medium"
    assert req.label == "judge:static_object:r00:s0" and len(image_parts(req)) == 3  # 2x2 montage + 2 detail crops
    assert raw["defects"] == {d.id: False for d in R.defects} and raw["defect_penalty"] == 0.0

    # Explicit replay settings still override the defaults.
    override = "gemini:gemini-3.1-pro-preview"
    model = FakeChatModel([good_reply(R, IDS, 0.8)])
    j = VlmJudge("static_object", model_id=override, thinking="low", chat_model=model,
                 cache_dir=cache_dir).judge(judge_input)
    assert j.judge_backend == judge_settings.backends(judge=override).judge == override
    assert model.requests[0].thinking == "low"


@pytest.mark.parametrize("fixed_order", [False, True])
def test_n_samples_mean_std_and_view_order(judge_input, cache_dir, fixed_order):
    """Samples fan out by :s<k> label; fixed_order (the calibration mode) shows every sample the same order."""
    model = FakeChatModel(by_label={":s0": [good_reply(R, IDS, 0.7)], ":s1": [good_reply(R, IDS, 0.9)],
                                    ":s2": [good_reply(R, IDS, 0.8)]})
    j = _judge(model, n_samples=3, cache_dir=cache_dir, fixed_order=fixed_order).judge(judge_input)
    assert j.n_samples == 3 and j.overall == pytest.approx(0.8) and j.score_std == pytest.approx(0.0816, abs=1e-3)
    assert j.usage.cost_usd == pytest.approx(0.003) and j.usage.input_tokens == 3000
    assert len(json.loads(j.raw)["samples"]) == 3
    assert j.summary.startswith("A recognisable chair.")  # the representative sample's narrative
    orders = {tuple(p.label for p in image_parts(r)) for r in model.requests}
    assert (len(orders) == 1) if fixed_order else (len(orders) >= 2)
    if fixed_order:
        assert len({r.system for r in model.requests}) == 1


def test_floor_fails_even_if_mean_high(judge_input, cache_dir):
    model = FakeChatModel([good_reply(R, IDS, 0.95, overrides={"intent_fidelity": 0.2})])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.overall > 0.72 and not j.passed
    assert "floors: intent_fidelity 0.20<0.30" in j.summary


@pytest.mark.parametrize(("retryable", "calls"), [(True, 3), (False, 1)])
def test_a_failing_model_degrades_the_verdict(judge_input, cache_dir, retryable, calls):
    model = FakeChatModel(default=ModelError("boom", retryable=retryable))
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert not j.passed and j.overall == 0.0 and j.summary.startswith("judge_error:")
    assert j.degraded and json.loads(j.raw)["status"] == "degraded" and j.n_samples == 0
    assert len(model.requests) == calls  # max_attempts, or at once when not retryable
    # a degraded verdict carries the protocol hash too, so a glitch is still attributable to a protocol
    from codeverse3d.judges.prompt_builder import judge_prompt_hash
    assert json.loads(j.raw)["judge_prompt_hash"] == judge_prompt_hash(R)


def test_a_sample_stops_when_its_budget_is_spent(judge_input, cache_dir, monkeypatch):
    import time as real_time
    from types import SimpleNamespace

    import codeverse3d.judges.vlm_judge as mod
    from codeverse3d.judges.vlm_judge import SAMPLE_BUDGET_S, SAMPLE_MIN_WAIT_S

    clock = {"t": 0.0}
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: clock["t"], time=real_time.time,
                                                      sleep=real_time.sleep))

    def spent(request):
        clock["t"] += SAMPLE_BUDGET_S + 10.0  # one attempt that retried for the whole budget
        return ModelError("503 high demand", retryable=True, status=503)

    model = FakeChatModel(default=spent)
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.degraded and len(model.requests) == 1, "max_attempts=3, but the budget is gone"
    assert "sample budget" in j.summary
    # the budget is a dial: a caller that can afford more gets the attempts back
    model = FakeChatModel(default=spent)
    roomy = 3 * (SAMPLE_BUDGET_S + 10.0)
    j = _judge(model, cache_dir=cache_dir, sample_budget_s=roomy).judge(judge_input)
    assert len(model.requests) == 3
    burn = SAMPLE_BUDGET_S + 10.0
    assert [r.max_wait_s for r in model.requests] == [roomy, roomy - burn, roomy - 2 * burn]

    # the last attempt still gets a real try (SAMPLE_MIN_WAIT_S) when the budget is nearly spent
    def nearly_spent(request):
        clock["t"] += SAMPLE_BUDGET_S - 5.0
        return ModelError("503", retryable=True, status=503)

    model = FakeChatModel([nearly_spent, good_reply(R, IDS, 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and [r.max_wait_s for r in model.requests] == [SAMPLE_BUDGET_S, SAMPLE_MIN_WAIT_S]




def test_retry_on_parse_failure_then_success(judge_input, cache_dir):
    model = FakeChatModel(["not json at all", good_reply(R, IDS, 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and len(model.requests) == 2 and j.usage.cost_usd == pytest.approx(0.002)  # failed attempt still charged
