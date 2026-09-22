"""AnthropicModel with a fake anthropic client (offline)."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest

from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ImagePart,
)
from codeverse3d.models.anthropic import (
    AnthropicModel,
    build_kwargs,
    classify_exception,
    to_messages,
)
from codeverse3d.models.base import ModelError

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def msg(blocks: list[Any], *, stop="end_turn", in_tok=100, out_tok=20, cache_read=0, cache_write=0):
    return NS(
        id="msg_1",
        model="claude-opus-5",
        content=blocks,
        stop_reason=stop,
        stop_details=None,
        usage=NS(
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


def text(t: str):
    return NS(type="text", text=t)


def tool_use(id_: str, name: str, inp: dict):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    def __init__(self, script: list[Any]):
        self.script, self.calls = script, []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def make(script, model="claude-opus-5", **kw):
    fc = FakeClient(script)
    return AnthropicModel(model, client=fc, sleep=lambda s: None, **kw), fc


def api_error(status: int, message: str = "err"):
    import anthropic

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status, request=req, json={"error": {"message": message}})
    cls = {
        429: anthropic.RateLimitError,
        400: anthropic.BadRequestError,
        500: anthropic.InternalServerError,
        529: anthropic.OverloadedError,
    }.get(status, anthropic.APIStatusError)
    return cls(message, response=resp, body=None)


def test_text_thinking_adaptive_and_usage_cost():
    m, fc = make([msg([text("hi")], cache_read=30, cache_write=10)])
    r = m.generate(
        ChatRequest(
            messages=[ChatMessage.user("x")], system="sys", thinking="medium", temperature=0.3
        )
    )
    assert r.text == "hi" and r.finish_reason == "end_turn"
    kw = fc.calls[0]
    assert (
        kw["system"] == "sys"
        and kw["thinking"] == {"type": "adaptive"}
        and kw["output_config"] == {"effort": "medium"}
    )
    assert "temperature" not in kw  # opus-5 rejects sampling params
    u = r.usage
    assert (
        u.input_tokens == 140
        and u.cached_tokens == 30
        and u.output_tokens == 20
        and u.thoughts_tokens == 0
    )
    # 100*5 + 30*0.5 + 10*5 + 20*25 + surcharge 10*1.25 (per 1M)
    assert abs(u.cost_usd - (500 + 15 + 50 + 500 + 12.5) / 1e6) < 1e-12


def test_budget_thinking_for_older_models_and_temperature_rules():
    kw = build_kwargs(
        ChatRequest(
            messages=[ChatMessage.user("x")],
            thinking="high",
            max_output_tokens=2000,
            temperature=0.5,
        ),
        "claude-haiku-4-5",
    )
    assert (
        kw["thinking"] == {"type": "enabled", "budget_tokens": 16000}
        and kw["max_tokens"] >= 16000 + 2048
    )
    assert "temperature" not in kw
    kw = build_kwargs(
        ChatRequest(messages=[ChatMessage.user("x")], thinking="off", temperature=0.5),
        "claude-haiku-4-5",
    )
    assert "thinking" not in kw and kw["temperature"] == 0.5
    kw = build_kwargs(
        ChatRequest(messages=[ChatMessage.user("x")], thinking="off"), "claude-opus-4-8"
    )
    assert kw["thinking"] == {"type": "disabled"}
    kw = build_kwargs(
        ChatRequest(messages=[ChatMessage.user("x")], thinking="off"), "claude-fable-5"
    )
    assert "thinking" not in kw and kw["output_config"] == {"effort": "low"}


def test_structured_output_via_forced_submit_tool():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    m, fc = make([msg([tool_use("tu_1", "submit", {"a": 1})], stop="tool_use")])
    r = m.generate(
        ChatRequest(messages=[ChatMessage.user("x")], response_schema=schema, thinking="off")
    )
    assert r.parsed == {"a": 1} and json.loads(r.text) == {"a": 1}
    kw = fc.calls[0]
    assert kw["tool_choice"] == {"type": "tool", "name": "submit"}
    assert (
        kw["tools"][0]["name"] == "submit"
        and kw["tools"][0]["input_schema"]["additionalProperties"] is False
    )


def test_structured_output_with_thinking_uses_auto_and_text_fallback():
    schema = {"type": "object"}
    m, fc = make([msg([text('```json\n{"b": 2}\n```')])])
    r = m.generate(
        ChatRequest(messages=[ChatMessage.user("x")], response_schema=schema, thinking="low")
    )
    assert r.parsed == {"b": 2}
    kw = fc.calls[0]
    assert "tool_choice" not in kw and "submit" in kw["system"]


def test_output_config_json_mode():
    m, fc = make([msg([text('{"c": 3}')])], json_mode="output_config")
    r = m.generate(
        ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"})
    )
    assert r.parsed == {"c": 3}
    assert fc.calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_images_base64_and_message_merging():
    msgs = to_messages(
        [
            ChatMessage.user(
                "a", images=[ImagePart(data_b64=PNG_B64, mime="image/png", label="L")]
            ),
            ChatMessage.user("b"),
        ]
    )
    assert len(msgs) == 1 and [b["type"] for b in msgs[0]["content"]] == [
        "text",
        "text",
        "image",
        "text",
    ]
    assert msgs[0]["content"][2]["source"]["media_type"] == "image/png"


def test_retry_on_429_529_5xx_and_not_on_400():
    m, fc = make([api_error(429), api_error(529), api_error(500), msg([text("ok")])])
    assert (
        m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "ok"
        and len(fc.calls) == 4
    )
    m, fc = make([api_error(400, "bad"), msg([text("never")])])
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert not ei.value.retryable and ei.value.status == 400 and len(fc.calls) == 1


def test_refusal_and_empty():
    m, _ = make([msg([], stop="refusal")])
    with pytest.raises(ModelError) as ei:
        m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert not ei.value.retryable
    m, fc = make([msg([], stop="end_turn"), msg([text("ok")])])
    assert m.generate(ChatRequest(messages=[ChatMessage.user("x")])).text == "ok"


def test_classify_connection_errors():
    import anthropic

    req = httpx.Request("POST", "https://api.anthropic.com")
    assert classify_exception(anthropic.APITimeoutError(request=req)).retryable
    assert classify_exception(anthropic.APIConnectionError(request=req)).retryable
    assert not classify_exception(RuntimeError("x")).retryable


def test_missing_key_is_loud(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from codeverse3d.config import get_settings

    get_settings.cache_clear()
    m = AnthropicModel("claude-opus-5")
    if not get_settings().anthropic_api_key:
        with pytest.raises(ModelError):
            m.client()
    get_settings.cache_clear()


def test_assistant_first_gets_user_prefix():
    msgs = to_messages([ChatMessage.assistant("hi")])
    assert msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant"


def test_a_failed_reply_carries_what_it_was_billed():
    """Three raise sites built a real Usage from ``msg.usage`` and threw it away, so a
    refusal / bad-JSON / empty reply looked FREE to the ledger and to the key pool."""
    for script, req in (
        ([msg([], stop="refusal")], ChatRequest(messages=[ChatMessage.user("x")])),
        (
            [msg([text("not json")], stop="max_tokens")],
            ChatRequest(messages=[ChatMessage.user("x")], response_schema={"type": "object"}),
        ),
        ([msg([], stop="end_turn")] * 6, ChatRequest(messages=[ChatMessage.user("x")])),
    ):
        m, _ = make(script)
        with pytest.raises(ModelError) as e:
            m.generate(req)
        assert e.value.usage.input_tokens == 100 and e.value.usage.output_tokens == 20


def test_each_attempt_gets_what_is_left_of_the_call_budget():
    """``max_wait_s`` is the whole call's deadline, and ``with_retries`` only checks it
    BETWEEN attempts — so the attempt itself must carry it.  The client is built once with
    a fixed 600 s timeout, so a judge with 20 s of budget left used to hold a socket for
    600 s.  Floor: a long completion (the 930 s plan) keeps the full client timeout."""
    m, fc = make([msg([text("hi")])], timeout_s=600.0)
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=25.0))
    assert 20.0 <= fc.calls[0]["timeout"] <= 25.0

    m, fc = make([msg([text("hi")])], timeout_s=600.0)
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=5.0))
    assert fc.calls[0]["timeout"] == 20.0, "a near-dead budget still buys ONE real attempt"

    m, fc = make([msg([text("hi")])], timeout_s=600.0)
    m.generate(ChatRequest(messages=[ChatMessage.user("x")], max_wait_s=930.0))
    assert fc.calls[0]["timeout"] == 600.0, "a long plan is bounded by the client, not clipped"

    m, fc = make([msg([text("hi")])], timeout_s=600.0)
    m.generate(ChatRequest(messages=[ChatMessage.user("x")]))
    assert fc.calls[0]["timeout"] == 600.0
