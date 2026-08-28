"""ChatRequest ⇄ google-genai types (contents, config, tools, response parsing).

Kept separate from ``gemini.py`` (which owns clients, keys, retries) so each
module stays small and the conversions are unit-testable offline.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image

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
from codeverse.models.parts import BoundedCache, Stopwatch, image_bytes
from codeverse.models.pricing import estimate_cost
from codeverse.models.retry import (
    MAX_WAIT_S,
    RETRY_DEADLINE_S,
    KeyPool,
    KeyPoolExhausted,
    OnAttempt,
    Outcome,
    StormGate,
    _Try,
    request_tokens,
    rotate_with_retries,
)
from codeverse.models.retry import storm_gate as storm_gate_for
from codeverse.models.schema_utils import JsonParseError, parse_json_lenient, to_gemini_schema

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

#: prefix of the ids WE mint when the provider sent none (``extract_candidate``).
#: A synthetic id exists only to pair ToolCallPart/ToolResultPart locally and is
#: NEVER echoed back to the provider; a GENUINE provider id is echoed verbatim on
#: both ``FunctionCall`` and ``FunctionResponse``.
SYNTHETIC_CALL_ID_PREFIX = "synth-call-"


def _next_call_id() -> str:
    with _call_lock:
        _call_counter[0] += 1
        return f"{SYNTHETIC_CALL_ID_PREFIX}{_call_counter[0]:06d}"


def is_synthetic_call_id(call_id: str | None) -> bool:
    """True for ids minted here rather than issued by the provider."""
    return bool(call_id) and str(call_id).startswith(SYNTHETIC_CALL_ID_PREFIX)


def _echo_id(call_id: str | None) -> str | None:
    """The id to send back to Gemini: the provider's own id verbatim, never a synthetic one."""
    return None if not call_id or is_synthetic_call_id(call_id) else str(call_id)


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
                    function_call=types.FunctionCall(
                        name=p.name, args=dict(p.arguments), id=_echo_id(p.id)
                    )
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
    return types.Part(
        function_response=types.FunctionResponse(name=p.name, response=payload, id=_echo_id(p.call_id))
    )


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


def clip_timeout(
    config: types.GenerateContentConfig, timeout_ms: int
) -> types.GenerateContentConfig:
    """A copy of ``config`` whose per-request HTTP timeout is ``timeout_ms`` (the
    SAME object when it already matches — the common full-budget case allocates
    nothing).  :func:`build_config` owns the initial ``timeout_ms``; this is the
    one sanctioned way to shorten it for a single attempt whose remaining retry
    budget is smaller than the configured read timeout
    (``GeminiModel._attempt_config``)."""
    current = config.http_options.timeout if config.http_options is not None else None
    if current == timeout_ms:
        return config
    return config.model_copy(update={"http_options": types.HttpOptions(timeout=timeout_ms)})


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


def extract_candidate(
    resp: types.GenerateContentResponse, usage: Usage | None = None
) -> tuple[str, list[ToolCallPart], str]:
    """→ ``(text, tool_calls, finish_reason)``.  Raises ``ModelError`` on blocked
    prompts (non-retryable) or empty candidates (retryable); ``usage`` (what the caller
    already parsed off ``resp``) rides on the error, because every one of these
    failures was billed."""
    pf = resp.prompt_feedback
    if pf is not None and pf.block_reason:
        raise ModelError(
            f"prompt blocked by Gemini: {pf.block_reason} {pf.block_reason_message or ''}".strip(),
            retryable=False, usage=usage,
        )
    if not resp.candidates:
        raise ModelError("Gemini returned no candidates", retryable=True, usage=usage)
    cand = resp.candidates[0]
    finish = str(cand.finish_reason.value if cand.finish_reason else "") or "UNKNOWN"
    texts: list[str] = []
    calls: list[ToolCallPart] = []
    for part in cand.content.parts if cand.content and cand.content.parts else []:
        if part.function_call is not None:
            fc = part.function_call
            # a genuine provider id is kept verbatim (and echoed back later); a synthetic
            # one is minted, marked by its prefix, only so the parts pair locally
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
                f"Gemini produced no content (finish_reason={finish})", retryable=False, usage=usage
            )
        if finish == "MAX_TOKENS":
            # Gemini 3 models always think (budget 0 is accepted but ignored); a tiny
            # max_output_tokens is eaten by thoughts.  Retrying cannot help — and this is
            # the most expensive failure on Gemini 3: every thought token was billed.
            thoughts = resp.usage_metadata.thoughts_token_count if resp.usage_metadata else None
            raise ModelError(
                f"Gemini produced no content: max_output_tokens exhausted by thinking "
                f"(thoughts_tokens={thoughts}); raise max_output_tokens",
                retryable=False, usage=usage,
            )
        raise ModelError(f"Gemini produced no content (finish_reason={finish})",
                         retryable=True, usage=usage)
    return text, calls, finish


