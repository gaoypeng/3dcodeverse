"""Gemini backend: ``ChatRequest`` ⇄ google-genai conversion, and ``GeminiModel`` (shared
``KeyPool``, ``rotate_with_retries``, streaming with stall detection)."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from codeverse3d.config import get_settings
from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
)
from codeverse3d.contracts.common import Usage
from c3d_flywheel.models.base import ModelError
from c3d_flywheel.models.parts import (
    FailedBills,
    Stopwatch,
    attempt_timeout_s,
    default_timeout_s,
    image_bytes,
    message_blocks,
    retry_budget_s,
    retryable_status,
)
from c3d_flywheel.models.pricing import estimate_cost
from c3d_flywheel.models.retry import KeyPool, KeyPoolExhausted, rotate_with_retries
from c3d_flywheel.models.schema_utils import JsonParseError, parse_json_lenient, to_gemini_schema

#: thinking level → token budget
THINKING_BUDGET: dict[str, int] = {"off": 0, "low": 1024, "medium": 4096, "high": 16384}

#: finish reasons worth one more attempt
RETRYABLE_FINISH = {"RECITATION", "MALFORMED_FUNCTION_CALL", "OTHER", "UNEXPECTED_TOOL_CALL"}
#: finish reasons retrying cannot fix
FATAL_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


# ------------------------------------------------------------------ contents
def _image_part(p: ImagePart) -> types.Part:
    raw, mime = image_bytes(p)
    return types.Part.from_bytes(data=raw, mime_type=mime)


def to_contents(messages: list[ChatMessage]) -> list[types.Content]:
    """ChatMessages → Gemini ``Content`` list."""
    out: list[types.Content] = []
    for msg in messages:
        role = "model" if msg.role == "assistant" else "user"
        parts: list[types.Part] = message_blocks(msg, text=lambda t: types.Part.from_text(text=t), image=_image_part)
        if not parts:
            continue
        # Gemini wants strict role alternation
        if out and out[-1].role == role:
            out[-1].parts.extend(parts)  # type: ignore[union-attr]
        else:
            out.append(types.Content(role=role, parts=parts))
    if not out:
        raise ModelError("ChatRequest has no content to send")
    return out


# -------------------------------------------------------------------- config
def build_config(
    request: ChatRequest,
    *,
    timeout_ms: int,
    use_thinking: bool = True,
) -> types.GenerateContentConfig:
    """Map a ChatRequest onto ``GenerateContentConfig``."""
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
    if request.response_schema is not None:
        cfg["response_mime_type"] = "application/json"
        cfg["response_schema"] = to_gemini_schema(request.response_schema)
    return types.GenerateContentConfig(**cfg)


def clip_timeout(
    config: types.GenerateContentConfig, timeout_ms: int
) -> types.GenerateContentConfig:
    """``config`` with its HTTP timeout set to ``timeout_ms`` (same object if unchanged)."""
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


def _guard_candidates(resp: types.GenerateContentResponse, usage: Usage | None, what: str) -> Any:
    """The first candidate, or a blocked-prompt (fatal) / no-candidates (retryable) error."""
    pf = resp.prompt_feedback
    if pf is not None and pf.block_reason:
        raise ModelError(
            f"{what} prompt blocked: {pf.block_reason} {pf.block_reason_message or ''}".strip(),
            retryable=False, usage=usage,
        )
    if not resp.candidates:
        raise ModelError(f"{what} returned no candidates", retryable=True, usage=usage)
    return resp.candidates[0]


def extract_candidate(
    resp: types.GenerateContentResponse, usage: Usage | None = None
) -> tuple[str, str]:
    """→ ``(text, finish_reason)``; raises ``ModelError`` carrying ``usage`` (these failures are billed)."""
    cand = _guard_candidates(resp, usage, "Gemini")
    finish = str(cand.finish_reason.value if cand.finish_reason else "") or "UNKNOWN"
    texts: list[str] = []
    for part in cand.content.parts if cand.content and cand.content.parts else []:
        if part.text and not part.thought:
            texts.append(part.text)
    text = "".join(texts)
    if not text:
        if finish in FATAL_FINISH:
            raise ModelError(
                f"Gemini produced no content (finish_reason={finish})", retryable=False, usage=usage
            )
        if finish == "MAX_TOKENS":
            # Gemini 3 always thinks; a small max_output_tokens is eaten by thoughts.  The
            # "finish_reason=MAX_TOKENS" text is matched by the planner's grow-the-budget retry.
            thoughts = resp.usage_metadata.thoughts_token_count if resp.usage_metadata else None
            raise ModelError(
                f"Gemini produced no content (finish_reason=MAX_TOKENS): max_output_tokens exhausted by thinking "
                f"(thoughts_tokens={thoughts}); raise max_output_tokens",
                retryable=False, usage=usage,
            )
        raise ModelError(f"Gemini produced no content (finish_reason={finish})",
                         retryable=True, usage=usage)
    return text, finish


# ===================================================================== gemini
log = logging.getLogger(__name__)

#: ceiling of the inter-chunk read timeout under streaming: a hung socket dies within it,
#: while a long healthy stream is bounded only by its attempt budget
STREAM_STALL_S = 300.0

_pools: dict[tuple[str, ...], KeyPool] = {}
_clients: dict[tuple[str, int], genai.Client] = {}
_registry_lock = threading.Lock()


def shared_pool(keys: list[str], *, max_in_flight: int | None = None) -> KeyPool:
    """One ``KeyPool`` per (key list, in-flight cap) per process; slots are machine-wide
    (``<cache_dir>/slots/gemini/NN.lock``)."""
    cap = _rate().max_in_flight if max_in_flight is None else max_in_flight
    sig = (*keys, f"|{cap}")
    with _registry_lock:
        pool = _pools.get(sig)
        if pool is None:
            slots = get_settings().cache_dir / "slots" / "gemini" if cap else None
            # random first key, so parallel processes do not all start on key 0
            pool = KeyPool(keys, max_in_flight=cap or 0, slots_dir=slots, start=random.randrange(len(keys)))
            _pools[sig] = pool
        return pool


def _rate() -> Any:
    return get_settings().rate


def _default_keys() -> list[str]:
    return list(get_settings().gemini_api_keys)


def _keys_or_raise(keys: list[str] | None, pool: KeyPool | None) -> list[str]:
    keys = list(keys) if keys is not None else _default_keys()
    if pool is None and not keys:
        raise ModelError("no Gemini API keys configured (GEMINI_API_KEYS / ~/.config/astra3d/gemini_keys.env)")
    return keys


def _client_for(key: str, timeout_s: float, factory: Callable[[str], Any] | None) -> Any:
    """One ``genai.Client`` per (key, timeout), process-wide; ``factory`` bypasses the cache."""
    if factory is not None:
        return factory(key)
    ck = (key, int(timeout_s * 1000))
    with _registry_lock:
        client = _clients.get(ck)
        if client is None:
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=ck[1],
                # the WSL2 IPv6 path silently hangs reads: bind the transport to IPv4
                client_args={"transport": httpx.HTTPTransport(local_address="0.0.0.0")}))
            _clients[ck] = client
        return client


def classify_exception(exc: BaseException) -> ModelError:
    """Map provider / transport exceptions onto ``ModelError``."""
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, genai_errors.APIError):
        code = int(exc.code or 0)
        msg = f"Gemini API error {code}: {exc.message}"
        return ModelError(msg, retryable=retryable_status(code), status=code)
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return ModelError(f"Gemini request timed out: {exc}", retryable=True, status=408)
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return ModelError(f"Gemini transport error: {exc}", retryable=True)
    if isinstance(exc, KeyPoolExhausted):
        return ModelError(f"Gemini key pool exhausted: {exc}", retryable=False, status=429)
    return ModelError(f"Gemini unexpected error: {type(exc).__name__}: {exc}", retryable=False)


def _drain_stream(
    it: Iterable[types.GenerateContentResponse],
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> list[types.GenerateContentResponse]:
    """Collect every chunk, enforcing the attempt ``deadline`` between chunks (the wire
    timeout only bounds silence between chunks)."""
    chunks: list[types.GenerateContentResponse] = []
    for ch in it:
        chunks.append(ch)
        if clock() > deadline:
            raise ModelError(
                f"Gemini stream exceeded its attempt budget after {len(chunks)} chunks",
                retryable=True,
            )
    return chunks


def _merge_stream_chunks(
    chunks: list[types.GenerateContentResponse],
) -> types.GenerateContentResponse:
    """One response from a chunk sequence: parts concatenate; finish_reason, usage_metadata
    and prompt_feedback come from the last chunk carrying each."""
    if not chunks:
        raise ModelError("Gemini stream yielded no chunks", retryable=True)
    parts: list[types.Part] = []
    finish = None
    usage_md = None
    feedback = None
    base = None
    for ch in chunks:
        if ch.usage_metadata is not None:
            usage_md = ch.usage_metadata
        if ch.prompt_feedback is not None and ch.prompt_feedback.block_reason:
            feedback = ch.prompt_feedback
        if ch.candidates:
            base = ch
            cand = ch.candidates[0]
            if cand.content and cand.content.parts:
                parts.extend(cand.content.parts)
            if cand.finish_reason is not None:
                finish = cand.finish_reason
    if base is None:  # no candidates: extract_candidate raises
        base = chunks[-1]
    else:
        base.candidates[0].content = types.Content(role="model", parts=parts)
        base.candidates[0].finish_reason = finish
    base.usage_metadata = usage_md
    if feedback is not None:
        base.prompt_feedback = feedback
    return base


class GeminiModel:
    """ChatModel for ``gemini:<model>``."""

    provider = "gemini"

    def __init__(
        self,
        model: str,
        *,
        keys: list[str] | None = None,
        pool: KeyPool | None = None,
        timeout_s: float | None = None,
        max_attempts: int = 6,
        storm_attempts: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        #: None = rotate_with_retries' default; 0 = a 503 is final (the storm branch ignores max_attempts)
        self.storm_attempts = storm_attempts
        keys = _keys_or_raise(keys, pool)
        self.pool = pool or shared_pool(keys)
        self.timeout_s = default_timeout_s() if timeout_s is None else timeout_s
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._client_factory = client_factory
        self._thinking_ok = True  # flipped when the model rejects ThinkingConfig
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ proto
    @property
    def id(self) -> str:
        return f"gemini:{self.model}"

    # --------------------------------------------------------------- generate
    def generate(self, request: ChatRequest) -> ChatResponse:
        contents = to_contents(request.messages)
        state = {"config": self._config(request)}

        def downgrade_thinking(err: ModelError) -> bool:
            """Model rejects ThinkingConfig → retry without it, once."""
            if not (self._thinking_ok and err.status == 400 and "thinking" in str(err).lower()):
                return False
            with self._lock:
                self._thinking_ok = False
            log.warning("%s rejected thinking config; retrying without it: %s", self.model, err)
            state["config"] = self._config(request)
            return True

        budget = retry_budget_s(request.max_wait_s)
        deadline = time.monotonic() + budget
        stats: dict[str, Any] = {}
        bills = FailedBills()
        try:
            resp = rotate_with_retries(
                self.pool,
                bills.track(lambda key: self._once(
                    key, contents, self._attempt_config(state["config"], deadline),
                    request,
                )),
                classify=classify_exception,
                max_attempts=self.max_attempts,
                max_total_s=budget,
                sleep=self._sleep,
                on_free_retry=downgrade_thinking,
                label=f"gemini {self.model}",
                stats=stats,
                **({} if self.storm_attempts is None else {"storm_attempts": self.storm_attempts}),
            )
        except ModelError as e:
            bills.charge(e)
            raise
        resp = bills.join(resp)
        resp.raw["attempts"] = int(stats.get("attempts", 0))
        return resp

    def _config(self, request: ChatRequest) -> types.GenerateContentConfig:
        return build_config(
            request,
            timeout_ms=int(self.timeout_s * 1000),
            use_thinking=self._thinking_ok,
        )

    def _attempt_config(
        self, config: types.GenerateContentConfig, deadline: float
    ) -> types.GenerateContentConfig:
        """Config whose HTTP timeout is the remaining retry budget (see ``attempt_timeout_s``)."""
        return clip_timeout(config, int(attempt_timeout_s(deadline, self.timeout_s) * 1000))

    def _once(
        self,
        key: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
        request: ChatRequest,
    ) -> ChatResponse:
        client = _client_for(key, self.timeout_s, self._client_factory)

        # the attempt budget becomes an in-loop deadline; the wire timeout drops to the stall ceiling
        cfg_ms = config.http_options.timeout if config.http_options is not None else None
        attempt_s = (float(cfg_ms) / 1000.0) if cfg_ms else self.timeout_s
        wire = clip_timeout(config, int(min(attempt_s, STREAM_STALL_S) * 1000))
        with Stopwatch() as sw:
            chunks = _drain_stream(
                client.models.generate_content_stream(
                    model=self.model, contents=contents, config=wire
                ),
                time.monotonic() + attempt_s,
            )
        resp = _merge_stream_chunks(chunks)
        usage = parse_usage(resp, self.model)  # before extract_candidate, whose raises carry it
        usage.latency_ms = sw.ms
        usage.cost_usd = estimate_cost("gemini", self.model, usage)
        text, finish = extract_candidate(resp, usage)
        parsed: Any = None
        if request.response_schema is not None:
            try:
                parsed = parse_json_lenient(text)
            except JsonParseError as exc:
                if finish in FATAL_FINISH or finish == "MAX_TOKENS":
                    raise ModelError(
                        f"structured output unavailable (finish_reason={finish}; "
                        f"raise max_output_tokens if truncated): {exc}",
                        retryable=False,
                        usage=usage,
                    ) from exc
                raise ModelError(
                    f"structured output is not valid JSON (finish_reason={finish}): {exc}",
                    retryable=True,
                    usage=usage,
                ) from exc
        elif finish in RETRYABLE_FINISH and not text.strip():
            raise ModelError(f"Gemini finish_reason={finish}", retryable=True, usage=usage)
        return ChatResponse(text=text, parsed=parsed, finish_reason=finish, usage=usage,
                            raw={"key": f"…{key[-4:]}"})
