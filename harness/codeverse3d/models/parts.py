"""Small helpers shared by the provider backends (images, timing, usage)."""

from __future__ import annotations

import base64
import mimetypes
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.contracts.chat import ChatMessage, ImagePart, TextPart
from codeverse3d.contracts.common import Usage
from codeverse3d.models.base import ModelError


def image_bytes(part: ImagePart) -> tuple[bytes, str]:
    """Return ``(raw_bytes, mime)`` for an ``ImagePart``.
    Raises ``ModelError(retryable=False)`` when the part has no readable path."""
    if part.path:
        p = Path(part.path)
        if not p.is_file():
            raise ModelError(f"ImagePart.path does not exist: {part.path}")
        mime = part.mime
        if not mime or mime == "image/png" and p.suffix.lower() not in ("", ".png"):
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
        return p.read_bytes(), mime
    raise ModelError("ImagePart has no path")


def image_b64(part: ImagePart) -> tuple[str, str]:
    """``(base64_string, mime)`` for providers that want base64 (Anthropic, OpenAI data URLs)."""
    raw, mime = image_bytes(part)
    return base64.b64encode(raw).decode("ascii"), mime


def message_blocks(msg: ChatMessage, *, text: Callable[[str], Any], image: Callable[[ImagePart], Any]) -> list[Any]:
    """One message's parts as a provider's blocks: empty text is dropped, and a labelled image
    is preceded by an ``[image: <label>]`` text block (the one place that text is written)."""
    out: list[Any] = []
    for p in msg.parts:
        if isinstance(p, TextPart):
            if p.text:
                out.append(text(p.text))
        elif isinstance(p, ImagePart):
            if p.label:
                out.append(text(f"[image: {p.label}]"))
            out.append(image(p))
    return out


def default_timeout_s() -> float:
    """``Settings.model_timeout_s``."""
    return float(get_settings().model_timeout_s)


class SdkModel:
    """The shell the SDK adapters (anthropic, openai) share: the constructor, ``id`` and the
    lazily built client.  Each adapter owns ``_make_client`` and its own ``generate``."""

    provider = ""

    def __init__(
        self,
        model: str,
        *,
        timeout_s: float | None = None,
        max_attempts: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.timeout_s = default_timeout_s() if timeout_s is None else timeout_s
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._client = client
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"

    def client(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = self._make_client()
            return self._client

    def _make_client(self) -> Any:
        raise NotImplementedError


class Stopwatch:
    """Context-manager timer: ``with Stopwatch() as sw: ...`` leaves the elapsed milliseconds in ``sw.ms``."""

    def __enter__(self) -> Stopwatch:
        self._t0 = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)


def retryable_status(status: int | None) -> bool:
    """HTTP statuses worth retrying: 408, 409, 429 and every 5xx."""
    return status is not None and (status in (408, 409, 429) or status >= 500)


def classify_sdk_exception(exc: BaseException, sdk: Any, label: str) -> Any:
    """The openai/anthropic SDK exception ladder → ``ModelError``.

    The message wording is load-bearing: eval/bench/_infra.py string-matches "request timed
    out" and "connection error" to tell a provider outage from a model failure.
    """
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, sdk.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        return ModelError(f"{label} API error {status}: {exc.message}", retryable=retryable_status(status), status=status)
    if isinstance(exc, sdk.APITimeoutError):
        return ModelError(f"{label} request timed out: {exc}", retryable=True, status=408)
    if isinstance(exc, sdk.APIConnectionError):
        return ModelError(f"{label} connection error: {exc}", retryable=True)
    if isinstance(exc, sdk.APIError):
        return ModelError(f"{label} API error: {exc}", retryable=False)
    return ModelError(f"{label} unexpected error: {type(exc).__name__}: {exc}", retryable=False)


#: floor of the per-attempt timeout, so a call near its deadline still gets one real attempt
SDK_TIMEOUT_FLOOR_S = 20.0


def retry_budget_s(max_wait_s: float | None) -> float:
    """The budget for ONE logical call: the caller clips ``RETRY_DEADLINE_S``, never extends it."""
    from codeverse3d.models.retry import RETRY_DEADLINE_S

    return RETRY_DEADLINE_S if max_wait_s is None else min(RETRY_DEADLINE_S, float(max_wait_s))


def attempt_timeout_s(deadline: float, ceiling: float) -> float:
    """Per-attempt SDK timeout: time left to ``deadline``, capped at ``ceiling``, floored at
    :data:`SDK_TIMEOUT_FLOOR_S`.  The retry loop checks the deadline only between attempts."""
    return min(ceiling, max(SDK_TIMEOUT_FLOOR_S, deadline - time.monotonic()))


def retry_one_key(attempt: Any, *, label: str, classify: Any, attempts: int, sleep: Any,
                  max_wait_s: float | None = None) -> Any:
    """The SDK adapters' retry loop: ``rotate_with_retries`` over a one-key pool, no cooldown,
    storm patience or hedge, inside the request's ``max_wait_s`` deadline."""
    from codeverse3d.models.retry import KeyPool, rotate_with_retries

    billed = Usage()  # what failed attempts were billed for (ModelError.usage): joins the winner's row

    def once() -> Any:
        nonlocal billed
        try:
            return attempt()
        except ModelError as e:
            if e.usage is not None:
                billed = billed + e.usage
            raise

    resp = rotate_with_retries(KeyPool([label], cooldown_s=0.0), lambda _key: once(), classify=classify,
                               max_attempts=attempts, max_total_s=retry_budget_s(max_wait_s),
                               storm_attempts=0, hedge=1, sleep=sleep, label=label)
    if billed.input_tokens or billed.output_tokens:
        resp = resp.model_copy(update={"usage": resp.usage + billed})
    return resp