# ===================================================================== gemini
# (merged from codeverse/models/gemini.py, 2026-08-28)
log = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")

#: floor of the per-attempt HTTP read timeout derived from the remaining retry
#: budget (``ChatRequest.max_wait_s``): a call that starts near its deadline still
#: gets ONE real attempt instead of an instant socket timeout.  20 s matches the
#: judge's own floor (``judges.vlm_judge.SAMPLE_MIN_WAIT_S`` = 20 s), so an in-flight
#: attempt may overshoot the deadline by at most this floor — never by the full
#: ``timeout_s`` read timeout, which was the dominant overshoot (audit 2026-08-27,
#: when that ceiling was 300 s).
HTTP_TIMEOUT_FLOOR_S = 20.0

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
        hedge: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        #: None = rotate_with_retries' default (60).  0 = a 503 is final: for probes,
        #: whose whole point is a fast verdict — the storm branch does NOT consume
        #: max_attempts, so max_attempts=1 alone still retried a 503 for up to 15 min.
        self.storm_attempts = storm_attempts
        #: keys a retry is raced on after the call's first 503 (``Settings.rate.hedge``,
        #: 2; 1 = off) — see ``rotate_with_retries`` and docs/COST.md §27
        self.hedge = max(1, int(_rate().hedge if hedge is None else hedge))
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

        # the caller's budget clips the retry deadline, never extends it
        budget = RETRY_DEADLINE_S if request.max_wait_s is None else min(RETRY_DEADLINE_S, float(request.max_wait_s))
        deadline = time.monotonic() + budget  # mirrors rotate_with_retries' own deadline
        stats: dict[str, Any] = {}
        wasted: list[Usage] = []  # discarded round-trips the provider still billed
        try:
            resp = rotate_with_retries(
                self.pool,
                lambda key: self._once(
                    key, contents, self._attempt_config(state["config"], deadline),
                    request, warnings,
                ),
                classify=classify_exception,
                outcome_of=failure_outcome,
                max_attempts=self.max_attempts,
                base_delay=self.base_delay,
                max_delay=self.max_delay,
                max_total_s=budget,
                hedge=self.hedge,
                sleep=self._sleep,
                on_free_retry=downgrade_thinking,
                retry_after=_retry_after_s,
                tokens_of=lambda r: r.usage.input_tokens,
                tokens_hint=request_tokens(request, model_id=self.id),
                storm_gate=self.storm_gate,
                label=f"gemini {self.model}",
                stats=stats,
                on_attempt=self._attempt_hook(wasted),
                **({} if self.storm_attempts is None else {"storm_attempts": self.storm_attempts}),
            )
        except ModelError as err:
            err.attempts = int(stats.get("attempts", 0))  # the ledger's error row wants it too
            raise
        # how hard the call was, next to which key served it (``_once``): ledger fields
        resp.raw["attempts"] = int(stats.get("attempts", 0))
        resp.raw["hedged"] = int(stats.get("hedged", 0))
        if wasted:
            # money the winner's usage does not include; a hedge loser that lands AFTER
            # this returns reaches the ledger through the sink, never this field
            resp.raw["wasted_usage"] = sum(wasted[1:], wasted[0])
        return resp

    def _config(self, request: ChatRequest, warnings: list[str]) -> types.GenerateContentConfig:
        return build_config(
            request,
            timeout_ms=int(self.timeout_s * 1000),
            use_thinking=self._thinking_ok,
            warnings=warnings,
        )

    def _attempt_config(
        self, config: types.GenerateContentConfig, deadline: float
    ) -> types.GenerateContentConfig:
        """The config for ONE attempt: its HTTP read timeout is the remaining retry
        budget, capped at ``self.timeout_s`` and floored at
        :data:`HTTP_TIMEOUT_FLOOR_S`.  A 20 s-budget turn used to hand the provider
        a 300 s socket — the dominant deadline overshoot (audit 2026-08-27)."""
        remaining = deadline - time.monotonic()
        want_s = min(self.timeout_s, max(HTTP_TIMEOUT_FLOOR_S, remaining))
        return clip_timeout(config, int(want_s * 1000))

    def _attempt_hook(self, wasted: list[Usage]) -> OnAttempt:
        """Per-round-trip observer.  It collects every DISCARDED round-trip that still
        cost money into ``wasted`` (published on ``resp.raw["wasted_usage"]`` — the only
        place a budget guard can see that money) and feeds the ledger's per-attempt sink
        when the metering layer installed one (``cost.instrument.MeteredChatModel``).
        Captured once per logical call so a hedge loser landing later, in its own thread,
        still reports through it.  Imported lazily: the models package must not import
        the cost package at module level (``cost.ledger`` imports ``models.pricing``)."""
        try:
            from codeverse.cost.context import AttemptRecord, attempt_sink

            sink = attempt_sink()
        except Exception:  # pragma: no cover - accounting must never break a call
            AttemptRecord = sink = None

        def on_attempt(t: _Try, no: int, discarded: bool) -> None:
            if t.err is None:
                usage: Usage = t.result.usage
                outcome, error = "ok", ""
            else:
                usage = getattr(t.err, "usage", None) or Usage()
                outcome, error = t.outcome, str(t.err)
            if discarded and usage.cost_usd:
                wasted.append(usage)
            if sink is not None:
                sink(AttemptRecord(attempt=no, key=t.key, outcome=outcome,
                                   discarded=discarded, usage=usage, error=error))

        return on_attempt

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
        usage = parse_usage(resp, self.model)  # BEFORE extract_candidate: its raises carry it
        usage.latency_ms = sw.ms
        usage.cost_usd = estimate_cost("gemini", self.model, usage)
        text, calls, finish = extract_candidate(resp, usage)
        usage.tool_calls = len(calls)
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
                        usage=usage,  # the provider billed this reply; the ledger wants it
                    ) from exc
                raise ModelError(
                    f"structured output is not valid JSON (finish_reason={finish}): {exc}",
                    retryable=True,
                    usage=usage,
                ) from exc
        elif finish in RETRYABLE_FINISH and not calls and not text.strip():
            raise ModelError(f"Gemini finish_reason={finish}", retryable=True, usage=usage)
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


