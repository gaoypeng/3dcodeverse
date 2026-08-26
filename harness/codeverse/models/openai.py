"""``OpenAIModel`` — Chat Completions backend (images as data URLs, function
tools, ``json_schema`` response_format with strict→non-strict fallback,
``reasoning_effort`` for o*/gpt-5* models, usage/cost, ``OPENAI_BASE_URL``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.contracts.common import Usage
from codeverse.models.base import ModelError
from codeverse.models.keypool import MAX_WAIT_S
from codeverse.models.openai_convert import build_kwargs, parse_choice
from codeverse.models.parts import Stopwatch
from codeverse.models.pricing import estimate_cost
from codeverse.models.retry import with_retries
from codeverse.models.schema_utils import JsonParseError, parse_json_lenient

log = logging.getLogger(__name__)


def classify_exception(exc: BaseException) -> ModelError:
    """Map openai SDK exceptions onto ``ModelError``."""
    if isinstance(exc, ModelError):
        return exc
    import openai

    if isinstance(exc, openai.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        retry = status in (408, 409, 429) or status >= 500
        return ModelError(
            f"OpenAI API error {status}: {exc.message}", retryable=retry, status=status
        )
    if isinstance(exc, openai.APITimeoutError):
        return ModelError(f"OpenAI request timed out: {exc}", retryable=True, status=408)
    if isinstance(exc, openai.APIConnectionError):
        return ModelError(f"OpenAI connection error: {exc}", retryable=True)
    if isinstance(exc, openai.APIError):
        return ModelError(f"OpenAI API error: {exc}", retryable=False)
    return ModelError(f"OpenAI unexpected error: {type(exc).__name__}: {exc}", retryable=False)


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

    def supports_vision(self) -> bool:
        return True

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
        def attempt() -> ChatResponse:
            strict = self._strict_ok and request.response_schema is not None
            kwargs = build_kwargs(request, self.model, strict_schema=strict)
            try:
                return self._once(kwargs, request)
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
                        return self._once(kwargs, request)
                    except Exception as exc2:  # noqa: BLE001
                        raise classify_exception(exc2) from exc2
                raise err from exc

        def on_retry(n: int, exc: BaseException, delay: float) -> None:
            log.warning(
                "openai %s attempt %d/%d failed (%s); retrying in %.1fs",
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
            completion = client.chat.completions.create(model=self.model, **kwargs)
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
                    retryable=finish != "length",
                ) from exc
        if not text and not calls:
            raise ModelError(
                f"OpenAI returned no content (finish_reason={finish})",
                retryable=finish != "content_filter",
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
