"""GeminiModel's side of TPM-aware scheduling and the shared storm gate.

Offline: the provider is a fake client, the pool is real, and nothing sleeps.
"""

from __future__ import annotations

import contextlib

import pytest
from google.genai import errors as genai_errors

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.models.base import ModelError
from codeverse.models.gemini import GeminiModel, shared_pool
from codeverse.models.retry import KeyPool, StormGate, request_tokens
from tests.models.test_gemini import PNG_1PX, make_model, text_response


def api_error(code: int, msg: str = "boom") -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": msg, "code": code}})


# ------------------------------------------------------------------ estimates
def test_request_tokens_scales_with_the_prompt():
    small = ChatRequest(messages=[ChatMessage.user("caption this")])
    big = ChatRequest(messages=[ChatMessage.user("x" * 800_000)])
    assert request_tokens(small) < 100
    assert 150_000 < request_tokens(big) < 250_000


def test_request_tokens_counts_system_tools_schema_and_images():
    plain = ChatRequest(messages=[ChatMessage.user("hi")])
    rich = ChatRequest(
        messages=[ChatMessage.user("hi", images=[ImagePart(data_b64="x", label="a")])],
        system="a system prompt that is quite long " * 20,
        response_schema={"type": "object", "properties": {"score": {"type": "number"}}},
    )
    assert request_tokens(rich) > request_tokens(plain) + 1000  # the image alone is ~1290


# --------------------------------------------------------------- reservations
def test_generate_reserves_and_reconciles_tpm():
    pool = KeyPool(["k1"], tpm_per_key=1_000_000)
    m, _log, _ = make_model([text_response("hi")], pool=pool)
    before = pool._by_key["k1"].tpm.tokens  # noqa: SLF001
    m.generate(ChatRequest(messages=[ChatMessage.user("y" * 40_000)]))
    after = pool._by_key["k1"].tpm.tokens  # noqa: SLF001
    # the fake provider reports prompt_token_count=100, so the ~10k estimate is
    # refunded down to the real 100 (allow a little refill on the wall clock)
    assert before - after < 500, f"reservation was not reconciled: {before - after}"


def test_a_failed_call_gives_its_reservation_back():
    pool = KeyPool(["k1", "k2"], tpm_per_key=1_000_000, cooldown_s=0.0)
    m, _log, _ = make_model([api_error(500), text_response("ok")], pool=pool, max_attempts=3)
    m.generate(ChatRequest(messages=[ChatMessage.user("z" * 40_000)]))
    total = sum(s.tpm.tokens for s in pool._states)  # noqa: SLF001
    assert total > 2_000_000 - 5_000  # only the one real 100-token call was charged


def test_in_flight_returns_to_zero_after_success_and_after_failure():
    pool = KeyPool(["k1", "k2"], cooldown_s=0.0)
    m, _log, _ = make_model([text_response("hi")], pool=pool)
    m.generate(ChatRequest(messages=[ChatMessage.user("hi")]))
    assert pool.stats()["in_flight"] == 0
    m2, _log2, _ = make_model([api_error(400, "bad request")], pool=pool, max_attempts=1)
    with contextlib.suppress(Exception):  # the point is the gauge, not the error
        m2.generate(ChatRequest(messages=[ChatMessage.user("hi")]))
    assert pool.stats()["in_flight"] == 0


# ---------------------------------------------------------------- shared pool
def test_shared_pool_is_keyed_by_quota_not_only_by_keys():
    a = shared_pool(["x1", "x2"], rpm_per_key=100, tpm_per_key=1000)
    b = shared_pool(["x1", "x2"], rpm_per_key=100, tpm_per_key=1000)
    c = shared_pool(["x1", "x2"], rpm_per_key=100, tpm_per_key=2000)
    assert a is b
    assert a is not c, "a different quota must not silently reuse another pool's buckets"


# ----------------------------------------------------------------- storm gate
def test_a_503_closes_the_shared_gate_and_the_call_still_succeeds():
    # probe_lease_s=0: the free rotation re-enters the gate, so this thread takes the
    # probe slot and would then park on its OWN 30 s lease until the retry deadline —
    # 30 s of wall clock for a test about whether the call survives the storm.
    gate = StormGate("test", base_delay=0.0, max_wait_s=0.0, probe_lease_s=0.0)
    pool = KeyPool(["k1", "k2"], cooldown_s=0.0)
    # a 503 is per key at any instant (retry.py docstring, measured 2026-08-26): the first
    # one rotates to k2 for free; only k2's 503 — no untried key left in this 2-key pool —
    # makes it a storm and closes the gate
    m, _log, _ = make_model([api_error(503, "high demand"), api_error(503, "high demand"), text_response("ok")],
                            pool=pool, max_attempts=3)
    m.storm_gate = gate
    r = m.generate(ChatRequest(messages=[ChatMessage.user("hi")]))
    assert r.text == "ok"
    assert gate.n_hits == 1 and gate.n_storms == 1
    assert not gate.storming, "the success must reopen the gate"


def test_the_gate_is_off_by_default_because_it_lost_its_ab():
    """docs/COST.md §21: the shared gate cost 45-56 % of throughput, so it ships off."""
    from codeverse.config import get_settings

    assert get_settings().rate.storm_gate is False
    assert GeminiModel("gemini-3.7-flash", keys=["z1"]).storm_gate is None


