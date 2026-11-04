"""AnthropicModel with a fake anthropic client (offline)."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
)
from codeverse3d.models.anthropic import (
    AnthropicModel,
)


def msg(blocks: list[Any], *, stop="end_turn", in_tok=100, out_tok=20, cache_read=0, cache_write=0):
    return NS(
        id="msg_1",
        model="claude-opus-5-5",
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


def make(script, model="claude-opus-5-5", **kw):
    fc = FakeClient(script)
    return AnthropicModel(model, client=fc, sleep=lambda s: None, **kw), fc


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
    assert "temperature" not in kw  # opus-5.5 rejects sampling params
    u = r.usage
    assert (
        u.input_tokens == 140
        and u.cached_tokens == 30
        and u.output_tokens == 20
        and u.thoughts_tokens == 0
    )
    # 100*4 + 30*0.2 + 10*4 + 20*20 + surcharge 10*1.00 (per 1M)
    assert abs(u.cost_usd - (400 + 6 + 40 + 400 + 10) / 1e6) < 1e-12


def test_structured_output_via_forced_submit_tool():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    # haiku-5.5 can still turn thinking off and force a tool (opus-5.5 / sonnet-5.5 / fable cannot)
    m, fc = make([msg([tool_use("tu_1", "submit", {"a": 1})], stop="tool_use")], model="claude-haiku-5-5")
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
