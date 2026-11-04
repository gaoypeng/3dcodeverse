"""GeminiModel's side of the key pool: slots, the shared pool, the retry budget, the hedge.

Offline: the provider is a fake client, the pool is real, and nothing sleeps.
"""

from __future__ import annotations

import pytest
from google.genai import errors as genai_errors

from codeverse3d.contracts.chat import ChatMessage, ChatRequest
from codeverse3d.models.base import ModelError
from codeverse3d.models.retry import KeyPool
from tests.models.test_gemini import make_model, text_response


def api_error(code: int, msg: str = "boom") -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": msg, "code": code}})


def test_a_recovered_call_reports_its_attempts_and_a_failed_call_carries_its_count():
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