# ===================================================================== gemini_image
# (merged from codeverse/models/gemini_image.py, 2026-08-28)
log = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
FALLBACK_IMAGE_MODEL = "gemini-2.5-flash-image"

#: approximate USD per generated 1024² image (floor for cost accounting)
IMAGE_USD: dict[str, float] = {
    "gemini-3.1-flash-image": 0.067,
    "gemini-2.5-flash-image": 0.039,
}

#: requested pixel size → Gemini ``image_size`` token
_IMAGE_SIZE_TOKEN: dict[int, str] = {512: "1K", 1024: "1K", 2048: "2K", 4096: "4K"}


@runtime_checkable
class ImageModel(Protocol):
    """Text-to-image backend.  Implementations must be thread-safe."""

    model: str

    @property
    def id(self) -> str: ...

    def generate(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
    ) -> list[Image.Image]: ...

    def generate_with_usage(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
    ) -> tuple[list[Image.Image], Usage]: ...


def image_cost(model: str, usage: Usage, n_images: int) -> float:
    """Token-priced cost floored by the per-image price (approximate)."""
    token_cost = estimate_cost("gemini", model, usage)
    per_image = IMAGE_USD.get(model.strip().lower(), 0.0)
    return max(token_cost, per_image * max(0, n_images))


def _to_part(ref: Image.Image | Path | str) -> types.Part:
    if isinstance(ref, Image.Image):
        buf = io.BytesIO()
        ref.convert("RGB").save(buf, format="PNG")
        return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")
    p = Path(ref)
    if not p.is_file():
        raise ModelError(f"reference image not found: {p}")
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)


