"""ChatRequest ⇄ google-genai types (contents, config, tools, response parsing).

Kept separate from ``gemini.py`` (which owns clients, keys, retries) so each
module stays small and the conversions are unit-testable offline.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from google.genai import types

from codeverse.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from codeverse.contracts.common import Usage
from codeverse.models.base import ModelError
from codeverse.models.parts import BoundedCache, image_bytes
from codeverse.models.schema_utils import to_gemini_schema

#: INTERFACES.md mapping; ``off`` → 0 where the model allows it
THINKING_BUDGET: dict[str, int] = {"off": 0, "low": 1024, "medium": 4096, "high": 16384}

#: finish reasons that are worth one more attempt (transient model glitches)
RETRYABLE_FINISH = {"RECITATION", "MALFORMED_FUNCTION_CALL", "OTHER", "UNEXPECTED_TOOL_CALL"}
#: finish reasons that will not improve by retrying
FATAL_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


#: Gemini 3 function calls carry an opaque ``thought_signature`` that MUST be
#: echoed back with the call on the next turn.  ``ToolCallPart`` has no slot for
#: it, so we remember signatures by our synthetic call id (bounded LRU).
SIGNATURES: BoundedCache[bytes] = BoundedCache(4096)
_call_counter = [0]
_call_lock = threading.Lock()


def _next_call_id() -> str:
    with _call_lock:
        _call_counter[0] += 1
        return f"call_{_call_counter[0]:06d}"


# ------------------------------------------------------------------ contents
def to_contents(messages: list[ChatMessage]) -> list[types.Content]:
    """ChatMessages → Gemini ``Content`` list.  ``tool`` messages become a user
    turn of ``function_response`` parts (images attached as extra parts)."""
    out: list[types.Content] = []
    for msg in messages:
        role = "model" if msg.role == "assistant" else "user"
        parts: list[types.Part] = []
        for p in msg.parts:
            if isinstance(p, TextPart):
                if p.text:
                    parts.append(types.Part.from_text(text=p.text))
            elif isinstance(p, ImagePart):
                raw, mime = image_bytes(p)
                if p.label:
                    parts.append(types.Part.from_text(text=f"[image: {p.label}]"))
                parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
            elif isinstance(p, ToolCallPart):
                part = types.Part(
                    function_call=types.FunctionCall(name=p.name, args=dict(p.arguments))
                )
                sig = SIGNATURES.get(p.id)
                if sig:
                    part.thought_signature = sig
                parts.append(part)
            elif isinstance(p, ToolResultPart):
                parts.append(_function_response_part(p))
                for img in p.images:
                    raw, mime = image_bytes(img)
                    if img.label:
                        parts.append(types.Part.from_text(text=f"[image: {img.label}]"))
                    parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
        if not parts:
            continue
        # merge consecutive same-role turns (Gemini wants strict alternation)
        if out and out[-1].role == role:
            out[-1].parts.extend(parts)  # type: ignore[union-attr]
        else:
            out.append(types.Content(role=role, parts=parts))
    if not out:
        raise ModelError("ChatRequest has no content to send")
    return out


def _function_response_part(p: ToolResultPart) -> types.Part:
    payload: dict[str, Any]
    try:
        parsed = json.loads(p.content)
        payload = parsed if isinstance(parsed, dict) else {"result": parsed}
    except (json.JSONDecodeError, TypeError):
        payload = {"result": p.content}
    if p.is_error:
        payload = {"error": payload.get("result", payload)} if "error" not in payload else payload
    return types.Part(function_response=types.FunctionResponse(name=p.name, response=payload))


# -------------------------------------------------------------------- config
def to_tools(tools: list[ToolSpec]) -> list[types.Tool]:
    decls = [
        types.FunctionDeclaration(
            name=t.name,
            description=t.description,
            parameters=to_gemini_schema(t.parameters) if t.parameters else None,
        )
        for t in tools
    ]
    return [types.Tool(function_declarations=decls)]


def build_config(
    request: ChatRequest,
    *,
    timeout_ms: int,
    use_thinking: bool = True,
    warnings: list[str] | None = None,
) -> types.GenerateContentConfig:
    """Map a ChatRequest onto ``GenerateContentConfig``.  Function calling and
    JSON mode are mutually exclusive on Gemini: with tools, the schema is
    dropped (the text is still parsed leniently) and a warning is recorded."""
    cfg: dict[str, Any] = {
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
        "http_options": types.HttpOptions(timeout=timeout_ms),
    }
    if request.system:
        cfg["system_instruction"] = request.system
    if use_thinking:
        cfg["thinking_config"] = types.ThinkingConfig(
            thinking_budget=THINKING_BUDGET[request.thinking], include_thoughts=False
        )
    if request.tools:
        cfg["tools"] = to_tools(request.tools)
        cfg["tool_config"] = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        )
        cfg["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        if request.response_schema is not None and warnings is not None:
            warnings.append(
                "response_schema ignored: Gemini cannot combine function calling with JSON mode"
            )
    elif request.response_schema is not None:
        cfg["response_mime_type"] = "application/json"
        cfg["response_schema"] = to_gemini_schema(request.response_schema)
    return types.GenerateContentConfig(**cfg)


# ------------------------------------------------------------------ response
def parse_usage(resp: types.GenerateContentResponse, model: str) -> Usage:
    um = resp.usage_metadata
    if um is None:
        return Usage(backend="gemini", model=model)
    return Usage(
        backend="gemini",
        model=resp.model_version or model,
        input_tokens=(um.prompt_token_count or 0) + (um.tool_use_prompt_token_count or 0),
        output_tokens=um.candidates_token_count or 0,
        cached_tokens=um.cached_content_token_count or 0,
        thoughts_tokens=um.thoughts_token_count or 0,
    )


def extract_candidate(resp: types.GenerateContentResponse) -> tuple[str, list[ToolCallPart], str]:
    """→ ``(text, tool_calls, finish_reason)``.  Raises ``ModelError`` on blocked
    prompts (non-retryable) or empty candidates (retryable)."""
    pf = resp.prompt_feedback
    if pf is not None and pf.block_reason:
        raise ModelError(
            f"prompt blocked by Gemini: {pf.block_reason} {pf.block_reason_message or ''}".strip(),
            retryable=False,
        )
    if not resp.candidates:
        raise ModelError("Gemini returned no candidates", retryable=True)
    cand = resp.candidates[0]
    finish = str(cand.finish_reason.value if cand.finish_reason else "") or "UNKNOWN"
    texts: list[str] = []
    calls: list[ToolCallPart] = []
    for part in cand.content.parts if cand.content and cand.content.parts else []:
        if part.function_call is not None:
            fc = part.function_call
            call_id = fc.id or _next_call_id()
            SIGNATURES.put(call_id, part.thought_signature)
            calls.append(
                ToolCallPart(id=call_id, name=fc.name or "", arguments=dict(fc.args or {}))
            )
        elif part.text and not part.thought:
            texts.append(part.text)
    text = "".join(texts)
    if not text and not calls:
        if finish in FATAL_FINISH:
            raise ModelError(
                f"Gemini produced no content (finish_reason={finish})", retryable=False
            )
        if finish == "MAX_TOKENS":
            # Gemini 3 models always think (budget 0 is accepted but ignored); a tiny
            # max_output_tokens is eaten by thoughts.  Retrying cannot help.
            thoughts = resp.usage_metadata.thoughts_token_count if resp.usage_metadata else None
            raise ModelError(
                f"Gemini produced no content: max_output_tokens exhausted by thinking "
                f"(thoughts_tokens={thoughts}); raise max_output_tokens",
                retryable=False,
            )
        raise ModelError(f"Gemini produced no content (finish_reason={finish})", retryable=True)
    return text, calls, finish
