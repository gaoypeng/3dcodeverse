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
from codeverse.models.keypool import KeyPool, KeyPoolExhausted, Outcome
from codeverse.models.parts import Stopwatch
from codeverse.models.pricing import estimate_cost
from codeverse.models.retry import backoff_delay
from codeverse.models.schema_utils import JsonParseError, parse_json_lenient

log = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")

_pools: dict[tuple[str, ...], KeyPool] = {}
_clients: dict[tuple[str, int], genai.Client] = {}
_registry_lock = threading.Lock()


def shared_pool(keys: list[str], *, rpm_per_key: int = 900) -> KeyPool:
    """One ``KeyPool`` per distinct key list so limiters are process-wide."""
    sig = tuple(keys)
    with _registry_lock:
        pool = _pools.get(sig)
        if pool is None:
            pool = KeyPool(keys, rpm_per_key=rpm_per_key)
            _pools[sig] = pool
        return pool


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
        timeout_s: float = 300.0,
        max_attempts: int = 6,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        rpm_per_key: int = 900,
        sleep: Callable[[float], None] = time.sleep,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        keys = list(keys) if keys is not None else _default_keys()
        if pool is None and not keys:
            raise ModelError(
                "no Gemini API keys configured (GEMINI_API_KEYS / ~/.config/astra3d/gemini_keys.env)"
            )
        self.pool = pool or shared_pool(keys, rpm_per_key=rpm_per_key)
        self.timeout_s = timeout_s
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
        failed_keys: set[str] = set()  # keys that 429'd or looked dead during this call
        dead_keys: set[str] = set()
        last_err: ModelError | None = None
        attempt = 0
        config = self._config(request, warnings)
        while attempt < self.max_attempts:
            attempt += 1
            # never go back to a key that looked dead this call; throttled keys are
            # excluded while an untried one remains, else acquire() waits for a cooldown
            exclude = dead_keys | (failed_keys if len(failed_keys) < len(self.pool) else set())
            try:
                key = self.pool.acquire(exclude=exclude)
            except KeyPoolExhausted as exc:
                raise classify_exception(exc) from exc
            try:
                resp = self._once(key, contents, config, request, warnings)
                self.pool.report(
                    key, "ok", tokens=resp.usage.input_tokens + resp.usage.output_tokens
                )
                self._bench(dead_keys)
                return resp
            except Exception as exc:  # noqa: BLE001 - classified below
                err = classify_exception(exc)
                last_err = err
                if self._thinking_ok and err.status == 400 and "thinking" in str(err).lower():
                    # model rejects ThinkingConfig: retry without it, once, for free
                    with self._lock:
                        self._thinking_ok = False
                    warnings.append(
                        f"{self.model} rejected thinking config; retrying without it: {err}"
                    )
                    config = self._config(request, warnings)
                    attempt -= 1
                    continue
                outcome = failure_outcome(err)
                if outcome == "dead":
                    # key-scoped: move on at once (no budget, no sleep); the key is only
                    # benched once another key proves the request itself is fine
                    dead_keys.add(key)
                    failed_keys.add(key)
                    self.pool.report(key, "error")
                    if len(dead_keys) < len(self.pool):
                        log.warning(
                            "gemini %s key …%s looks dead (%s); rotating", self.model, key[-4:], err
                        )
                        attempt -= 1
                        continue
                    raise err from exc  # every key failed the same way: not the keys' fault
                self.pool.report(
                    key, outcome, retry_after_s=_retry_after_s(exc) if outcome == "429" else None
                )
                if outcome == "429":
                    failed_keys.add(key)
                    if len(failed_keys) < len(self.pool):
                        # an untried key remains: rotation is free, only a courtesy pause
                        attempt -= 1
                        log.warning(
                            "gemini %s key …%s throttled (%s); rotating to a fresh key",
                            self.model,
                            key[-4:],
                            err,
                        )
                        self._sleep(0.5)
                        continue
                if not err.retryable or attempt >= self.max_attempts:
                    self._bench(dead_keys)
                    raise err from exc
                delay = backoff_delay(attempt, base_delay=self.base_delay, max_delay=self.max_delay)
                log.warning(
                    "gemini %s attempt %d/%d failed (%s); retrying in %.1fs",
                    self.model,
                    attempt,
                    self.max_attempts,
                    err,
                    delay,
                )
                self._sleep(delay)
        assert last_err is not None
        self._bench(dead_keys)
        raise last_err

    def _bench(self, dead_keys: set[str]) -> None:
        """Mark keys that failed with key-scoped errors dead — called once the call
        got past the auth layer on some other key (success, 429, 5xx, bad JSON …)."""
        for key in dead_keys:
            self.pool.report(key, "dead")
        dead_keys.clear()

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