def _is_model_missing(err: ModelError) -> bool:
    s = str(err).lower()
    return err.status == 404 or (err.status == 400 and ("not found" in s or "not supported" in s))


def _record(usage: Usage, n_images: int) -> None:
    """One ledger row per image batch (the image model is not a ChatModel, so
    ``models.registry`` cannot meter it).  Never raises."""
    try:
        from codeverse.cost import record_call
        from codeverse.cost.types import Role, Stage

        record_call(usage, stage=Stage.TEXTURE, role=Role.IMAGE, label="image",
                    backend="gemini-image", model=usage.model, n_calls=max(1, n_images))
    except Exception:  # noqa: BLE001 - accounting must never break a texture pass
        pass


class GeminiImageModel:
    """See module docstring.  ``model`` is the primary; ``fallback`` the second try."""

    provider = "gemini"

    def __init__(
        self,
        model: str = DEFAULT_IMAGE_MODEL,
        *,
        fallback: str | None = FALLBACK_IMAGE_MODEL,
        keys: list[str] | None = None,
        pool: KeyPool | None = None,
        timeout_s: float = 180.0,
        max_attempts: int = 4,
        base_delay: float = 1.0,
        max_delay: float = MAX_WAIT_S,
        hedge: int = 1,
        sleep: Callable[[float], None] = time.sleep,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        self.fallback = fallback if fallback and fallback != model else None
        #: no 503 hedge by default: an image is billed per image, so a hedge that
        #: lands twice pays for two of them (the chat models default to 2)
        self.hedge = max(1, int(hedge))
        keys = list(keys) if keys is not None else _default_keys()
        if pool is None and not keys:
            raise ModelError("no Gemini API keys configured (GEMINI_API_KEYS / ~/.config/astra3d/gemini_keys.env)")
        self.pool = pool or shared_pool(keys)
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._sleep = sleep
        # reuse GeminiModel's cached clients (same key → same genai.Client)
        self._clients = GeminiModel(model, pool=self.pool, timeout_s=timeout_s, client_factory=client_factory)
        self.storm_gate = self._clients.storm_gate
        self._lock = threading.Lock()
        self._primary_dead = False

    @property
    def id(self) -> str:
        return f"gemini-image:{self.model}"

    # ------------------------------------------------------------------ API
    def generate(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
        max_wait_s: float | None = None,
    ) -> list[Image.Image]:
        images, _ = self.generate_with_usage(prompt, size=size, n=n, seed=seed,
                                             reference_images=reference_images, max_wait_s=max_wait_s)
        return images

    def generate_with_usage(
        self,
        prompt: str,
        *,
        size: int = 1024,
        n: int = 1,
        seed: int | None = None,
        reference_images: Sequence[Image.Image | Path | str] = (),
        max_wait_s: float | None = None,
    ) -> tuple[list[Image.Image], Usage]:
        """``max_wait_s`` is the caller's retry budget for ONE image (each of the
        ``n`` images gets its own window, and a fallback model too), exactly like
        ``ChatRequest.max_wait_s`` clips ``GeminiModel.generate``; ``None`` = the
        full ``retry.RETRY_DEADLINE_S`` (900 s).  Kept off the ``ImageModel``
        protocol so fakes stay conformant; callers that hold a deadline pass it."""
        if not prompt.strip():
            raise ModelError("empty image prompt")
        if n < 1:
            raise ModelError("n must be >= 1")
        images: list[Image.Image] = []
        usage = Usage(backend="gemini-image", model=self.model)
        for i in range(n):
            got, u = self._one(prompt, size=size, seed=None if seed is None else seed + i,
                               refs=reference_images, max_wait_s=max_wait_s)
            images.extend(got)
            usage = usage + u
        if len(images) > n:
            images = images[:n]
        _record(usage, len(images))
        return images, usage

    # ------------------------------------------------------------------ internals
    def _models(self) -> list[str]:
        with self._lock:
            dead = self._primary_dead
        order = [self.model] if not dead else []
        if self.fallback:
            order.append(self.fallback)
        if not order:
            order = [self.model]
        return order

    def _one(
        self, prompt: str, *, size: int, seed: int | None,
        refs: Sequence[Image.Image | Path | str], max_wait_s: float | None = None,
    ) -> tuple[list[Image.Image], Usage]:
        last: ModelError | None = None
        for model in self._models():
            try:
                return self._attempts(model, prompt, size=size, seed=seed, refs=refs,
                                      max_wait_s=max_wait_s)
            except ModelError as err:
                last = err
                if model == self.model and self.fallback and (err.retryable or _is_model_missing(err)):
                    if _is_model_missing(err):
                        with self._lock:
                            self._primary_dead = True
                    log.warning("image model %s failed (%s); falling back to %s", model, err, self.fallback)
                    continue
                raise
        assert last is not None
        raise last

    def _attempts(
        self, model: str, prompt: str, *, size: int, seed: int | None,
        refs: Sequence[Image.Image | Path | str], max_wait_s: float | None = None,
    ) -> tuple[list[Image.Image], Usage]:
        parts: list[types.Part] = [_to_part(r) for r in refs]
        parts.append(types.Part.from_text(text=prompt))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="1:1", image_size=_IMAGE_SIZE_TOKEN.get(int(size), "1K")),
            seed=seed,
            http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)),
        )
        # the caller's budget clips the retry deadline, never extends it (the same
        # contract as GeminiModel.generate with ChatRequest.max_wait_s), and each
        # attempt's HTTP read timeout is the REMAINING budget, floored like
        # GeminiModel._attempt_config — a 60 s image budget no longer holds a
        # 180 s socket past its deadline.
        budget = RETRY_DEADLINE_S if max_wait_s is None else min(RETRY_DEADLINE_S, float(max_wait_s))
        deadline = time.monotonic() + budget

        def _attempt_config() -> types.GenerateContentConfig:
            remaining = max(HTTP_TIMEOUT_FLOOR_S, deadline - time.monotonic())
            return clip_timeout(config, int(min(self.timeout_s, remaining) * 1000))

        return rotate_with_retries(
            self.pool,
            lambda key: self._call(key, model, contents, _attempt_config(), size),
            classify=classify_exception,
            outcome_of=failure_outcome,
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            max_total_s=budget,
            hedge=self.hedge,
            sleep=self._sleep,
            retry_after=_retry_after_s,
            tokens_of=lambda r: r[1].input_tokens,
            storm_gate=self.storm_gate,
            label=f"image {model}",
        )

    def _call(
        self, key: str, model: str, contents: list[types.Content], config: types.GenerateContentConfig, size: int
    ) -> tuple[list[Image.Image], Usage]:
        client = self._clients._client(key)
        with Stopwatch() as sw:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
        usage = parse_usage(resp, model)  # parsed first: the raises below were billed too
        usage.backend = "gemini-image"
        usage.latency_ms = sw.ms
        pf = resp.prompt_feedback
        if pf is not None and pf.block_reason:
            raise ModelError(f"image prompt blocked: {pf.block_reason}", retryable=False, usage=usage)
        if not resp.candidates:
            raise ModelError("image model returned no candidates", retryable=True, usage=usage)
        cand = resp.candidates[0]
        finish = str(cand.finish_reason.name if cand.finish_reason is not None else "")
        images: list[Image.Image] = []
        for part in (cand.content.parts if cand.content and cand.content.parts else []):
            blob = getattr(part, "inline_data", None)
            if blob is not None and blob.data:
                img = Image.open(io.BytesIO(blob.data))
                img.load()
                images.append(img.convert("RGB"))
        if not images:
            if finish in FATAL_FINISH:
                raise ModelError(f"image generation refused (finish_reason={finish})",
                                 retryable=False, usage=usage)
            raise ModelError(f"image model returned no image (finish_reason={finish or 'unknown'})",
                             retryable=True, usage=usage)
        usage.cost_usd = image_cost(model, usage, len(images))
        if int(size) not in (0, 1024) and images[0].size != (size, size):
            images = [im.resize((int(size), int(size)), Image.LANCZOS) for im in images]
        return images, usage
