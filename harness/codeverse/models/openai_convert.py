"""ChatRequest ⇄ OpenAI Chat Completions dicts (messages, tools, response_format).

Chat Completions (not Responses) was chosen because every OpenAI-compatible
endpoint (``OPENAI_BASE_URL``: vLLM, OpenRouter, Ollama, …) speaks it, while
the Responses API is OpenAI-only.
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
from codeverse.models.parts import image_b64
from codeverse.models.schema_utils import inline_refs, to_openai_strict_schema

SCHEMA_NAME = "response"


# ------------------------------------------------------------- model families
def is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5"))


def reasoning_effort(model: str, thinking: str) -> str | None:
    """Map ``thinking`` onto ``reasoning_effort`` (None for non-reasoning models)."""
    if not is_reasoning_model(model):
        return None
    m = model.lower()
    if thinking == "off":
        if m.startswith("gpt-5.") and not m.startswith("gpt-5.0"):
            return "none"  # gpt-5.1+ accept "none"
        if m.startswith("gpt-5"):
            return "minimal"
        return "low"  # o-series has no off switch
    return thinking


# ------------------------------------------------------------------ messages
def _image_block(p: ImagePart) -> dict[str, Any]:
    data, mime = image_b64(p)
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{data}", "detail": "auto"},
    }


def to_messages(messages: list[ChatMessage], system: str) -> list[dict[str, Any]]:
    """ChatMessages → Chat Completions messages.  Tool results become ``role:
    tool`` messages; images inside tool results (unsupported there) are sent as
    a follow-up user message."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        if msg.role == "assistant":
            out.append(_assistant_message(msg))
            continue
        trailing_images: list[dict[str, Any]] = []
        content: list[dict[str, Any]] = []
        for p in msg.parts:
            if isinstance(p, TextPart):
                if p.text:
                    content.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                if p.label:
                    content.append({"type": "text", "text": f"[image: {p.label}]"})
                content.append(_image_block(p))
            elif isinstance(p, ToolResultPart):
                if content:
                    out.append({"role": "user", "content": content})
                    content = []
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": p.call_id,
                        "content": p.content or "(no output)",
                    }
                )
                for img in p.images:
                    if img.label:
                        trailing_images.append(
                            {"type": "text", "text": f"[image from {p.name}: {img.label}]"}
                        )
                    trailing_images.append(_image_block(img))
            elif isinstance(p, ToolCallPart):
                raise ModelError("ToolCallPart is only valid in assistant messages")
        if content:
            out.append({"role": "user", "content": content})
        if trailing_images:
            out.append({"role": "user", "content": trailing_images})
    if not out or all(m["role"] == "system" for m in out):
        raise ModelError("ChatRequest has no content to send")
    return out


def _assistant_message(msg: ChatMessage) -> dict[str, Any]:
    text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
    calls = [
        {
            "id": p.id,
            "type": "function",
            "function": {"name": p.name, "arguments": json.dumps(p.arguments)},
        }
        for p in msg.parts
        if isinstance(p, ToolCallPart)
    ]
    m: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        m["tool_calls"] = calls
    return m


# --------------------------------------------------------------------- tools
def to_tool(t: ToolSpec) -> dict[str, Any]:
    params = inline_refs(t.parameters) if t.parameters else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {"name": t.name, "description": t.description, "parameters": params},
    }


def response_format(schema: dict[str, Any], *, strict: bool) -> dict[str, Any]:
    body = to_openai_strict_schema(schema) if strict else inline_refs(schema)
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "schema": body, "strict": strict},
    }


# ------------------------------------------------------------------- request
def build_kwargs(request: ChatRequest, model: str, *, strict_schema: bool) -> dict[str, Any]:
    """``client.chat.completions.create(**kwargs)`` minus ``model``."""
    kw: dict[str, Any] = {
        "messages": to_messages(request.messages, request.system),
        "max_completion_tokens": request.max_output_tokens,
    }
    effort = reasoning_effort(model, request.thinking)
    if effort is not None:
        kw["reasoning_effort"] = effort
    else:
        kw["temperature"] = request.temperature
    if request.tools:
        kw["tools"] = [to_tool(t) for t in request.tools]
        kw["tool_choice"] = "auto"
    if request.response_schema is not None:
        kw["response_format"] = response_format(request.response_schema, strict=strict_schema)
    return kw


# ------------------------------------------------------------------ response
def parse_choice(choice: Any) -> tuple[str, list[ToolCallPart], str]:
    """→ ``(text, tool_calls, finish_reason)``; bad tool-call JSON → ModelError(retryable)."""
    msg = choice.message
    text = msg.content or ""
    if getattr(msg, "refusal", None):
        raise ModelError(f"OpenAI refusal: {msg.refusal}", retryable=False)
    calls: list[ToolCallPart] = []
    for tc in msg.tool_calls or []:
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        try:
            args = json.loads(fn.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ModelError(
                f"tool call {fn.name} has invalid JSON arguments: {exc}", retryable=True
            ) from exc
        if not isinstance(args, dict):
            args = {"value": args}
        calls.append(ToolCallPart(id=tc.id, name=fn.name, arguments=args))
    return text, calls, str(choice.finish_reason or "")
