"""The whole Gemini stack in one module (merged 2026-08-28, d512ccc):

* conversion — ``ChatRequest`` ⇄ google-genai types (``to_contents``, ``build_config``,
  ``extract_candidate``, ``parse_usage``); pure, unit-testable offline;
* ``GeminiModel`` — the ``ChatModel``: a shared ``KeyPool`` over the configured keys,
  ``rotate_with_retries`` with the hedge, streaming with stall detection, per-attempt
  ledger rows;
* ``GeminiImageModel`` — text(+reference images) → PIL images on the same pool and
  retry machine, priced per generated image (``pricing.per_image_usd``), with a
  fallback model when the primary is missing.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image

from codeverse3d.config import Rate, get_settings
from codeverse3d.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
)
from codeverse3d.contracts.common import Usage
from codeverse3d.cost.context import AttemptRecord, attempt_sink
from codeverse3d.cost.ledger import record_call
from codeverse3d.cost.types import Role, Stage
from codeverse3d.models.base import ModelError
from codeverse3d.models.parts import (
    Stopwatch,
    attempt_timeout_s,
    default_timeout_s,
    image_bytes,
    message_blocks,
    retry_budget_s,
)
from codeverse3d.models.pricing import estimate_cost, per_image_usd
from codeverse3d.models.retry import KeyPool, KeyPoolExhausted, OnAttempt, _Try, rotate_with_retries
from codeverse3d.models.schema_utils import JsonParseError, parse_json_lenient, to_gemini_schema

#: INTERFACES.md mapping; ``off`` → 0 where the model allows it
THINKING_BUDGET: dict[str, int] = {"off": 0, "low": 1024, "medium": 4096, "high": 16384}

#: finish reasons that are worth one more attempt (transient model glitches)
RETRYABLE_FINISH = {"RECITATION", "MALFORMED_FUNCTION_CALL", "OTHER", "UNEXPECTED_TOOL_CALL"}
#: finish reasons that will not improve by retrying
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
        # merge consecutive same-role turns (Gemini wants strict alternation)
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


def _guard_candidates(resp: types.GenerateContentResponse, usage: Usage | None, what: str) -> Any:
    """The first candidate, or the two billed failures every Gemini reply can carry:
    a blocked prompt (non-retryable) and no candidates at all (retryable)."""
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
    """→ ``(text, finish_reason)``.  Raises ``ModelError`` on blocked
    prompts (non-retryable) or empty candidates (retryable); ``usage`` (what the caller
    already parsed off ``resp``) rides on the error, because every one of these
    failures was billed."""
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
            # Gemini 3 models always think (budget 0 is accepted but ignored); a tiny
            # max_output_tokens is eaten by thoughts.  Retrying cannot help — and this is
            # the most expensive failure on Gemini 3: every thought token was billed.  The
            # finish reason stays in the text: the planner's grow-the-budget retry keys on it.
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

_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")

#: ceiling of the per-READ (inter-chunk) HTTP timeout under streaming.  Five runs
#: died tonight (drill/vise/clamp x2, one plan hang) because a hung NON-streaming
#: read holds the socket for the whole attempt budget — up to model_timeout_s
#: (1200 s) — and the timeout is then terminal.  Streaming separates the two
#: cases: a hung socket delivers no chunk and dies within this ceiling with the
#: rest of the retry budget intact, while a legitimate 930 s plan streams tokens
#: continuously (145 tok/s p50, audit 2026-08-27) and is bounded only by its
#: attempt budget.  Generous vs the longest silent prefix we allow for thinking
#: before the first chunk.  ``C3D_STREAM=off`` restores the buffered call.
STREAM_STALL_S = 300.0

_pools: dict[tuple[str, ...], KeyPool] = {}
_clients: dict[tuple[str, int], genai.Client] = {}
_registry_lock = threading.Lock()


def shared_pool(keys: list[str], *, max_in_flight: int | None = None) -> KeyPool:
    """One ``KeyPool`` per distinct (key list, in-flight cap) per process; an unset cap comes
    from ``Settings.rate.max_in_flight``.  Its slots are machine-wide: every process draws
    from the same ``<cache_dir>/slots/gemini/NN.lock`` files (``retry.Slots``, COST §23)."""
    cap = _rate().max_in_flight if max_in_flight is None else max_in_flight
    sig = (*keys, f"|{cap}")
    with _registry_lock:
        pool = _pools.get(sig)
        if pool is None:
            slots = get_settings().cache_dir / "slots" / "gemini" if cap else None
            pool = KeyPool(keys, max_in_flight=cap or 0, slots_dir=slots)
            _pools[sig] = pool
        return pool


def _rate() -> Any:
    try:
        return get_settings().rate
    except Exception:  # pragma: no cover - settings must never break a model call
        return Rate()


def _default_keys() -> list[str]:
    return list(get_settings().gemini_api_keys)


def _keys_or_raise(keys: list[str] | None, pool: KeyPool | None) -> list[str]:
    keys = list(keys) if keys is not None else _default_keys()
    if pool is None and not keys:
        raise ModelError("no Gemini API keys configured (GEMINI_API_KEYS / ~/.config/astra3d/gemini_keys.env)")
    return keys


def _client_for(key: str, timeout_s: float, factory: Callable[[str], Any] | None) -> Any:
    """One ``genai.Client`` per (key, timeout), process-wide; ``factory`` (tests) bypasses the cache."""
    if factory is not None:
        return factory(key)
    ck = (key, int(timeout_s * 1000))
    with _registry_lock:
        client = _clients.get(ck)
        if client is None:
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=ck[1], client_args=_ipv4_client_args() or None))
            _clients[ck] = client
        return client


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


def _retry_after_s(exc: BaseException) -> float | None:
    """Gemini 429 bodies carry ``details[].retryDelay: "7s"``; honour it (capped)."""
    m = _RETRY_DELAY_RE.search(str(exc))
    return min(float(m.group(1)), 120.0) if m else None


def _ipv4_client_args() -> dict[str, Any]:
    """Bind the sync transport to IPv4.  Every one of tonight's five hung reads sat
    on an IPv6 destination (2001:4860::/32) with the response headers never arriving
    — the WSL2 IPv6 path drops these silently and a buffered read then holds the
    socket for the whole attempt budget.  ``C3D_IPV4=off`` restores the default
    (dual-stack) resolution."""
    return {"transport": httpx.HTTPTransport(local_address="0.0.0.0")} if get_settings().ipv4 else {}


def _drain_stream(
    it: Iterable[types.GenerateContentResponse],
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> list[types.GenerateContentResponse]:
    """Collect every chunk, enforcing the ATTEMPT budget between chunks.  The wire
    timeout under streaming only bounds the silence BETWEEN chunks (httpx applies
    the read timeout per network read), so a healthy-but-endless stream is cut
    here instead.  Tokens generated before the cut were billed but cannot be
    counted: usage_metadata only arrives on the final chunk."""
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
    """One response out of a chunk sequence: parts concatenate in arrival order;
    finish_reason, usage_metadata and prompt_feedback come from the last chunk
    that carries each (usage is cumulative and final-chunk-only on this API)."""
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
    if base is None:  # no chunk carried a candidate: let extract_candidate raise its way
        base = chunks[-1]
    else:
        base.candidates[0].content = types.Content(role="model", parts=parts)
        base.candidates[0].finish_reason = finish
    base.usage_metadata = usage_md
    if feedback is not None:
        base.prompt_feedback = feedback
    return base


class GeminiModel:
    """ChatModel for ``gemini:<model>``: a shared ``KeyPool``, ``rotate_with_retries``
    (hedge, ``ChatRequest.max_wait_s`` deadline), streaming with stall detection and
    per-attempt ledger rows."""

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
        warnings: list[str] = []
        state = {"config": self._config(request)}

        def downgrade_thinking(err: ModelError) -> bool:
            """Free-retry hook: model rejects ThinkingConfig → retry without it, once."""
            if not (self._thinking_ok and err.status == 400 and "thinking" in str(err).lower()):
                return False
            with self._lock:
                self._thinking_ok = False
            warnings.append(f"{self.model} rejected thinking config; retrying without it: {err}")
            state["config"] = self._config(request)
            return True

        budget = retry_budget_s(request.max_wait_s)
        deadline = time.monotonic() + budget  # mirrors rotate_with_retries' own deadline
        stats: dict[str, Any] = {}
        resp = rotate_with_retries(
            self.pool,
            lambda key: self._once(
                key, contents, self._attempt_config(state["config"], deadline),
                request, warnings,
            ),
            classify=classify_exception,
            max_attempts=self.max_attempts,
            max_total_s=budget,
            hedge=self.hedge,
            sleep=self._sleep,
            on_free_retry=downgrade_thinking,
            retry_after=_retry_after_s,
            label=f"gemini {self.model}",
            stats=stats,
            on_attempt=self._attempt_hook(),
            **({} if self.storm_attempts is None else {"storm_attempts": self.storm_attempts}),
        )
        # how hard the call was, next to which key served it (``_once``): ledger fields
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
        """The config for ONE attempt: its HTTP read timeout is the remaining retry
        budget, capped at ``self.timeout_s`` and floored at
        :data:`parts.SDK_TIMEOUT_FLOOR_S`.  A 20 s-budget turn used to hand the provider
        a 300 s socket — the dominant deadline overshoot (audit 2026-08-27)."""
        return clip_timeout(config, int(attempt_timeout_s(deadline, self.timeout_s) * 1000))

    def _attempt_hook(self) -> OnAttempt:
        """Per-round-trip observer: feeds the ledger's per-attempt sink when the metering
        layer installed one (``cost.instrument.MeteredChatModel``) — a DISCARDED round-trip
        that still cost money is booked there as an ``extra`` row, the only record of it (D84).
        Captured once per logical call so a hedge loser landing later, in its own thread,
        still reports through it."""
        sink = attempt_sink()

        def on_attempt(t: _Try, no: int, discarded: bool) -> None:
            if t.err is None:
                usage: Usage = t.result.usage
                outcome, error = "ok", ""
            else:
                usage = getattr(t.err, "usage", None) or Usage()
                outcome, error = t.outcome, str(t.err)
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
        client = _client_for(key, self.timeout_s, self._client_factory)

        if not get_settings().stream:   # C3D_STREAM=off: the buffered call
            with Stopwatch() as sw:
                resp = client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
        else:
            # the attempt budget (what _attempt_config put on the wire for the
            # buffered call) becomes an in-loop deadline; the wire timeout drops
            # to the stall ceiling, which under streaming bounds only the gap
            # between chunks.  A hung socket now costs <=STREAM_STALL_S of the
            # retry budget instead of all of it.
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
        usage = parse_usage(resp, self.model)  # BEFORE extract_candidate: its raises carry it
        usage.latency_ms = sw.ms
        usage.cost_usd = estimate_cost("gemini", self.model, usage)
        text, finish = extract_candidate(resp, usage)
        parsed: Any = None
        if request.response_schema is not None:
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
        elif finish in RETRYABLE_FINISH and not text.strip():
            raise ModelError(f"Gemini finish_reason={finish}", retryable=True, usage=usage)
        raw: dict[str, Any] = {
            "finish_reason": finish,
            "model_version": resp.model_version,
            "response_id": resp.response_id,
            "key": f"…{key[-4:]}",
        }
        if warnings:
            raw["warnings"] = list(warnings)
        return ChatResponse(text=text, parsed=parsed, finish_reason=finish, usage=usage, raw=raw)


# ===================================================================== gemini_image

DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
FALLBACK_IMAGE_MODEL = "gemini-2.5-flash-image"

#: requested pixel size → Gemini ``image_size`` token; a 512 request GENERATES a 1K
#: image (resized locally), so it is billed as one — ``_GENERATED_PX`` is the size
#: ``pricing.per_image_usd`` is asked for
_IMAGE_SIZE_TOKEN: dict[int, str] = {512: "1K", 1024: "1K", 2048: "2K", 4096: "4K"}
_GENERATED_PX: dict[str, int] = {"1K": 1024, "2K": 2048, "4K": 4096}


def _to_part(ref: Image.Image | Path | str) -> types.Part:
    if isinstance(ref, Image.Image):
        buf = io.BytesIO()
        ref.convert("RGB").save(buf, format="PNG")
        return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")
    data, mime = image_bytes(ImagePart(path=str(ref)))
    return types.Part.from_bytes(data=data, mime_type=mime)


def _is_model_missing(err: ModelError) -> bool:
    s = str(err).lower()
    return err.status == 404 or (err.status == 400 and ("not found" in s or "not supported" in s))


def _record(usage: Usage, n_images: int) -> None:
    """One ledger row per image batch (the image model is not a ChatModel, so
    ``models.registry`` cannot meter it).  ``record_call`` never raises."""
    record_call(usage, stage=Stage.TEXTURE, role=Role.IMAGE, label="image",
                backend="gemini-image", model=usage.model, n_calls=max(1, n_images))


class GeminiImageModel:
    """Text (+ reference images) → PIL images on the shared key pool and retry machine.
    ``model`` is the primary; ``fallback`` the second try once the primary is missing
    or fails retryably.  Not a ``ChatModel``: it meters itself (``_record``)."""

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
        hedge: int = 1,
        sleep: Callable[[float], None] = time.sleep,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        self.fallback = fallback if fallback and fallback != model else None
        #: no 503 hedge by default: an image is billed per image, so a hedge that
        #: lands twice pays for two of them (the chat models default to 2)
        self.hedge = max(1, int(hedge))
        keys = _keys_or_raise(keys, pool)
        self.pool = pool or shared_pool(keys)
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._primary_dead = False

    @property
    def id(self) -> str:
        return f"gemini-image:{self.model}"

    # ------------------------------------------------------------------ API
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
        full ``retry.RETRY_DEADLINE_S`` (1800 s)."""
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
        token = _IMAGE_SIZE_TOKEN.get(int(size), "1K")
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="1:1", image_size=token),
            seed=seed,
            http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)),
        )
        # the caller's budget clips the retry deadline, never extends it (the same
        # contract as GeminiModel.generate with ChatRequest.max_wait_s), and each
        # attempt's HTTP read timeout is the REMAINING budget, floored like
        # GeminiModel._attempt_config — a 60 s image budget no longer holds a
        # 180 s socket past its deadline.
        budget = retry_budget_s(max_wait_s)
        deadline = time.monotonic() + budget

        def _attempt_config() -> types.GenerateContentConfig:
            return clip_timeout(config, int(attempt_timeout_s(deadline, self.timeout_s) * 1000))

        return rotate_with_retries(
            self.pool,
            lambda key: self._call(key, model, contents, _attempt_config(), size, _GENERATED_PX[token]),
            classify=classify_exception,
            max_attempts=self.max_attempts,
            max_total_s=budget,
            hedge=self.hedge,
            sleep=self._sleep,
            retry_after=_retry_after_s,
            label=f"image {model}",
        )

    def _call(
        self, key: str, model: str, contents: list[types.Content], config: types.GenerateContentConfig,
        size: int, generated_px: int,
    ) -> tuple[list[Image.Image], Usage]:
        client = _client_for(key, self.timeout_s, self._client_factory)
        with Stopwatch() as sw:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
        usage = parse_usage(resp, model)  # parsed first: the raises below were billed too
        usage.backend = "gemini-image"
        usage.latency_ms = sw.ms
        cand = _guard_candidates(resp, usage, "image model")
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
        # token-priced cost floored by the per-image price of the size that was GENERATED
        usage.cost_usd = max(estimate_cost("gemini", model, usage),
                             per_image_usd("gemini", model, size=generated_px) * len(images))
        if int(size) not in (0, 1024) and images[0].size != (size, size):
            images = [im.resize((int(size), int(size)), Image.LANCZOS) for im in images]
        return images, usage
