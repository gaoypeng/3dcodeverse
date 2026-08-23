"""Generic retry helper with exponential backoff + full jitter.

Provider modules use it for 429/5xx/timeouts; it is deliberately tiny and has
no provider knowledge.  ``sleep`` is injectable so unit tests run instantly.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

OnRetry = Callable[[int, BaseException, float], None]


def backoff_delay(
    attempt: int, *, base_delay: float, max_delay: float, jitter: bool = True
) -> float:
    """Delay before retry number ``attempt`` (1-based): ``base * 2**(attempt-1)`` capped,
    with full jitter (uniform in ``[delay/2, delay]``) when ``jitter`` is on."""
    raw = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    if not jitter:
        return raw
    return random.uniform(raw / 2.0, raw)


def with_retries(
    fn: Callable[[], T],
    *,
    is_retryable: Callable[[BaseException], bool],
    attempts: int = 6,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    on_retry: OnRetry | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: bool = True,
) -> T:
    """Call ``fn`` up to ``attempts`` times.

    Re-raises the last exception when it is not retryable (per ``is_retryable``)
    or when attempts are exhausted.  ``on_retry(attempt, exc, delay)`` is called
    before each sleep (attempt is the 1-based index of the attempt that failed).
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - classification is delegated
            last = exc
            if attempt >= attempts or not is_retryable(exc):
                raise
            delay = backoff_delay(
                attempt, base_delay=base_delay, max_delay=max_delay, jitter=jitter
            )
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            sleep(delay)
    # unreachable, but keeps type-checkers happy
    assert last is not None
    raise last
