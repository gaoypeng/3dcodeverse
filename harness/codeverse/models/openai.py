"""ChatRequest ⇄ OpenAI Chat Completions dicts (messages, tools, response_format).

Chat Completions (not Responses) was chosen because every OpenAI-compatible
endpoint (``OPENAI_BASE_URL``: vLLM, OpenRouter, Ollama, …) speaks it, while
the Responses API is OpenAI-only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from codeverse.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from codeverse.contracts.common import Usage
from codeverse.models.base import ModelError
from codeverse.models.parts import (
    Stopwatch,
    attempt_timeout_s,
    classify_sdk_exception,
    image_b64,
    retry_budget_s,
    with_logged_retries,
)
from codeverse.models.pricing import estimate_cost
from codeverse.models.retry import MAX_WAIT_S, cause_for
from codeverse.models.schema_utils import (
    JsonParseError,
    inline_refs,
    parse_json_lenient,
    to_openai_strict_schema,
)

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


# ===================================================================== openai
log = logging.getLogger(__name__)


def classify_exception(exc: BaseException) -> ModelError:
    """Map openai SDK exceptions onto ``ModelError`` (the shared ladder in parts.py)."""
    import openai

    return classify_sdk_exception(exc, openai, "OpenAI")


def _is_schema_rejection(err: ModelError) -> bool:
    s = str(err).lower()
    return err.status == 400 and ("schema" in s or "response_format" in s or "strict" in s)


class OpenAIModel:
    """ChatModel for ``openai:<model>`` (and OpenAI-compatible endpoints)."""

    provider = "openai"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 600.0,
        max_attempts: int = 6,
        base_delay: float = 1.0,
        max_delay: float = MAX_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._client = client
        self._api_key = api_key
        self._base_url = base_url
        self._strict_ok = True  # flipped when the endpoint rejects strict schemas
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        return f"openai:{self.model}"

    # ---------------------------------------------------------------- client
    def client(self) -> Any:
        with self._lock:
            if self._client is None:
                import openai

                from codeverse.config import get_settings

                s = get_settings()
                key = self._api_key or s.openai_api_key
                base_url = self._base_url or s.openai_base_url or None
                if not key and not base_url:
                    raise ModelError("OPENAI_API_KEY is not configured")
                self._client = openai.OpenAI(
                    api_key=key or "sk-local",
                    base_url=base_url,
                    max_retries=0,
                    timeout=self.timeout_s,
                )
            return self._client

    # -------------------------------------------------------------- generate
    def generate(self, request: ChatRequest) -> ChatResponse:
        deadline = time.monotonic() + retry_budget_s(request.max_wait_s)

        def attempt() -> ChatResponse:
            strict = self._strict_ok and request.response_schema is not None
            kwargs = build_kwargs(request, self.model, strict_schema=strict)
            try:
                return self._once(kwargs, request, deadline)
            except Exception as exc:  # noqa: BLE001 - classified
                err = classify_exception(exc)
                if strict and _is_schema_rejection(err):
                    log.warning(
                        "openai %s rejected strict json_schema (%s); falling back to strict=false",
                        self.model,
                        err,
                    )
                    with self._lock:
                        self._strict_ok = False
                    kwargs = build_kwargs(request, self.model, strict_schema=False)
                    try:
                        return self._once(kwargs, request, deadline)
                    except Exception as exc2:  # noqa: BLE001
                        err2 = classify_exception(exc2)
                        raise err2 from cause_for(err2, exc2)
                raise err from cause_for(err, exc)

        return with_logged_retries(attempt, label="openai", model=self.model,
                                   attempts=self.max_attempts, base_delay=self.base_delay,
                                   max_delay=self.max_delay, sleep=self._sleep, log=log,
                                   max_wait_s=request.max_wait_s)

    def _once(self, kwargs: dict[str, Any], request: ChatRequest, deadline: float) -> ChatResponse:
        client = self.client()
        with Stopwatch() as sw:
            # the client is built once with a fixed timeout; the deadline is per call
            completion = client.chat.completions.create(
                model=self.model, timeout=attempt_timeout_s(deadline, self.timeout_s), **kwargs)
        if not completion.choices:
            raise ModelError("OpenAI returned no choices", retryable=True)
        text, calls, finish = parse_choice(completion.choices[0])
        usage = self._usage(completion.usage, sw.ms, len(calls))
        parsed: Any = None
        if request.response_schema is not None and not calls:
            try:
                parsed = parse_json_lenient(text)
            except JsonParseError as exc:
                raise ModelError(
                    f"structured output is not valid JSON (finish_reason={finish}): {exc}",
                    retryable=finish != "length", usage=usage,  # billed like a good reply
                ) from exc
        if not text and not calls:
            raise ModelError(
                f"OpenAI returned no content (finish_reason={finish})",
                retryable=finish != "content_filter", usage=usage,
            )
        raw: dict[str, Any] = {
            "finish_reason": finish,
            "id": completion.id,
            "model": completion.model,
        }
        return ChatResponse(
            text=text, parsed=parsed, tool_calls=calls, finish_reason=finish, usage=usage, raw=raw
        )

    def _usage(self, u: Any, latency_ms: int, n_calls: int) -> Usage:
        if u is None:
            usage = Usage(
                backend="openai", model=self.model, tool_calls=n_calls, latency_ms=latency_ms
            )
        else:
            ptd = getattr(u, "prompt_tokens_details", None)
            ctd = getattr(u, "completion_tokens_details", None)
            cached = int(getattr(ptd, "cached_tokens", 0) or 0) if ptd else 0
            reasoning = int(getattr(ctd, "reasoning_tokens", 0) or 0) if ctd else 0
            completion = int(u.completion_tokens or 0)
            usage = Usage(
                backend="openai",
                model=self.model,
                input_tokens=int(u.prompt_tokens or 0),
                # completion_tokens already includes reasoning; split so the sum stays exact
                output_tokens=max(0, completion - reasoning),
                cached_tokens=cached,
                thoughts_tokens=reasoning,
                tool_calls=n_calls,
                latency_ms=latency_ms,
            )
        usage.cost_usd = estimate_cost("openai", self.model, usage)
        return usage
