import json

import pytest

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse3d.judges.rubrics import is_degraded, load_rubric
from codeverse3d.judges.vlm_judge import VlmJudge
from codeverse3d.models.base import ModelError
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
    assert req.label == "judge:static_object_v1:r00:s0" and req.temperature == 0.2
    assert len(image_parts(req)) == 3  # 2×2 montage + 2 detail crops
    assert "BLIND JUDGE" in req.system and "DEFECT CHECKLIST" in req.system
    assert "Defects" in req.response_schema["$defs"]
    assert raw["defects"] == {d.id: False for d in R.defects} and raw["defect_penalty"] == 0.0


def test_overall_is_code_computed_weighted_mean(judge_input, cache_dir):
    over = {"intent_fidelity": 1.0, "materials": 0.5}
    model = FakeChatModel([good_reply(R, IDS, 0.8, overrides=over)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    expected = sum(R.weights[c] * over.get(c, 0.8) for c in R.weights)
    assert j.overall == pytest.approx(round(expected, 4))


def test_n_samples_mean_std_and_shuffle(judge_input, cache_dir):
    # samples fan out in parallel: route replies by the request's :s<k> label
    model = FakeChatModel(by_label={":s0": [good_reply(R, IDS, 0.7)], ":s1": [good_reply(R, IDS, 0.9)],
                                    ":s2": [good_reply(R, IDS, 0.8)]})
    j = _judge(model, n_samples=3, cache_dir=cache_dir).judge(judge_input)
    assert j.n_samples == 3 and j.overall == pytest.approx(0.8) and j.score_std == pytest.approx(0.0816, abs=1e-3)
    assert j.usage.cost_usd == pytest.approx(0.003) and j.usage.input_tokens == 3000
    orders = [[p.label for p in image_parts(r)] for r in model.requests]
    assert len({tuple(o) for o in orders}) >= 2  # montage tile order differs between samples
    raw = json.loads(j.raw)
    assert raw["per_sample_overall"] == [0.7, 0.9, 0.8]
    # representative sample (closest to mean) supplies the narrative
    assert j.summary.startswith("A recognisable chair.")


def test_fixed_order_sends_the_identical_prompt_to_every_sample(judge_input, cache_dir):
    """The σ the loop keys its stop thresholds to must be the model's re-judge noise; the
    default per-sample shuffle measures view-order robustness instead (JUDGE_NOISE was
    tabulated from that).  fixed_order is the mode the calibration battery uses."""
    model = FakeChatModel(by_label={":s0": [good_reply(R, IDS, 0.7)], ":s1": [good_reply(R, IDS, 0.9)],
                                    ":s2": [good_reply(R, IDS, 0.8)]})
    j = _judge(model, n_samples=3, cache_dir=cache_dir, fixed_order=True).judge(judge_input)
    assert j.n_samples == 3
    orders = [[p.label for p in image_parts(r)] for r in model.requests]
    assert len({tuple(o) for o in orders}) == 1, "every sample must see the montages in the same order"
    assert len({r.system for r in model.requests}) == 1


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


def test_non_retryable_error_stops_at_once(judge_input, cache_dir):
    model = FakeChatModel(default=ModelError("bad request", retryable=False))
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert is_degraded(j) and len(model.requests) == 1


def test_base_judge_rejects_measured_rubric(judge_input, cache_dir):
    with pytest.raises(ValueError, match="measured criteria"):
        VlmJudge("reference_v1", chat_model=FakeChatModel(), cache_dir=cache_dir).judge(judge_input)


def test_missing_render_is_loud(judge_input, cache_dir):
    from codeverse3d.judges.prompt_builder import JudgeImageError
    judge_input.renders.views[0].path = "/nonexistent/x.png"
    with pytest.raises(JudgeImageError):
        _judge(FakeChatModel([good_reply(R, IDS)]), cache_dir=cache_dir).judge(judge_input)


def test_no_acceptance_items(judge_input, cache_dir):
    judge_input.acceptance = []
    model = FakeChatModel([good_reply(R, [], 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and j.acceptance_results == {}
    assert "acceptance" not in model.requests[0].response_schema["properties"]


# --------------------------------------------------------------- payload size (docs/COST.md §3)
def test_judge_payload_size_comes_from_the_settings_dial(monkeypatch):
    """``Settings.judge`` (set coherently by a cost profile) sizes the verdict's
    images unless the caller states its own."""
    from codeverse3d.config import Settings, get_settings
    from codeverse3d.judges.vlm_judge import VlmJudge

    s = Settings()
    s.apply_profile("economy", force=True)
    monkeypatch.setattr("codeverse3d.judges.vlm_judge.get_settings", lambda: s)
    j = VlmJudge(rubric="static_object_v1", model_id="fake:fake-1")
    # no profile shrinks the payload: 768 px bills the same as 1024 on Gemini and is
    # noisier, the 2→1 crop cut did not survive a second draw (docs/COST.md §14), and
    # fewer than 5 montages would silently drop the rig's low ring + poles (D47)
    assert j.max_px == 1024 and j.detail_crops == 2 and j.max_montages == 5
    # a caller that states a size still gets it — the dial is the default, not a cap
    explicit = VlmJudge(rubric="static_object_v1", model_id="fake:fake-1", max_px=768, detail_crops=1)
    assert explicit.max_px == 768 and explicit.detail_crops == 1
    s2 = Settings(judge={"detail_crops": 0, "max_px": 512})
    monkeypatch.setattr("codeverse3d.judges.vlm_judge.get_settings", lambda: s2)
    s2.apply_profile("economy")  # a stated payload survives a profile
    assert VlmJudge(rubric="static_object_v1", model_id="fake:fake-1").detail_crops == 0
    get_settings.cache_clear()


# ----------------------------------------------------------------------------- D37 judge protocol hash
def test_judge_prompt_hash_is_recorded_in_every_verdict(judge_input, cache_dir):
    from codeverse3d.judges.prompt_builder import judge_prompt_hash

    j = _judge(FakeChatModel([good_reply(R, IDS, 0.8)]), cache_dir=cache_dir).judge(judge_input)
    raw = json.loads(j.raw)
    assert raw["judge_prompt_hash"] == judge_prompt_hash(R) and len(raw["judge_prompt_hash"]) == 12
    assert raw["rubric_hash"] == R.content_hash() and raw["judge_prompt_hash"] != raw["rubric_hash"]
    # a degraded verdict carries it too, so a glitch is still attributable to a protocol
    d = _judge(FakeChatModel([ModelError("down", retryable=False)]), cache_dir=cache_dir).judge(judge_input)
    assert is_degraded(d) and json.loads(d.raw)["judge_prompt_hash"] == judge_prompt_hash(R)
    assert _judge(FakeChatModel([]), cache_dir=cache_dir).prompt_hash == judge_prompt_hash(R)


def test_judge_prompt_hash_tracks_the_protocol_not_the_run(monkeypatch):
    from codeverse3d.judges import prompt_builder as pb
    from codeverse3d.judges.prompt_builder import judge_prompt_hash
    from codeverse3d.judges.rubrics import wire_schema

    base = judge_prompt_hash(R)
    assert judge_prompt_hash(R) == base  # deterministic
    # per-run content (acceptance ids) is NOT part of it: the schema is hashed with no ids
    assert wire_schema(R, ["A1"]) != wire_schema(R, []) and judge_prompt_hash(R) == base
    # the rubric is part of it (through the rendered rubric block)
    other = load_rubric("shader_v1")
    assert judge_prompt_hash(other) != base
    # an edit to the role prompt or the rig rules changes it — that is the whole point
    monkeypatch.setattr(pb, "_ROLE", pb._ROLE + "\nScore generously.")
    edited_role = judge_prompt_hash(R)
    assert edited_role != base
    monkeypatch.setattr(pb, "RIG_RULES", {**pb.RIG_RULES, "object": "All views show the same object."})
    assert judge_prompt_hash(R) not in (base, edited_role)


# -------------------------------------------------------- per-sample retry budget (audit 2026-08-26 §5.1)
def _fake_clock(monkeypatch):
    import time as real_time
    from types import SimpleNamespace

    import codeverse3d.judges.vlm_judge as mod

    clock = {"t": 0.0}
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: clock["t"], time=real_time.time,
                                                      sleep=real_time.sleep))
    return clock


def test_each_sample_has_a_retry_budget_and_hands_what_is_left_to_the_model(judge_input, cache_dir, monkeypatch):
    """The verdict call is 42 s p50 / 73 s p90 on a normal day, 50 / 103 s under a storm
    (audit 2026-08-26 §2); two storm-day rounds lost 1 162 s and 927 s to 3 x 300 s timeouts
    before a second sample answered in 128 s.  A sample now has SAMPLE_BUDGET_S = 240 s for all
    its attempts, and every attempt tells the model how much of it remains."""
    from codeverse3d.judges.vlm_judge import SAMPLE_BUDGET_S

    clock = _fake_clock(monkeypatch)

    def slow_503(request):
        clock["t"] += 100.0
        return ModelError("503 high demand", retryable=True, status=503)

    model = FakeChatModel([slow_503, slow_503, good_reply(R, IDS, 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and len(model.requests) == 3
    # each retry gets what is LEFT of the sample budget (the fake clock burns 100 s a call)
    assert [r.max_wait_s for r in model.requests] == [SAMPLE_BUDGET_S, SAMPLE_BUDGET_S - 100.0, SAMPLE_BUDGET_S - 200.0]


def test_a_sample_stops_when_its_budget_is_spent(judge_input, cache_dir, monkeypatch):
    from codeverse3d.judges.vlm_judge import SAMPLE_BUDGET_S

    clock = _fake_clock(monkeypatch)

    def spent(request):
        clock["t"] += SAMPLE_BUDGET_S + 10.0  # one attempt that retried for the whole budget
        return ModelError("503 high demand", retryable=True, status=503)

    model = FakeChatModel(default=spent)
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert is_degraded(j) and len(model.requests) == 1, "max_attempts=3, but the budget is gone"
    assert "sample budget" in j.summary
    # the budget is a dial: a caller that can afford more gets the attempts back
    model = FakeChatModel(default=spent)
    roomy = 3 * (SAMPLE_BUDGET_S + 10.0)
    j = _judge(model, cache_dir=cache_dir, sample_budget_s=roomy).judge(judge_input)
    assert len(model.requests) == 3
    burn = SAMPLE_BUDGET_S + 10.0
    assert [r.max_wait_s for r in model.requests] == [roomy, roomy - burn, roomy - 2 * burn]


def test_the_last_attempt_still_gets_a_real_try(judge_input, cache_dir, monkeypatch):
    """A few seconds of budget would be a deadline the model cannot use; the floor is 20 s."""
    from codeverse3d.judges.vlm_judge import SAMPLE_BUDGET_S, SAMPLE_MIN_WAIT_S

    clock = _fake_clock(monkeypatch)

    def nearly_spent(request):
        clock["t"] += SAMPLE_BUDGET_S - 5.0
        return ModelError("503", retryable=True, status=503)

    model = FakeChatModel([nearly_spent, good_reply(R, IDS, 0.8)])
    j = _judge(model, cache_dir=cache_dir).judge(judge_input)
    assert j.passed and [r.max_wait_s for r in model.requests] == [SAMPLE_BUDGET_S, SAMPLE_MIN_WAIT_S]


def test_scene_and_graphics_keep_the_pre_d47_montage_ceiling():
    """PR #3 review: montages 3→5 was measured only on the D47 object rig; the other
    tracks keep 3 until someone measures them, and an explicit caller value wins."""
    from codeverse3d.contracts.artifacts import RenderSet
    from codeverse3d.contracts.common import Language, Track
    from codeverse3d.contracts.spec import Spec
    from codeverse3d.judges.base import JudgeInput
    from codeverse3d.judges.vlm_judge import VlmJudge

    def inp(track, language):
        spec = Spec(id="t", track=track, language=language, prompt="p")
        return JudgeInput(spec=spec, renders=RenderSet(), gates=[], acceptance=[])

    j = VlmJudge("scene_v1", model_id="fake:fake-1")
    assert j._montages_for(inp(Track.SCENE, Language.SCENE_THREEJS)) == 3
    assert j._montages_for(inp(Track.GRAPHICS, Language.GLSL_SHADER)) == 3
    jo = VlmJudge("static_object_v1", model_id="fake:fake-1")
    assert jo._montages_for(inp(Track.STATIC_OBJECT, Language.BLENDER)) == 5
    explicit = VlmJudge("scene_v1", model_id="fake:fake-1", max_montages=5)
    assert explicit._montages_for(inp(Track.SCENE, Language.SCENE_THREEJS)) == 5
