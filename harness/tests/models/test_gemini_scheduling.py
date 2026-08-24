"""GeminiModel's side of TPM-aware scheduling and the shared storm gate.

Offline: the provider is a fake client, the pool is real, and nothing sleeps.
"""

from __future__ import annotations

import contextlib

from google.genai import errors as genai_errors

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.models.gemini import GeminiModel, shared_pool
from codeverse.models.keypool import KeyPool
from codeverse.models.storm import StormGate
from codeverse.models.tokens import request_tokens
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


def test_an_image_heavy_judge_request_reserves_far_more_than_a_caption():
    caption = ChatRequest(messages=[ChatMessage.user("name this object in three words")])
    verdict = ChatRequest(
        messages=[ChatMessage.user(
            "score this", images=[ImagePart(data_b64="x") for _ in range(5)])],
        system="rubric " * 5000,
    )
    assert request_tokens(verdict) > 30 * request_tokens(caption)


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


def test_shared_pool_takes_its_defaults_from_settings():
    from codeverse.config import get_settings

    r = get_settings().rate
    pool = shared_pool(["y1"])
    st = pool.stats()
    assert st["rpm_capacity"] == r.rpm_per_key
    assert st["tpm_capacity"] == r.tpm_per_key


# ----------------------------------------------------------------- storm gate
def test_a_503_closes_the_shared_gate_and_the_call_still_succeeds():
    gate = StormGate("test", base_delay=0.0, max_wait_s=0.0)
    pool = KeyPool(["k1", "k2"], cooldown_s=0.0)
    m, _log, _ = make_model([api_error(503, "high demand"), text_response("ok")],
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