def test_when_enabled_the_gate_is_shared_per_model(monkeypatch):
    from codeverse.config import Rate, get_settings

    s = get_settings()
    monkeypatch.setattr(s, "rate", Rate(storm_gate=True))
    a = GeminiModel("gemini-3.7-flash", keys=["z1"])
    b = GeminiModel("gemini-3.7-flash", keys=["z1"])
    c = GeminiModel("gemini-3.1-pro-preview", keys=["z1"])
    assert a.storm_gate is not None
    assert a.storm_gate is b.storm_gate
    assert a.storm_gate is not c.storm_gate


def test_image_part_without_data_still_estimates():
    """request_tokens must never raise on a part the estimator cannot read."""
    req = ChatRequest(messages=[ChatMessage.user("hi", images=[ImagePart(path="/nope.png")])])
    assert request_tokens(req) > 1000


def test_png_bytes_are_not_counted_as_text():
    """A base64 image is a flat per-image cost, not len(b64)/4 tokens."""
    import base64

    b64 = base64.b64encode(PNG_1PX * 500).decode()
    req = ChatRequest(messages=[ChatMessage.user("hi", images=[ImagePart(data_b64=b64)])])
    assert request_tokens(req) < 2000


# ------------------------------------------------ retry budget + hedge (audit 2026-08-26 §5.1 / §5.2)
def test_max_wait_s_clips_the_retry_deadline_and_never_extends_it(monkeypatch):
    """Audit 2026-08-26 §5.1: 66 give-up spans of the model's 900 s deadline (x3 outer retries)
    were 30 % of a storm day's waiting.  ``ChatRequest.max_wait_s`` is the caller's budget for
    the whole call; the model clips its deadline to it and never goes above its own ceiling."""
    import codeverse.models.gemini as gm
    from codeverse.models.retry import RETRY_DEADLINE_S

    seen: list[float] = []
    real = gm.rotate_with_retries

    def spy(pool, call, **kw):
        seen.append(kw["max_total_s"])
        return real(pool, call, **kw)

    monkeypatch.setattr(gm, "rotate_with_retries", spy)
    m, _log, _ = make_model([text_response("a"), text_response("b"), text_response("c")])
    m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=30))
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=5000))
    assert seen == [RETRY_DEADLINE_S, 30.0, RETRY_DEADLINE_S]


def test_max_wait_s_must_be_positive():
    """0 would mean "no deadline" to rotate_with_retries — the opposite of what a caller
    that is out of time wants — so the contract refuses it."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=0)


def test_a_second_503_is_hedged_across_keys_and_the_response_says_how_hard_it_was():
    """§5.2: after the first 503 (a free rotation) the next attempt goes out on two fresh
    keys at once; the response carries the winner's key suffix and the round-trip count."""
    pool = KeyPool(["k1", "k2", "k3"], cooldown_s=0.0)
    m, log, _ = make_model([api_error(503, "high demand"), api_error(503, "high demand"), text_response("ok")],
                           pool=pool, max_attempts=3)
    r = m.generate(ChatRequest(messages=[ChatMessage.user("hi")]))
    assert r.text == "ok" and len(log) == 3
    assert r.raw["attempts"] == 3 and r.raw["hedged"] == 1
    assert r.raw["key"] in ("…k2", "…k3"), "the key that answered, never the first one that 503'd"


def test_the_hedge_is_a_settings_knob_and_a_constructor_argument(monkeypatch):
    from codeverse.config import Rate, get_settings

    assert make_model([])[0].hedge == 2, "Settings.rate.hedge default"
    assert make_model([], hedge=1)[0].hedge == 1
    monkeypatch.setattr(get_settings(), "rate", Rate(hedge=1))
    assert make_model([])[0].hedge == 1, "CV3D_RATE__HEDGE=1 is the A/B switch"


def test_a_clean_call_reports_one_attempt_and_a_failed_call_carries_its_count():
    m, _log, _ = make_model([text_response("hi")])
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert r.raw["attempts"] == 1 and r.raw["hedged"] == 0 and r.raw["key"] == "…k1"
    m2, _log2, _ = make_model([api_error(503, "high demand")] * 2, keys=("k1",), max_attempts=1, storm_attempts=0)
    with pytest.raises(ModelError) as ei:
        m2.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert ei.value.attempts == 1, "the ledger's error row gets the count too"
# ------------------------------------------ per-attempt HTTP timeout (audit 2026-08-27)
def test_a_small_budget_shortens_the_per_attempt_http_timeout():
    """A 20 s-budget turn used to hand the provider a 300 s socket: the per-attempt
    HTTP timeout is now min(timeout_s, remaining budget), floored at
    HTTP_TIMEOUT_FLOOR_S so a nearly-expired call still gets one real attempt."""
    from codeverse.models.gemini import HTTP_TIMEOUT_FLOOR_S

    m, log, _ = make_model([text_response("a"), text_response("b"), text_response("c")],
                           timeout_s=300.0)
    m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert log[0]["config"].http_options.timeout == 300_000  # plenty of budget: the full read timeout
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=60))
    assert 55_000 <= log[1]["config"].http_options.timeout <= 60_000
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=5))
    assert log[2]["config"].http_options.timeout == int(HTTP_TIMEOUT_FLOOR_S * 1000)


def test_the_floor_never_raises_an_explicitly_small_read_timeout():
    """A model built with timeout_s below the floor keeps its own ceiling."""
    m, log, _ = make_model([text_response("a")], timeout_s=8.0)
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=5))
    assert log[0]["config"].http_options.timeout == 8_000
