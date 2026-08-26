"""``AnthropicModel`` — Messages API backend (images, tools, JSON via ``submit``
tool, adaptive / budgeted thinking, cache-aware usage + cost, typed errors).

Retries (429 / 529 / 5xx / connection / timeouts) use ``with_retries``; the
SDK's own retry is disabled so back-off is governed here.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.contracts.common import Usage
from codeverse.models.anthropic_convert import THINKING_BLOCKS, build_kwargs, parse_content
from codeverse.models.base import ModelError
from codeverse.models.keypool import MAX_WAIT_S
from codeverse.models.parts import Stopwatch
from codeverse.models.pricing import cache_write_surcharge, estimate_cost
from codeverse.models.retry import with_retries
from codeverse.models.schema_utils import JsonParseError, parse_json_lenient, strip_control_chars

log = logging.getLogger(__name__)


def classify_exception(exc: BaseException) -> ModelError:
    """Map anthropic SDK exceptions onto ``ModelError``."""
    if isinstance(exc, ModelError):
        return exc
    import anthropic

    if isinstance(exc, anthropic.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        retry = status in (408, 409, 429, 529) or status >= 500
        return ModelError(
            f"Anthropic API error {status}: {exc.message}", retryable=retry, status=status
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return ModelError(f"Anthropic request timed out: {exc}", retryable=True, status=408)
    if isinstance(exc, anthropic.APIConnectionError):
        return ModelError(f"Anthropic connection error: {exc}", retryable=True)
    if isinstance(exc, anthropic.APIError):
        return ModelError(f"Anthropic API error: {exc}", retryable=False)
    return ModelError(f"Anthropic unexpected error: {type(exc).__name__}: {exc}", retryable=False)


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

    def supports_vision(self) -> bool:
        return True

    # ---------------------------------------------------------------- client
    def client(self) -> Any:
        with self._lock:
            if self._client is None:
                import anthropic

                from codeverse.config import get_settings

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

        def attempt() -> ChatResponse:
            try:
                return self._once(kwargs, request)
            except Exception as exc:  # noqa: BLE001 - classified
                raise classify_exception(exc) from exc

        def on_retry(n: int, exc: BaseException, delay: float) -> None:
            log.warning(
                "anthropic %s attempt %d/%d failed (%s); retrying in %.1fs",
                self.model,
                n,
                self.max_attempts,
                exc,
                delay,
            )

        # ChatRequest.max_wait_s is not honoured here: with_retries has no deadline, and its
        # 6 attempts x <= 5 s backoff bound one call to ~20 s of waiting plus the round-trips.
        return with_retries(
            attempt,
            is_retryable=lambda e: isinstance(e, ModelError) and e.retryable,
            attempts=self.max_attempts,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            on_retry=on_retry,
            sleep=self._sleep,
        )

    def _once(self, kwargs: dict[str, Any], request: ChatRequest) -> ChatResponse:
        client = self.client()
        with Stopwatch() as sw:
            msg = client.messages.create(model=self.model, **kwargs)
        text, calls, submit, thinking_blocks = parse_content(msg.content)
        stop = str(msg.stop_reason or "")
        if calls and thinking_blocks:
            THINKING_BLOCKS.put(calls[0].id, thinking_blocks)
        usage = self._usage(msg.usage, sw.ms, len(calls))
        if stop == "refusal":
            raise ModelError(
                f"Anthropic refused the request: {getattr(msg, 'stop_details', None)}",
                retryable=False,
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
            elif not calls:
                try:
                    parsed = parse_json_lenient(text)
                except JsonParseError as exc:
                    raise ModelError(
                        f"structured output missing (stop_reason={stop}): {exc}",
                        retryable=stop != "max_tokens",
                    ) from exc
        if not text and not calls and parsed is None:
            raise ModelError(f"Anthropic returned no content (stop_reason={stop})", retryable=True)
        raw: dict[str, Any] = {"stop_reason": stop, "id": msg.id, "model": msg.model}
        return ChatResponse(
            text=text, parsed=parsed, tool_calls=calls, finish_reason=stop, usage=usage, raw=raw
        )

    def _usage(self, u: Any, latency_ms: int, n_calls: int) -> Usage:
        cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        usage = Usage(
            backend="anthropic",
            model=self.model,
            input_tokens=int(u.input_tokens or 0) + cache_read + cache_write,
            output_tokens=int(u.output_tokens or 0),
            cached_tokens=cache_read,
            thoughts_tokens=0,  # thinking is billed inside output_tokens
            tool_calls=n_calls,
            latency_ms=latency_ms,
        )
        usage.cost_usd = estimate_cost("anthropic", self.model, usage) + cache_write_surcharge(
            "anthropic", self.model, cache_write
        )
        return usage
