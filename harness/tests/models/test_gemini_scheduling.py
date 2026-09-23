"""GeminiModel's side of the key pool: slots, the shared pool, the retry budget, the hedge.

Offline: the provider is a fake client, the pool is real, and nothing sleeps.
"""

from __future__ import annotations

import pytest
from google.genai import errors as genai_errors

from codeverse3d.contracts.chat import ChatMessage, ChatRequest
from codeverse3d.models.base import ModelError
from codeverse3d.models.gemini import shared_pool
from codeverse3d.models.retry import KeyPool
from tests.models.test_gemini import make_model, text_response


def api_error(code: int, msg: str = "boom") -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": msg, "code": code}})


# ------------------------------------------------------------------ slots
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
def test_shared_pool_is_keyed_by_the_cap_not_only_by_keys():
    a = shared_pool(["x1", "x2"], max_in_flight=4)
    b = shared_pool(["x1", "x2"], max_in_flight=4)
    c = shared_pool(["x1", "x2"], max_in_flight=8)
    assert a is b
    assert a is not c, "a different cap must not silently reuse another pool's slots"


def test_the_hedge_comes_from_settings(monkeypatch):
    from codeverse3d.config import Rate, get_settings

    assert make_model([])[0].hedge == 2
    assert make_model([], hedge=1)[0].hedge == 1
    monkeypatch.setattr(get_settings(), "rate", Rate(hedge=1))
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
    assert r.raw["attempts"] == 1 and r.raw["key"] == "…k1"

    hedged, log, _ = make_model(
        [api_error(503), api_error(503), text_response("recovered")],
        pool=KeyPool(["k1", "k2", "k3"], cooldown_s=0.0),
        max_attempts=3,
    )
    recovered = hedged.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert recovered.text == "recovered" and len(log) == 3
    assert recovered.raw["attempts"] == 3
    assert recovered.raw["key"] in ("…k2", "…k3")

    m2, _log2, _ = make_model(
        [api_error(503, "high demand")] * 2, keys=("k1",), max_attempts=1, storm_attempts=0
    )
    with pytest.raises(ModelError) as ei:
        m2.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert ei.value.attempts == 1, "the ledger's error row gets the count too"


# ------------------------------------------ per-attempt HTTP timeout (audit 2026-08-27)
def test_retry_budget_bounds_http_timeout_without_raising_an_explicit_ceiling():
    from codeverse3d.models.parts import SDK_TIMEOUT_FLOOR_S

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
    assert log[2]["config"].http_options.timeout == int(SDK_TIMEOUT_FLOOR_S * 1000)
    small, small_log, _ = make_model([text_response("d")], timeout_s=8.0)
    small.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=5))
    assert small_log[0]["config"].http_options.timeout == 8_000
