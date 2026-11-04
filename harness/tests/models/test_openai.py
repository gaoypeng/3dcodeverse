"""OpenAIModel with a fake openai client (offline)."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import httpx

from codeverse3d.contracts.chat import ChatMessage, ChatRequest
from codeverse3d.models.openai import (
    OpenAIModel,
)


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
        model="gpt-6.1-sol",
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


def make(script, model="gpt-6.1-sol", **kw):
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
    # gpt-6.1-sol: 80*2.00 + 20*0.10 + 30*10.00 per 1M (pricing checked 2026-10-13)
    assert abs(u.cost_usd - (160 + 2 + 300) / 1e6) < 1e-12


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
