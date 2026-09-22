"""ChatRequest ⇄ Anthropic Messages API dicts (messages, submit tool, thinking, parse).

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
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    TextPart,
)
from codeverse3d.contracts.common import Usage
from codeverse3d.models.base import ModelError
from codeverse3d.models.parts import (
    Stopwatch,
    attempt_timeout_s,
    classify_sdk_exception,
    image_b64,
    retry_budget_s,
    with_logged_retries,
)
from codeverse3d.models.pricing import cache_write_surcharge, estimate_cost
from codeverse3d.models.retry import MAX_WAIT_S, cause_for
from codeverse3d.models.schema_utils import (
    JsonParseError,
    parse_json_lenient,
    strip_control_chars,
    to_anthropic_schema,
)

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


# ------------------------------------------------------------------ messages
def _image_block(p: ImagePart) -> dict[str, Any]:
    data, mime = image_b64(p)
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}


def to_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """ChatMessages → Anthropic ``messages``; consecutive same-role turns are merged."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = "assistant" if msg.role == "assistant" else "user"
        blocks: list[dict[str, Any]] = []
        for p in msg.parts:
            if isinstance(p, TextPart):
                if p.text:
                    blocks.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                if p.label:
                    blocks.append({"type": "text", "text": f"[image: {p.label}]"})
                blocks.append(_image_block(p))
        if not blocks:
            continue
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

    if request.response_schema is not None:
        if json_mode == "output_config":
            kw.setdefault("output_config", {})["format"] = {
                "type": "json_schema",
                "schema": to_anthropic_schema(request.response_schema),
            }
        else:
            kw["tools"] = [submit_tool(request.response_schema)]
            if not thinking_on:
                kw["tool_choice"] = {"type": "tool", "name": SUBMIT_TOOL}
            else:
                # forced tool_choice is incompatible with thinking; nudge instead
                system = (
                    (system + "\n\n" if system else "")
                    + f"When you have the final answer, call the `{SUBMIT_TOOL}` tool exactly once with the complete JSON object."
                )
    if system:
        kw["system"] = system
    return kw


# ------------------------------------------------------------------ response
def parse_content(content: list[Any]) -> tuple[str, Any]:
    """→ ``(text, submit_input)`` from response blocks."""
    texts: list[str] = []
    submit: Any = None
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(block.text)
        elif btype == "tool_use" and block.name == SUBMIT_TOOL:
            submit = (
                block.input
                if isinstance(block.input, dict)
                else json.loads(json.dumps(block.input))
            )
    return "".join(texts), submit


# ===================================================================== anthropic
log = logging.getLogger(__name__)


def classify_exception(exc: BaseException) -> ModelError:
    """Map anthropic SDK exceptions onto ``ModelError`` (the shared ladder in parts.py)."""
    import anthropic

    return classify_sdk_exception(exc, anthropic, "Anthropic", extra_retry=frozenset({529}))


class AnthropicModel:
    """ChatModel for ``anthropic:<model>``."""

    provider = "anthropic"

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 600.0,
        max_attempts: int = 6,
        base_delay: float = 1.0,
        max_delay: float = MAX_WAIT_S,
        json_mode: str = "tool",
        sleep: Callable[[float], None] = time.sleep,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.json_mode = json_mode
        self._sleep = sleep
        self._client = client
        self._api_key = api_key
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        return f"anthropic:{self.model}"

    # ---------------------------------------------------------------- client
    def client(self) -> Any:
        with self._lock:
            if self._client is None:
                import anthropic

                from codeverse3d.config import get_settings

                key = self._api_key or get_settings().anthropic_api_key
                if not key:
                    raise ModelError("ANTHROPIC_API_KEY is not configured")
                self._client = anthropic.Anthropic(
                    api_key=key, max_retries=0, timeout=self.timeout_s
                )
            return self._client

    # -------------------------------------------------------------- generate
    def generate(self, request: ChatRequest) -> ChatResponse:
        kwargs = build_kwargs(request, self.model, json_mode=self.json_mode)
        deadline = time.monotonic() + retry_budget_s(request.max_wait_s)

        def attempt() -> ChatResponse:
            try:
                return self._once(kwargs, request, deadline)
            except Exception as exc:  # noqa: BLE001 - classified
                err = classify_exception(exc)
                raise err from cause_for(err, exc)

        return with_logged_retries(attempt, label="anthropic", model=self.model,
                                   attempts=self.max_attempts, base_delay=self.base_delay,
                                   max_delay=self.max_delay, sleep=self._sleep, log=log,
                                   max_wait_s=request.max_wait_s)

    def _once(self, kwargs: dict[str, Any], request: ChatRequest, deadline: float) -> ChatResponse:
        client = self.client()
        with Stopwatch() as sw:
            # the client is built once with a fixed timeout; the deadline is per call
            msg = client.messages.create(
                model=self.model, timeout=attempt_timeout_s(deadline, self.timeout_s), **kwargs)
        text, submit = parse_content(msg.content)
        stop = str(msg.stop_reason or "")
        usage = self._usage(msg.usage, sw.ms)
        if stop == "refusal":
            raise ModelError(
                f"Anthropic refused the request: {getattr(msg, 'stop_details', None)}",
                retryable=False, usage=usage,  # a refusal is billed like any other reply
            )
        parsed: Any = None
        if request.response_schema is not None:
            if submit is not None:
                # The SDK hands back tool input already parsed, so it never passes through
                # parse_json_lenient -> strip_control_chars the way the gemini/openai text
                # paths do.  A model NUL in a `submit` field would otherwise survive into
                # plan.json and detonate stages later at subprocess.Popen ("embedded null
                # byte"): the identical failure strip_control_chars was written to end, and
                # sanitising at ingestion is the only place that covers every model.
                parsed = strip_control_chars(submit)
                if not text:
                    text = json.dumps(parsed)
            else:
                try:
                    parsed = parse_json_lenient(text)
                except JsonParseError as exc:
                    raise ModelError(
                        f"structured output missing (stop_reason={stop}): {exc}",
                        retryable=stop != "max_tokens", usage=usage,
                    ) from exc
        if not text and parsed is None:
            raise ModelError(f"Anthropic returned no content (stop_reason={stop})",
                             retryable=True, usage=usage)
        raw: dict[str, Any] = {"stop_reason": stop, "id": msg.id, "model": msg.model}
        return ChatResponse(text=text, parsed=parsed, finish_reason=stop, usage=usage, raw=raw)

    def _usage(self, u: Any, latency_ms: int) -> Usage:
        cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        usage = Usage(
            backend="anthropic",
            model=self.model,
            input_tokens=int(u.input_tokens or 0) + cache_read + cache_write,
            output_tokens=int(u.output_tokens or 0),
            cached_tokens=cache_read,
            thoughts_tokens=0,  # thinking is billed inside output_tokens
            latency_ms=latency_ms,
        )
        usage.cost_usd = estimate_cost("anthropic", self.model, usage) + cache_write_surcharge(
            "anthropic", self.model, cache_write
        )
        return usage
