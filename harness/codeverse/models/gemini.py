"""``GeminiModel`` — google-genai backend with key rotation + retries.

* one ``genai.Client`` per API key (cached, thread-safe),
* ``KeyPool`` shared by every GeminiModel built on the same key list,
* 429 → rotate to a fresh key (short pause; free while an untried key remains),
  then up to ``max_attempts`` tries with exponential backoff + jitter for 5xx /
  timeouts / empty candidates / 429s once every key has been tried,
* key-scoped auth failures (401/403, ``API_KEY_INVALID`` …) → rotate for free and
  bench the key in the pool (``dead``) once a sibling key proves the request is
  fine; only when every key fails the same way is the error raised,
* ``thinking`` → ``ThinkingConfig(thinking_budget=…)``; models that reject the
  field get one retry without it and a warning in ``response.raw["warnings"]``.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from codeverse.contracts.chat import ChatRequest, ChatResponse
from codeverse.models.base import ModelError
from codeverse.models.gemini_convert import (
    FATAL_FINISH,
    RETRYABLE_FINISH,
    build_config,
    extract_candidate,
    parse_usage,
    to_contents,
)
from codeverse.models.keypool import MAX_WAIT_S, KeyPool, KeyPoolExhausted, Outcome
from codeverse.models.parts import Stopwatch
from codeverse.models.pricing import estimate_cost
from codeverse.models.retry import rotate_with_retries
from codeverse.models.schema_utils import JsonParseError, parse_json_lenient
from codeverse.models.storm import StormGate
from codeverse.models.storm import storm_gate as storm_gate_for
from codeverse.models.tokens import request_tokens

log = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")

_pools: dict[tuple[str, ...], KeyPool] = {}
_clients: dict[tuple[str, int], genai.Client] = {}
_registry_lock = threading.Lock()


def shared_pool(
    keys: list[str],
    *,
    rpm_per_key: int | None = None,
    tpm_per_key: int | None = None,
    max_in_flight: int | None = None,
) -> KeyPool:
    """One ``KeyPool`` per distinct (key list, quota) so limiters are process-wide.

    Unset arguments come from ``Settings.rate`` — the owner's real per-key quota
    (``docs/COST.md`` Part III), which is what makes the pool TPM-aware: a
    200 k-token judge verdict reserves 100x what a caption does."""
    rate = _rate()
    rpm = rate.rpm_per_key if rpm_per_key is None else rpm_per_key
    tpm = rate.tpm_per_key if tpm_per_key is None else tpm_per_key
    cap = rate.max_in_flight if max_in_flight is None else max_in_flight
    sig = (*keys, f"|{rpm}|{tpm}|{cap}")
    with _registry_lock:
        pool = _pools.get(sig)
        if pool is None:
            pool = KeyPool(keys, rpm_per_key=rpm, tpm_per_key=tpm or None, max_in_flight=cap or 0)
            _pools[sig] = pool
        return pool


def _rate() -> Any:
    from codeverse.config import Rate, get_settings

    try:
        return get_settings().rate
    except Exception:  # pragma: no cover - settings must never break a model call
        return Rate()


def _default_timeout_s() -> float:
    from codeverse.config import get_settings

    try:
        return float(get_settings().model_timeout_s)
    except Exception:  # pragma: no cover - settings must never break a model call
        return 300.0


def _default_keys() -> list[str]:
    from codeverse.config import get_settings

    return list(get_settings().gemini_api_keys)


def classify_exception(exc: BaseException) -> ModelError:
    """Map provider / transport exceptions onto ``ModelError``."""
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, genai_errors.APIError):
        code = int(exc.code or 0)
        msg = f"Gemini API error {code}: {exc.message}"
        if code == 429 or code == 408 or code >= 500:
            return ModelError(msg, retryable=True, status=code)
        return ModelError(msg, retryable=False, status=code)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return ModelError(f"Gemini request timed out: {exc}", retryable=True, status=408)
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return ModelError(f"Gemini transport error: {exc}", retryable=True)
    if isinstance(exc, KeyPoolExhausted):
        return ModelError(f"Gemini key pool exhausted: {exc}", retryable=False, status=429)
    return ModelError(f"Gemini unexpected error: {type(exc).__name__}: {exc}", retryable=False)


# message fragments of key-scoped failures (revoked / suspended / expired / disabled
# project); HTTP 401 / 403 are key-scoped regardless of wording
_DEAD_KEY_MARKERS = (
    "api_key_invalid",
    "api key not valid",
    "api key expired",
    "permission_denied",
    "unauthenticated",
    "suspended",
    "leaked",
    "service_disabled",
    "has not been used in project",
)


def is_dead_key_error(err: ModelError) -> bool:
    """True when ``err`` indicts the API key rather than the request, so the call
    should move to another key instead of failing."""
    if err.status in (401, 403):
        return True
    text = str(err).lower()
    return err.status == 400 and any(m in text for m in _DEAD_KEY_MARKERS)


def failure_outcome(err: ModelError) -> Outcome:
    """``KeyPool.report`` outcome for a failed call (content-level failures such as
    bad JSON / empty candidates carry no status and are not the key's fault)."""
    if err.status == 429:
        return "429"
    if (err.status or 0) >= 500:
        return "5xx"
    if is_dead_key_error(err):
        return "dead"
    return "error" if err.status else "ok"


def _retry_after_s(exc: BaseException) -> float | None:
    """Gemini 429 bodies carry ``details[].retryDelay: "7s"``; honour it (capped)."""
    m = _RETRY_DELAY_RE.search(str(exc))
    return min(float(m.group(1)), 120.0) if m else None


class GeminiModel:
    """ChatModel for ``gemini:<model>``.  See module docstring."""

    provider = "gemini"

    def __init__(
        self,
        model: str,
        *,
        keys: list[str] | None = None,
        pool: KeyPool | None = None,
        timeout_s: float | None = None,
        max_attempts: int = 6,
        base_delay: float = 1.0,
        max_delay: float = MAX_WAIT_S,
        rpm_per_key: int | None = None,
        tpm_per_key: int | None = None,
        storm_gate: StormGate | None = None,
        storm_attempts: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        #: None = rotate_with_retries' default (60).  0 = a 503 is final: for probes,
        #: whose whole point is a fast verdict — the storm branch does NOT consume
        #: max_attempts, so max_attempts=1 alone still retried a 503 for up to 15 min.
        self.storm_attempts = storm_attempts
        keys = list(keys) if keys is not None else _default_keys()
        if pool is None and not keys:
            raise ModelError(
                "no Gemini API keys configured (GEMINI_API_KEYS / ~/.config/astra3d/gemini_keys.env)"
            )
        self.pool = pool or shared_pool(keys, rpm_per_key=rpm_per_key, tpm_per_key=tpm_per_key)
        self.storm_gate = storm_gate if storm_gate is not None else (
            storm_gate_for(f"gemini:{model}") if _rate().storm_gate else None
        )
        self.timeout_s = _default_timeout_s() if timeout_s is None else timeout_s
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._client_factory = client_factory
        self._thinking_ok = True  # flipped when the model rejects ThinkingConfig
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ proto
    @property
    def id(self) -> str:
        return f"gemini:{self.model}"

    def supports_vision(self) -> bool:
        return True

    # ---------------------------------------------------------------- clients
    def _client(self, key: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(key)
        ck = (key, int(self.timeout_s * 1000))
        with _registry_lock:
            client = _clients.get(ck)
            if client is None:
                client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=ck[1]))
                _clients[ck] = client
            return client

    # --------------------------------------------------------------- generate
    def generate(self, request: ChatRequest) -> ChatResponse:
        contents = to_contents(request.messages)
        warnings: list[str] = []
        state = {"config": self._config(request, warnings)}

        def downgrade_thinking(err: ModelError) -> bool:
            """Free-retry hook: model rejects ThinkingConfig → retry without it, once."""
            if not (self._thinking_ok and err.status == 400 and "thinking" in str(err).lower()):
                return False
            with self._lock:
                self._thinking_ok = False
            warnings.append(f"{self.model} rejected thinking config; retrying without it: {err}")
            state["config"] = self._config(request, warnings)
            return True

        return rotate_with_retries(
            self.pool,
            lambda key: self._once(key, contents, state["config"], request, warnings),
            classify=classify_exception,
            outcome_of=failure_outcome,
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            sleep=self._sleep,
            on_free_retry=downgrade_thinking,
            retry_after=_retry_after_s,
            tokens_of=lambda r: r.usage.input_tokens,
            tokens_hint=request_tokens(request, model_id=self.id),
            storm_gate=self.storm_gate,
            label=f"gemini {self.model}",
            **({} if self.storm_attempts is None else {"storm_attempts": self.storm_attempts}),
        )

    def _config(self, request: ChatRequest, warnings: list[str]) -> types.GenerateContentConfig:
        return build_config(
            request,
            timeout_ms=int(self.timeout_s * 1000),
            use_thinking=self._thinking_ok,
            warnings=warnings,
        )

    def _once(
        self,
        key: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
        request: ChatRequest,
        warnings: list[str],
    ) -> ChatResponse:
        client = self._client(key)
        with Stopwatch() as sw:
            resp = client.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        text, calls, finish = extract_candidate(resp)
        usage = parse_usage(resp, self.model)
        usage.latency_ms = sw.ms
        usage.tool_calls = len(calls)
        usage.cost_usd = estimate_cost("gemini", self.model, usage)
        parsed: Any = None
        if request.response_schema is not None and not calls:
            try:
                parsed = parse_json_lenient(text)
            except JsonParseError as exc:
                if finish in FATAL_FINISH or finish == "MAX_TOKENS":
                    # blocked, or truncated: retrying the same request cannot help
                    raise ModelError(
                        f"structured output unavailable (finish_reason={finish}; "
                        f"raise max_output_tokens if truncated): {exc}",
                        retryable=False,
                    ) from exc
                raise ModelError(
                    f"structured output is not valid JSON (finish_reason={finish}): {exc}",
                    retryable=True,
                ) from exc
        elif finish in RETRYABLE_FINISH and not calls and not text.strip():
            raise ModelError(f"Gemini finish_reason={finish}", retryable=True)
        raw: dict[str, Any] = {
            "finish_reason": finish,
            "model_version": resp.model_version,
            "response_id": resp.response_id,
            "key": f"…{key[-4:]}",
        }
        if warnings:
            raw["warnings"] = list(warnings)
        return ChatResponse(
            text=text, parsed=parsed, tool_calls=calls, finish_reason=finish, usage=usage, raw=raw
        )
