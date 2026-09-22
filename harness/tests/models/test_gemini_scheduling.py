"""GeminiModel's side of TPM-aware scheduling and the shared storm gate.

Offline: the provider is a fake client, the pool is real, and nothing sleeps.
"""

from __future__ import annotations

import base64

import pytest
from google.genai import errors as genai_errors

from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse3d.models.base import ModelError
from codeverse3d.models.gemini import GeminiModel, shared_pool
from codeverse3d.models.retry import KeyPool, StormGate, request_tokens
from tests.models.test_gemini import PNG_1PX, make_model, text_response


def api_error(code: int, msg: str = "boom") -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": msg, "code": code}})


# ------------------------------------------------------------------ estimates
def test_request_token_estimates_scale_and_treat_images_as_fixed_cost():
    small = ChatRequest(messages=[ChatMessage.user("caption this")])
    big = ChatRequest(messages=[ChatMessage.user("x" * 800_000)])
    rich = ChatRequest(
        messages=[ChatMessage.user("hi", images=[ImagePart(data_b64="x", label="a")])],
        system="a system prompt that is quite long " * 20,
        response_schema={"type": "object", "properties": {"score": {"type": "number"}}},
    )
    path_only = ChatRequest(messages=[ChatMessage.user("hi", images=[ImagePart(path="/nope.png")])])
    large_image = ChatRequest(
        messages=[
            ChatMessage.user(
                "hi", images=[ImagePart(data_b64=base64.b64encode(PNG_1PX * 500).decode())]
            )
        ]
    )
    assert request_tokens(small) < 100
    assert 150_000 < request_tokens(big) < 250_000
    assert request_tokens(rich) > request_tokens(small) + 1000
    assert request_tokens(path_only) > 1000
    assert request_tokens(large_image) < 2000


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
    with pytest.raises(ModelError):
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
    m, _log, _ = make_model(
        [api_error(503, "high demand"), api_error(503, "high demand"), text_response("ok")],
        pool=pool,
        max_attempts=3,
    )
    m.storm_gate = gate
    r = m.generate(ChatRequest(messages=[ChatMessage.user("hi")]))
    assert r.text == "ok"
    assert gate.n_hits == 1 and gate.n_storms == 1
    assert not gate.storming, "the success must reopen the gate"


def test_rate_settings_control_the_shared_gate_and_hedge(monkeypatch):
    from codeverse3d.config import Rate, get_settings

    s = get_settings()
    assert s.rate.storm_gate is False
    assert GeminiModel("gemini-3.7-flash", keys=["z1"]).storm_gate is None
    assert make_model([])[0].hedge == 2
    assert make_model([], hedge=1)[0].hedge == 1

    monkeypatch.setattr(s, "rate", Rate(storm_gate=True, hedge=1))
    a = GeminiModel("gemini-3.7-flash", keys=["z1"])
    b = GeminiModel("gemini-3.7-flash", keys=["z1"])
    c = GeminiModel("gemini-3.1-pro-preview", keys=["z1"])
    assert a.storm_gate is not None
    assert a.storm_gate is b.storm_gate
    assert a.storm_gate is not c.storm_gate
    assert make_model([])[0].hedge == 1


# ------------------------------------------------ retry budget + hedge (audit 2026-08-26 §5.1 / §5.2)
def test_max_wait_s_clips_the_retry_deadline_and_never_extends_it(monkeypatch):
    """Audit 2026-08-26 §5.1: 66 give-up spans of the model's 900 s deadline (x3 outer retries)
    were 30 % of a storm day's waiting.  ``ChatRequest.max_wait_s`` is the caller's budget for
    the whole call; the model clips its deadline to it and never goes above its own ceiling."""
    import codeverse3d.models.gemini as gm
    from codeverse3d.models.retry import RETRY_DEADLINE_S

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


def test_a_clean_call_reports_one_attempt_and_a_failed_call_carries_its_count():
    m, _log, _ = make_model([text_response("hi")])
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert r.raw["attempts"] == 1 and r.raw["hedged"] == 0 and r.raw["key"] == "…k1"

    hedged, log, _ = make_model(
        [api_error(503), api_error(503), text_response("recovered")],
        pool=KeyPool(["k1", "k2", "k3"], cooldown_s=0.0),
        max_attempts=3,
    )
    recovered = hedged.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert recovered.text == "recovered" and len(log) == 3
    assert recovered.raw["attempts"] == 3 and recovered.raw["hedged"] == 1
    assert recovered.raw["key"] in ("…k2", "…k3")

    m2, _log2, _ = make_model(
        [api_error(503, "high demand")] * 2, keys=("k1",), max_attempts=1, storm_attempts=0
    )
    with pytest.raises(ModelError) as ei:
        m2.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert ei.value.attempts == 1, "the ledger's error row gets the count too"


# ------------------------------------------ per-attempt HTTP timeout (audit 2026-08-27)
def test_retry_budget_bounds_http_timeout_without_raising_an_explicit_ceiling():
    from codeverse3d.models.gemini import HTTP_TIMEOUT_FLOOR_S

    m, log, _ = make_model(
        [text_response("a"), text_response("b"), text_response("c")], timeout_s=300.0
    )
    m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert (
        log[0]["config"].http_options.timeout == 300_000
    )  # plenty of budget: the full read timeout
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=60))
    assert 55_000 <= log[1]["config"].http_options.timeout <= 60_000
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=5))
    assert log[2]["config"].http_options.timeout == int(HTTP_TIMEOUT_FLOOR_S * 1000)
    small, small_log, _ = make_model([text_response("d")], timeout_s=8.0)
    small.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=5))
    assert small_log[0]["config"].http_options.timeout == 8_000
