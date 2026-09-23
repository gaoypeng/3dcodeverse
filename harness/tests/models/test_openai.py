"""OpenAIModel with a fake openai client (offline)."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest

from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse3d.models.base import ModelError
from codeverse3d.models.openai import (
    OpenAIModel,
    build_kwargs,
    classify_exception,
    reasoning_effort,
    to_messages,
)

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def completion(
    content: str | None,
    *,
    finish="stop",
    prompt=100,
    comp=30,
    cached=20,
    reasoning=10,
):
    return NS(
        id="cmpl_1",
        model="gpt-5.6-sol",
        choices=[
            NS(
                message=NS(content=content, refusal=None),
                finish_reason=finish,
            )
        ],
        usage=NS(
            prompt_tokens=prompt,
            completion_tokens=comp,
            prompt_tokens_details=NS(cached_tokens=cached),
            completion_tokens_details=NS(reasoning_tokens=reasoning),
        ),
    )


class FakeClient:
    def __init__(self, script: list[Any]):
        self.script, self.calls = script, []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make(script, model="gpt-5.6-sol", **kw):
    fc = FakeClient(script)
    return OpenAIModel(model, client=fc, sleep=lambda s: None, **kw), fc


def api_error(status: int, message: str = "err"):
    import openai

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": {"message": message}})
    cls = {
        429: openai.RateLimitError,
        400: openai.BadRequestError,
        500: openai.InternalServerError,
    }.get(status, openai.APIStatusError)
    return cls(message, response=resp, body={"error": {"message": message}})


def test_text_reasoning_effort_usage_cost():
    m, fc = make([completion("hello")])
    r = m.generate(
        ChatRequest(
            messages=[ChatMessage.user("x")], system="sys", thinking="high", temperature=0.3
        )
    )
    assert r.text == "hello" and r.finish_reason == "stop"
    kw = fc.calls[0]
    assert kw["messages"][0] == {"role": "system", "content": "sys"}
    assert (
        kw["reasoning_effort"] == "high"
        and "temperature" not in kw
        and kw["max_completion_tokens"] == ChatRequest.model_fields["max_output_tokens"].default
    )
    u = r.usage
    assert (u.input_tokens, u.cached_tokens, u.output_tokens, u.thoughts_tokens) == (
        100,
        20,
        20,
        10,
    )
    # gpt-5.6-sol: 80*4.00 + 20*0.40 + 30*20.00 per 1M (pricing checked 2026-08-23)
    assert abs(u.cost_usd - (320 + 8 + 600) / 1e6) < 1e-12


def test_reasoning_effort_mapping_and_temperature_for_non_reasoning():
    assert reasoning_effort("gpt-5", "off") == "minimal"
    assert reasoning_effort("gpt-5.5", "off") == "none"
    assert reasoning_effort("o3", "off") == "low"
    assert reasoning_effort("gpt-5.6-sol", "medium") == "medium"
    assert reasoning_effort("gpt-4.1", "high") is None
    kw = build_kwargs(
        ChatRequest(messages=[ChatMessage.user("x")], temperature=0.1),
        "gpt-4.1",
        strict_schema=False,
    )
    assert kw["temperature"] == 0.1 and "reasoning_effort" not in kw


def test_json_schema_strict_then_fallback_to_non_strict():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "string", "default": "x"}},
        "required": ["a"],
    }
    m, fc = make(
        [
            api_error(400, "Invalid schema for response_format 'response': strict mode ..."),
            completion('{"a": 1}'),
        ]
    )
    r = m.generate(ChatRequest(messages=[ChatMessage.user("x")], response_schema=schema))
    assert r.parsed == {"a": 1} and len(fc.calls) == 2
    first, second = (
        fc.calls[0]["response_format"]["json_schema"],
        fc.calls[1]["response_format"]["json_schema"],
    )
    assert (
        first["strict"] is True
        and first["schema"]["additionalProperties"] is False
        and set(first["schema"]["required"]) == {"a", "b"}
    )
    assert second["strict"] is False and second["schema"]["required"] == ["a"]
    # sticky: later calls go straight to strict=false
    fc.script.append(completion('{"a": 2}'))
    m.generate(ChatRequest(messages=[ChatMessage.user("y")], response_schema=schema))
    assert fc.calls[2]["response_format"]["json_schema"]["strict"] is False


def test_bad_json_retried():
    m, fc = make([completion("garbage"), completion('{"ok": 1}')])
    r = m.generate(
        ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"})
    )
    assert r.parsed == {"ok": 1} and len(fc.calls) == 2


def test_images_data_urls():
    msgs = to_messages(
        [
            ChatMessage.user(
                "look", images=[ImagePart(data_b64=PNG_B64, mime="image/png", label="v")]
            )
        ],
        "",
    )
    c = msgs[0]["content"]
    assert [b["type"] for b in c] == ["text", "text", "image_url"]




def test_empty_and_classify():
    import openai

    m, fc = make([completion(None), completion("ok")])
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "ok"
    req = httpx.Request("POST", "https://api.openai.com")
    assert classify_exception(openai.APITimeoutError(request=req)).retryable
    assert not classify_exception(KeyError("k")).retryable


def test_base_url_from_settings(monkeypatch):
    from codeverse3d.config import get_settings

    monkeypatch.setenv("C3D_OPENAI_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        m = OpenAIModel("local-model")
        c = m.client()
        assert str(c.base_url).startswith("http://localhost:8000/v1")
    finally:
        get_settings.cache_clear()


def test_a_failed_reply_carries_what_it_was_billed():
    """A bad-JSON or empty completion is billed on the error, reasoning split out (D84)."""
    for script, req in (
        (
            [completion("not json", finish="length")],
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"}),
        ),
        (
            [completion(None, finish="content_filter")],
            ChatRequest(messages=[ChatMessage.user("x")]),
        ),
    ):
        m, _ = make(script)
        with pytest.raises(ModelError) as e:
            m.generate(req)
        u = e.value.usage
        assert u.input_tokens == 100 and u.output_tokens == 20 and u.thoughts_tokens == 10


