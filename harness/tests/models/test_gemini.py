"""GeminiModel with a fake google-genai client (offline)."""

from __future__ import annotations

from typing import Any

import pytest
from google.genai import errors as genai_errors
from google.genai import types

from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
)
from codeverse3d.models.base import ModelError
from codeverse3d.models.gemini import (
    GeminiModel,
    classify_exception,
)
from codeverse3d.models.retry import KeyPool, failure_outcome


def text_response(
    text: str, *, finish: str = "STOP", thoughts: int = 3
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        model_version="gemini-3.8-flash",
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
                finish_reason=finish,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100,
            candidates_token_count=20,
            thoughts_token_count=thoughts,
            cached_content_token_count=40,
        ),
    )


class FakeModels:
    def __init__(self, script: list[Any], key: str, log: list[dict]):
        self.script, self.key, self.log = script, key, log

    def generate_content(self, *, model: str, contents, config):
        self.log.append({"key": self.key, "model": model, "contents": contents, "config": config})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def generate_content_stream(self, *, model: str, contents, config):
        yield self.generate_content(model=model, contents=contents, config=config)


class FakeClient:
    def __init__(self, script, key, log):
        self.models = FakeModels(script, key, log)


def make_model(script: list[Any], keys=("k1", "k2", "k3"), pool: KeyPool | None = None, **kw):
    """Shared script across keys (each call pops the next item); log records which key was used."""
    log: list[dict] = []
    pool = pool or KeyPool(list(keys), cooldown_s=30)
    m = GeminiModel(
        "gemini-3.8-flash",
        pool=pool,
        sleep=lambda s: None,
        client_factory=lambda key: FakeClient(script, key, log),
        **kw,
    )
    return m, log, pool


def _api_error(code: int, message: str, status: str) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": message, "status": status}})


SUSPENDED = _api_error(
    403, "PERMISSION_DENIED: Consumer 'api_key:xxx' has been suspended.", "PERMISSION_DENIED"
)
THROTTLED = genai_errors.APIError(
    429,
    {
        "error": {
            "message": "quota",
            "status": "RESOURCE_EXHAUSTED",
        }
    },
)


def test_429_rotates_keys_and_cools_the_throttled_ones():
    m, log, pool = make_model([THROTTLED, THROTTLED, text_response("third time lucky")])
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "third time lucky"
    assert [e["key"] for e in log] == ["k1", "k2", "k3"]
    st = pool.stats()
    assert st["429"] == 2 and st["ok"] == 1
    assert [pool.acquire() for _ in range(3)] == ["k3"] * 3, "k1 and k2 are cooling"


def test_text_usage_cost_and_raw():
    m, log, _ = make_model([text_response("hello")])
    r = m.generate(
        ChatRequest(
            messages=[ChatMessage.user("hi")], system="sys", thinking="medium", temperature=0.2
        )
    )
    assert r.text == "hello" and r.finish_reason == "STOP" and r.parsed is None
    u = r.usage
    assert (u.input_tokens, u.output_tokens, u.thoughts_tokens, u.cached_tokens) == (100, 20, 3, 40)
    assert u.cost_usd > 0 and u.backend == "gemini" and u.model == "gemini-3.8-flash"
    cfg = log[0]["config"]
    assert cfg.system_instruction == "sys" and cfg.temperature == 0.2
    assert cfg.thinking_config.thinking_budget == 4096
    assert r.raw["key"] == "…k1"


def test_api_key_invalid_400_is_treated_as_dead_key():
    bad = _api_error(400, "API key not valid. Please pass a valid API key.", "INVALID_ARGUMENT")
    m, log, pool = make_model([bad, text_response("ok")], keys=("k1", "k2"))
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "ok"
    assert [event["key"] for event in log] == ["k1", "k2"]
    assert [pool.acquire() for _ in range(3)] == ["k2"] * 3, "the dead key is benched"
    assert failure_outcome(classify_exception(bad)) == "dead"
    assert failure_outcome(classify_exception(SUSPENDED)) == "dead"
    assert failure_outcome(classify_exception(THROTTLED)) == "429"
    assert failure_outcome(ModelError("bad json", retryable=True)) == "skip"  # not the key's fault: no health nudge


def test_thinking_rejected_falls_back_without_thinking_config():
    err = genai_errors.APIError(
        400,
        {
            "error": {
                "message": "Thinking level is not supported for this model",
                "status": "INVALID_ARGUMENT",
            }
        },
    )
    m, log, _ = make_model([err, text_response("ok")])
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")], thinking="high"))
    assert r.text == "ok"
    assert log[0]["config"].thinking_config is not None and log[1]["config"].thinking_config is None


def test_a_charged_but_invalid_reply_carries_its_usage_on_the_error():
    """A failed structured reply preserves its billed token usage."""
    m, _log, _ = make_model([text_response("nope")] * 6)
    with pytest.raises(ModelError) as ei:
        m.generate(
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"})
        )
    # every billed attempt, not just the last: 6 replies of 100 in / 20 out
    assert ei.value.usage.input_tokens == 600 and ei.value.usage.output_tokens == 120
    assert ei.value.usage.cost_usd > 0


