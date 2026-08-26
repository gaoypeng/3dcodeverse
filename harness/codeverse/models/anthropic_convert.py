"""ChatRequest ⇄ Anthropic Messages API dicts (messages, tools, thinking, parse).

Model-family rules (Anthropic API, 2026-08):
* 4.6+ family (opus-5, sonnet-5, fable-5, mythos, opus-4-6/7/8, sonnet-4-6):
  ``thinking={"type":"adaptive"}`` + ``output_config.effort``; ``budget_tokens``
  is rejected; sampling params are rejected on opus-5/sonnet-5/fable/4.7/4.8.
* older models (haiku-4-5, sonnet-4-5, opus-4-1 …): ``thinking={"type":
  "enabled","budget_tokens":N}`` (N < max_tokens), temperature must be left
  at default while thinking is on.
"""

from __future__ import annotations

import json
from typing import Any

from codeverse.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from codeverse.models.base import ModelError
from codeverse.models.parts import BoundedCache, image_b64
from codeverse.models.schema_utils import to_anthropic_schema

SUBMIT_TOOL = "submit"
THINKING_BUDGET: dict[str, int] = {"off": 0, "low": 1024, "medium": 4096, "high": 16000}
EFFORT: dict[str, str] = {"off": "low", "low": "low", "medium": "medium", "high": "high"}

_ADAPTIVE_MARKERS = (
    "opus-5",
    "sonnet-5",
    "fable-5",
    "mythos",
    "opus-4-6",
    "opus-4-7",
    "opus-4-8",
    "sonnet-4-6",
)
_ALWAYS_THINKING = ("fable-5", "mythos")
_NO_SAMPLING = ("opus-5", "sonnet-5", "fable-5", "mythos", "opus-4-7", "opus-4-8")


def uses_adaptive_thinking(model: str) -> bool:
    m = model.lower()
    return any(tok in m for tok in _ADAPTIVE_MARKERS)


def thinking_always_on(model: str) -> bool:
    m = model.lower()
    return any(tok in m for tok in _ALWAYS_THINKING)


def sampling_allowed(model: str) -> bool:
    m = model.lower()
    return not any(tok in m for tok in _NO_SAMPLING)


#: Assistant turns that contain tool calls must be replayed WITH their
#: thinking blocks on the next request.  ``ToolCallPart`` cannot carry them,
#: so we remember the blocks under the first tool-call id (bounded LRU).
THINKING_BLOCKS: BoundedCache[list[dict[str, Any]]] = BoundedCache(2048)


# ------------------------------------------------------------------ messages
def _image_block(p: ImagePart) -> dict[str, Any]:
    data, mime = image_b64(p)
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}


def to_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """ChatMessages → Anthropic ``messages``.  ``tool`` role → user turn of
    ``tool_result`` blocks; consecutive same-role turns are merged."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = "assistant" if msg.role == "assistant" else "user"
        blocks: list[dict[str, Any]] = []
        first_call_id: str | None = None
        for p in msg.parts:
            if isinstance(p, TextPart):
                if p.text:
                    blocks.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                if p.label:
                    blocks.append({"type": "text", "text": f"[image: {p.label}]"})
                blocks.append(_image_block(p))
            elif isinstance(p, ToolCallPart):
                first_call_id = first_call_id or p.id
                blocks.append(
                    {"type": "tool_use", "id": p.id, "name": p.name, "input": dict(p.arguments)}
                )
            elif isinstance(p, ToolResultPart):
                content: list[dict[str, Any]] = [
                    {"type": "text", "text": p.content or "(no output)"}
                ]
                for img in p.images:
                    if img.label:
                        content.append({"type": "text", "text": f"[image: {img.label}]"})
                    content.append(_image_block(img))
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": p.call_id,
                    "content": content,
                }
                if p.is_error:
                    block["is_error"] = True
                blocks.append(block)
        if not blocks:
            continue
        if role == "assistant" and first_call_id:
            blocks = list(THINKING_BLOCKS.get(first_call_id) or []) + blocks
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    if not out:
        raise ModelError("ChatRequest has no content to send")
    if out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(continue)"}]})
    return out


# --------------------------------------------------------------------- tools
def to_tool(t: ToolSpec) -> dict[str, Any]:
    schema = (
        to_anthropic_schema(t.parameters) if t.parameters else {"type": "object", "properties": {}}
    )
    return {"name": t.name, "description": t.description, "input_schema": schema}


def submit_tool(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": SUBMIT_TOOL,
        "description": "Submit the final answer.  Call exactly once with the complete JSON object.",
        "input_schema": to_anthropic_schema(schema),
    }


# ------------------------------------------------------------------- request
def build_kwargs(request: ChatRequest, model: str, *, json_mode: str = "tool") -> dict[str, Any]:
    """Build ``client.messages.create(**kwargs)`` (without ``model``/``timeout``).
    ``json_mode``: ``"tool"`` forces a ``submit`` tool; ``"output_config"`` uses
    ``output_config.format`` json_schema."""
    kw: dict[str, Any] = {
        "messages": to_messages(request.messages),
        "max_tokens": request.max_output_tokens,
    }
    system = request.system
    thinking_on = request.thinking != "off" or thinking_always_on(model)

    if uses_adaptive_thinking(model):
        if request.thinking != "off":
            kw["thinking"] = {"type": "adaptive"}
            kw["output_config"] = {"effort": EFFORT[request.thinking]}
        elif not thinking_always_on(model):
            kw["thinking"] = {"type": "disabled"}
        else:
            kw["output_config"] = {"effort": "low"}
    elif request.thinking != "off":
        budget = THINKING_BUDGET[request.thinking]
        kw["thinking"] = {"type": "enabled", "budget_tokens": budget}
        kw["max_tokens"] = max(request.max_output_tokens, budget + 2048)

    if sampling_allowed(model) and not thinking_on:
        kw["temperature"] = request.temperature

    tools = [to_tool(t) for t in request.tools] if request.tools else []
    if request.response_schema is not None:
        if json_mode == "output_config":
            kw.setdefault("output_config", {})["format"] = {
                "type": "json_schema",
                "schema": to_anthropic_schema(request.response_schema),
            }
        else:
            tools.append(submit_tool(request.response_schema))
            if not request.tools and not thinking_on:
                kw["tool_choice"] = {"type": "tool", "name": SUBMIT_TOOL}
            else:
                # forced tool_choice is incompatible with thinking; nudge instead
                system = (
                    (system + "\n\n" if system else "")
                    + f"When you have the final answer, call the `{SUBMIT_TOOL}` tool exactly once with the complete JSON object."
                )
    if tools:
        kw["tools"] = tools
    if system:
        kw["system"] = system
    return kw


# ------------------------------------------------------------------ response
def parse_content(content: list[Any]) -> tuple[str, list[ToolCallPart], Any, list[dict[str, Any]]]:
    """→ ``(text, tool_calls, submit_input, thinking_blocks)`` from response blocks."""
    texts: list[str] = []
    calls: list[ToolCallPart] = []
    submit: Any = None
    thinking: list[dict[str, Any]] = []
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(block.text)
        elif btype == "tool_use":
            args = (
                block.input
                if isinstance(block.input, dict)
                else json.loads(json.dumps(block.input))
            )
            if block.name == SUBMIT_TOOL:
                submit = args
            else:
                calls.append(ToolCallPart(id=block.id, name=block.name, arguments=dict(args)))
        elif btype in ("thinking", "redacted_thinking"):
            dump = block.model_dump() if hasattr(block, "model_dump") else dict(block)
            thinking.append(dump)
    return "".join(texts), calls, submit, thinking
