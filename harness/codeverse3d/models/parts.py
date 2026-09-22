"""Small helpers shared by the provider backends (images, timing, usage)."""

from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any

from codeverse3d.contracts.chat import ImagePart
from codeverse3d.models.base import ModelError

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


def classify_sdk_exception(exc: BaseException, sdk: Any, label: str,
                           *, extra_retry: frozenset[int] = frozenset()) -> Any:
    """The openai/anthropic SDK exception ladder → ``ModelError``.

    anthropic-sdk-python is a fork of openai-python, so ``APIStatusError`` /
    ``APITimeoutError`` / ``APIConnectionError`` / ``APIError`` and ``.status_code`` /
    ``.message`` are the same names on both — this ladder was written out twice,
    differing only in the module, the label and anthropic's extra 529.

    The MESSAGE WORDING IS LOAD-BEARING: eval/bench/_infra.py string-matches "request timed
    out" and "connection error" to tell a provider outage from a model failure, so the
    f"{label} …" forms below must stay byte-identical to what each adapter emitted.
    """
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


#: floor of the per-attempt SDK / HTTP read timeout (gemini's too) derived from what is
#: left of the call's ``max_wait_s`` budget: a call that starts near its deadline still gets
#: ONE real attempt instead of an instant timeout.  Same 20 s as the judge's own
#: ``SAMPLE_MIN_WAIT_S``, so an attempt in flight overshoots the deadline by at most this
#: floor — never by the full read timeout (audit 2026-08-27, when that ceiling was 300 s).
SDK_TIMEOUT_FLOOR_S = 20.0


def retry_budget_s(max_wait_s: float | None) -> float:
    """The budget for ONE logical call: the caller clips ``RETRY_DEADLINE_S``, never extends it."""
    from codeverse3d.models.retry import RETRY_DEADLINE_S

    return RETRY_DEADLINE_S if max_wait_s is None else min(RETRY_DEADLINE_S, float(max_wait_s))


def attempt_timeout_s(deadline: float, ceiling: float) -> float:
    """The SDK timeout for ONE attempt: what is left of ``deadline``, capped at the
    client's ``timeout_s`` and floored at :data:`SDK_TIMEOUT_FLOOR_S`.

    The retry loop checks the deadline only BETWEEN attempts and both SDK clients are
    built once with a fixed ``timeout`` (600 s), so a judge told it had 20 s of budget
    left held a socket for 600 s (audit 2026-08-29).  The floor is what keeps a legitimate
    long completion (the 930 s plan budget) on its full ``timeout_s`` — this shortens an
    attempt, it never lengthens one.  Gemini's ``_attempt_config`` clips through it too.
    """
    return min(ceiling, max(SDK_TIMEOUT_FLOOR_S, deadline - time.monotonic()))


def retry_one_key(attempt: Any, *, label: str, classify: Any, attempts: int, sleep: Any,
                  max_wait_s: float | None = None) -> Any:
    """The SDK adapters' retry loop: ``rotate_with_retries`` over a fresh one-key pool with no
    cooldown, no storm patience and no hedge — so a 429 / 5xx / 529 / timeout is retried
    ``attempts`` times with the ≤ ``MAX_WAIT_S`` backoff, inside the request's ``max_wait_s``
    deadline (``None`` = ``RETRY_DEADLINE_S``), and a raised ``ModelError`` says how many
    round-trips it took.  ``label`` names the call in the log ("anthropic <model>")."""
    from codeverse3d.models.retry import KeyPool, rotate_with_retries

    return rotate_with_retries(KeyPool([label], cooldown_s=0.0), lambda _key: attempt(), classify=classify,
                               max_attempts=attempts, max_total_s=retry_budget_s(max_wait_s),
                               storm_attempts=0, hedge=1, sleep=sleep, label=label)
