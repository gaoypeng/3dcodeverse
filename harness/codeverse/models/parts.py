"""Small helpers shared by the provider backends (images, timing, usage,
bounded per-call-id cache)."""

from __future__ import annotations

import base64
import mimetypes
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from codeverse.contracts.chat import ImagePart
from codeverse.models.base import ModelError

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def image_bytes(part: ImagePart) -> tuple[bytes, str]:
    """Return ``(raw_bytes, mime)`` for an ``ImagePart`` (path or base64).
    Raises ``ModelError(retryable=False)`` when the part carries no data."""
    if part.data_b64:
        try:
            return base64.b64decode(part.data_b64), part.mime or "image/png"
        except (ValueError, TypeError) as exc:
            raise ModelError(f"ImagePart.data_b64 is not valid base64: {exc}") from exc
    if part.path:
        p = Path(part.path)
        if not p.is_file():
            raise ModelError(f"ImagePart.path does not exist: {part.path}")
        mime = part.mime
        if not mime or mime == "image/png" and p.suffix.lower() not in ("", ".png"):
            mime = (
                _MIME_BY_SUFFIX.get(p.suffix.lower())
                or mimetypes.guess_type(p.name)[0]
                or "image/png"
            )
        return p.read_bytes(), mime
    raise ModelError("ImagePart has neither path nor data_b64")


def image_b64(part: ImagePart) -> tuple[str, str]:
    """``(base64_string, mime)`` for providers that want base64 (Anthropic, OpenAI data URLs)."""
    raw, mime = image_bytes(part)
    return base64.b64encode(raw).decode("ascii"), mime


class Stopwatch:
    """``with Stopwatch() as sw: ...; sw.ms``"""

    def __enter__(self) -> Stopwatch:
        self._t0 = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)


class BoundedCache[T]:
    """Thread-safe bounded LRU ``str -> T`` map for per-call-id provider state.

    Replaces ``gemini_convert._SignatureCache`` (thought signatures, ``bytes``)
    and ``anthropic_convert._ThinkingCache`` (thinking blocks, ``list[dict]``):
    both had the identical ``__init__``/``put``/``get`` — an ``OrderedDict``
    under a lock, ``put`` skipping falsy values, ``move_to_end`` then
    ``popitem(last=False)`` until under capacity — and differed only in value
    type and capacity.  ``get`` returns ``None`` on a miss; a caller that needs
    the old copy/empty-default shape wraps it: ``list(cache.get(k) or [])``.
    """

    def __init__(self, capacity: int) -> None:
        self._d: OrderedDict[str, T] = OrderedDict()
        self._cap = capacity
        self._lock = threading.Lock()

    def put(self, key: str, value: T | None) -> None:
        if not value:
            return
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._cap:
                self._d.popitem(last=False)

    def get(self, key: str) -> T | None:
        with self._lock:
            return self._d.get(key)


def classify_sdk_exception(exc: BaseException, sdk: Any, label: str,
                           *, extra_retry: frozenset[int] = frozenset()) -> Any:
    """The openai/anthropic SDK exception ladder → ``ModelError``.

    anthropic-sdk-python is a fork of openai-python, so ``APIStatusError`` /
    ``APITimeoutError`` / ``APIConnectionError`` / ``APIError`` and ``.status_code`` /
    ``.message`` are the same names on both — this ladder was written out twice,
    differing only in the module, the label and anthropic's extra 529.

    The MESSAGE WORDING IS LOAD-BEARING: bench/_infra.py string-matches "request timed
    out" and "connection error" to tell a provider outage from a model failure, so the
    f"{label} …" forms below must stay byte-identical to what each adapter emitted.
    """
    from codeverse.models.base import ModelError

    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, sdk.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        retry = status in ({408, 409, 429} | set(extra_retry)) or status >= 500
        return ModelError(f"{label} API error {status}: {exc.message}", retryable=retry, status=status)
    if isinstance(exc, sdk.APITimeoutError):
        return ModelError(f"{label} request timed out: {exc}", retryable=True, status=408)
    if isinstance(exc, sdk.APIConnectionError):
        return ModelError(f"{label} connection error: {exc}", retryable=True)
    if isinstance(exc, sdk.APIError):
        return ModelError(f"{label} API error: {exc}", retryable=False)
    return ModelError(f"{label} unexpected error: {type(exc).__name__}: {exc}", retryable=False)


def with_logged_retries(attempt: Any, *, label: str, model: str, attempts: int,
                        base_delay: float, max_delay: float, sleep: Any, log: Any) -> Any:
    """``with_retries`` plus the one log line both SDK adapters write.

    ChatRequest.max_wait_s is not honoured here: with_retries has no deadline, and its
    6 attempts x <= 5 s backoff bound one call to ~20 s of waiting plus the round-trips.
    """
    from codeverse.models.base import ModelError
    from codeverse.models.retry import with_retries

    def on_retry(n: int, exc: BaseException, delay: float) -> None:
        log.warning("%s %s attempt %d/%d failed (%s); retrying in %.1fs",
                    label, model, n, attempts, exc, delay)

    return with_retries(attempt, is_retryable=lambda e: isinstance(e, ModelError) and e.retryable,
                        attempts=attempts, base_delay=base_delay, max_delay=max_delay,
                        on_retry=on_retry, sleep=sleep)
