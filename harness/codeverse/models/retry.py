"""Generic retry helpers: exponential backoff + full jitter, and the shared
key-rotation state machine used by the Gemini backends.

* :func:`with_retries` — plain bounded retry; no provider knowledge.
* :func:`rotate_with_retries` — the dead-key / 429-rotation / backoff machine
  over a :class:`~codeverse.models.keypool.KeyPool`.  Provider specifics
  (exception classification, outcome mapping, retry-after extraction) are
  injected as hooks, so this module stays provider-neutral.

``sleep`` is injectable everywhere so unit tests run instantly.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from codeverse.models.keypool import MAX_WAIT_S, KeyPoolExhausted

#: the longest ONE logical call may spend being retried, waits and timeouts included.
#: The storm branch used to be bounded only in ATTEMPTS: 60 storm attempts x (a 300 s
#: read timeout + a <=5 s wait) is **5.1 hours** for a single call, though the docstring
#: claimed "~5 min".  Measured 2026-08-24: that, plus a wall-clock ceiling only checked
#: when a call is BILLED (a stalled call bills nothing), let a run with --max-minutes 90
#: reach 200 minutes during a flash outage.  Past this deadline the call gives up and the
#: cell is recorded infra_failed, which is re-runnable (`--redo-status infra_failed`).
RETRY_DEADLINE_S = 900.0

if TYPE_CHECKING:  # pragma: no cover
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import KeyPool, Outcome
    from codeverse.models.storm import StormGate

log = logging.getLogger(__name__)

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
    max_delay: float = MAX_WAIT_S,
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


def rotate_with_retries(
    pool: KeyPool,
    call: Callable[[str], T],
    *,
    classify: Callable[[BaseException], ModelError],
    outcome_of: Callable[[ModelError], Outcome],
    max_attempts: int = 6,
    base_delay: float = 1.0,
    max_delay: float = MAX_WAIT_S,
    storm_attempts: int = 60,
    storm_max_delay: float = MAX_WAIT_S,
    max_total_s: float = RETRY_DEADLINE_S,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_free_retry: Callable[[ModelError], bool] | None = None,
    retry_after: Callable[[BaseException], float | None] | None = None,
    tokens_of: Callable[[T], int] | None = None,
    tokens_hint: int = 0,
    storm_gate: StormGate | None = None,
    label: str = "model",
) -> T:
    """Run ``call(key)`` against a rotating :class:`KeyPool` until it succeeds.

    The state machine (shared by ``GeminiModel`` / ``GeminiImageModel``):

    * success → ``pool.report(key, "ok", tokens=tokens_of(result))``; keys that
      looked dead during this call are benched (another key proved the request
      itself is fine) and the result is returned.
    * ``on_free_retry(err)`` returning ``True`` → retry at once, for free
      (no report, no sleep) — e.g. a thinking-config downgrade.
    * ``outcome_of(err) == "dead"`` (key-scoped auth failure) → rotate at once
      for free; the key is only benched once a sibling key proves the request
      is fine.  Every key failing the same way raises without benching.
    * ``"429"`` → cool the key down (honouring ``retry_after``); rotation is
      FREE while an untried key remains (0.5 s courtesy pause), otherwise the
      429 counts against ``max_attempts`` with exponential backoff.
    * a **capacity storm** (HTTP 503/529 — model-wide, key rotation cannot
      help) gets its own patience budget: up to ``storm_attempts`` waits with
      backoff capped at ``storm_max_delay`` = 5 s per wait (the house rule), so
      patience comes from the NUMBER of waits (60 x <=5 s ~ 5 min) rather than
      from long sleeps that do NOT consume ``max_attempts``.  Observed 2026-08-23: a
      multi-minute gemini-3.7-flash "high demand" outage killed 8 bench runs
      under the plain 6-attempt budget.  A ``storm_gate`` (see
      :mod:`codeverse.models.storm`) shares that discovery across the process:
      workers park at the gate instead of each spending a round-trip to learn
      the model is out of capacity, and one probe at a time reopens it.
    * other retryable errors → exponential backoff + jitter until
      ``max_attempts``; non-retryable errors raise immediately.

    ``tokens_hint`` is the estimated **prompt** tokens of the pending call
    (:func:`codeverse.models.tokens.request_tokens`); the pool reserves them in
    the per-key TPM bucket at ``acquire`` and the reservation is reconciled
    against the provider's real count on ``report``, so a 200 k-token judge call
    and a 2 k-token caption are scheduled differently.
    """
    failed_keys: set[str] = set()  # keys that 429'd or looked dead during this call
    dead_keys: set[str] = set()

    def bench() -> None:
        # mark keys that failed with key-scoped errors dead — called once the call
        # got past the auth layer on some other key (success, 429, 5xx, bad JSON …)
        for k in dead_keys:
            pool.report(k, "dead")
        dead_keys.clear()

    last_err: ModelError | None = None
    attempt = 0
    storm = 0
    deadline = monotonic() + max_total_s if max_total_s > 0 else float("inf")

    def out_of_time() -> bool:
        return monotonic() >= deadline

    while attempt < max_attempts:
        attempt += 1
        # never go back to a key that looked dead this call; throttled keys are
        # excluded while an untried one remains, else acquire() waits for a cooldown
        exclude = dead_keys | (failed_keys if len(failed_keys) < len(pool) else set())
        if storm_gate is not None:
            storm_gate.enter()
        try:
            key = pool.acquire(exclude=exclude, tokens_hint=tokens_hint)
        except KeyPoolExhausted as exc:
            raise classify(exc) from exc
        try:
            result = call(key)
            pool.report(key, "ok", tokens=tokens_of(result) if tokens_of is not None else 0,
                        reserved=tokens_hint)
            if storm_gate is not None:
                storm_gate.ok()
            bench()
            return result
        except Exception as exc:  # noqa: BLE001 - classification is delegated
            err = classify(exc)
            last_err = err
            if on_free_retry is not None and on_free_retry(err):
                attempt -= 1
                continue
            outcome = outcome_of(err)
            if outcome == "dead":
                # key-scoped: move on at once (no budget, no sleep); the key is only
                # benched once another key proves the request itself is fine
                dead_keys.add(key)
                failed_keys.add(key)
                pool.report(key, "error", reserved=tokens_hint)
                if len(dead_keys) < len(pool):
                    log.warning("%s key …%s looks dead (%s); rotating", label, key[-4:], err)
                    attempt -= 1
                    continue
                raise err from exc  # every key failed the same way: not the keys' fault
            pool.report(
                key,
                outcome,
                reserved=tokens_hint,
                retry_after_s=retry_after(exc) if (outcome == "429" and retry_after) else None,
            )
            if err.retryable and err.status in (503, 529) and storm < storm_attempts and not out_of_time():
                # capacity storm: model-wide, so waiting (on a rotated key) is the
                # only cure — paid from its own budget, not max_attempts
                storm += 1
                # jitter FIRST, cap LAST: capping before a >1 jitter factor let a
                # "<=5 s" wait land at 6.25 s in the wild (observed 2026-08-24).
                raw = base_delay * (2 ** min(storm, 8)) * (0.75 + 0.5 * random.random())
                delay = min(storm_max_delay, raw)
                if storm_gate is not None:
                    # tell every other worker as well: the next one to arrive parks at
                    # the gate instead of spending its own round-trip to find the storm
                    delay = max(delay, storm_gate.hit(retry_after(exc) if retry_after else None))
                    log.warning("%s capacity storm %d/%d (%s); gate closed %.0fs",
                                label, storm, storm_attempts, err, delay)
                    storm_gate.enter()
                else:
                    log.warning("%s capacity storm %d/%d (%s); waiting %.0fs",
                                label, storm, storm_attempts, err, delay)
                    sleep(delay)
                attempt -= 1
                continue
            if outcome == "429":
                failed_keys.add(key)
                if len(failed_keys) < len(pool):
                    # an untried key remains: rotation is free, only a courtesy pause
                    attempt -= 1
                    log.warning(
                        "%s key …%s throttled (%s); rotating to a fresh key", label, key[-4:], err
                    )
                    sleep(0.5)
                    continue
            if not err.retryable or attempt >= max_attempts:
                bench()
                raise err from exc
            if out_of_time():
                log.warning("%s giving up after %.0f s of retrying (%s)", label, max_total_s, err)
                raise err from exc
            delay = backoff_delay(attempt, base_delay=base_delay, max_delay=max_delay)
            log.warning(
                "%s attempt %d/%d failed (%s); retrying in %.1fs",
                label, attempt, max_attempts, err, delay,
            )
            sleep(delay)
        finally:
            pool.release()
    assert last_err is not None
    bench()
    raise last_err
