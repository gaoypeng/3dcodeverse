"""Generic retry helpers: exponential backoff + full jitter, and the shared
key-rotation state machine used by the Gemini backends.

* :func:`with_retries` — plain bounded retry; no provider knowledge.
* :func:`rotate_with_retries` — the dead-key / 429-rotation / 503-hedge / backoff
  machine over a :class:`~codeverse.models.keypool.KeyPool`.  Provider specifics
  (exception classification, outcome mapping, retry-after extraction) are
  injected as hooks, so this module stays provider-neutral.

``sleep`` is injectable everywhere so unit tests run instantly.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from codeverse.models.keypool import MAX_WAIT_S, KeyPoolExhausted

#: the longest ONE logical call may spend being retried, waits and timeouts included.
#: The storm branch used to be bounded only in ATTEMPTS: 60 storm attempts x (a 300 s
#: read timeout + a <=5 s wait) is **5.1 hours** for a single call, though the docstring
#: claimed "~5 min".  Measured 2026-08-24: that, plus a wall-clock ceiling only checked
#: when a call is BILLED (a stalled call bills nothing), let a run with --max-minutes 90
#: reach 200 minutes during a flash outage.  Past this deadline the call gives up and the
#: cell is recorded infra_failed, which is re-runnable (`--redo-status infra_failed`).
#: This is the CEILING; a caller that cannot afford it passes a smaller ``max_total_s``
#: (``ChatRequest.max_wait_s``) — audit 2026-08-26 §5.1: 66 give-up spans of 900 s, up to
#: three in a row on one agent turn, were 30 % of a storm day's waiting.
RETRY_DEADLINE_S = 900.0

#: how many keys a retry is spread over once a call has met its first 503/529.
#: Measured 2026-08-26 §5.2: a failed 503 costs the 21-50 s round-trip the provider
#: holds before rejecting (not the <= 5 s sleep), storm streaks average 4.7 attempts,
#: and a 503 bills nothing — so racing the next attempt on two fresh keys is free.
DEFAULT_HEDGE = 2

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


def with_retries[T](
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


def rotate_with_retries[T](
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
    hedge: int = DEFAULT_HEDGE,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_free_retry: Callable[[ModelError], bool] | None = None,
    retry_after: Callable[[BaseException], float | None] | None = None,
    tokens_of: Callable[[T], int] | None = None,
    tokens_hint: int = 0,
    storm_gate: StormGate | None = None,
    label: str = "model",
    stats: dict[str, Any] | None = None,
) -> T:
    """Run ``call(key)`` against a rotating :class:`KeyPool` until it succeeds.

    The state machine (shared by ``GeminiModel`` / ``GeminiImageModel``):

    * success → ``pool.report(key, "ok", tokens=tokens_of(result))``; keys that
      looked dead during this call are benched (another key proved the request
      itself is fine) and the result is returned.
    * ``on_free_retry(err)`` returning ``True`` → retry at once, for free
      (no sleep, no budget) — e.g. a thinking-config downgrade.
    * ``outcome_of(err) == "dead"`` (key-scoped auth failure) → rotate at once
      for free; the key is only benched once a sibling key proves the request
      is fine.  Every key failing the same way raises without benching.
    * ``"429"`` → cool the key down (honouring ``retry_after``); rotation is
      FREE while an untried key remains (0.5 s courtesy pause), otherwise the
      429 counts against ``max_attempts`` with exponential backoff.
    * ``"503"``/529 → EXACTLY like the 429 path: the key is added to this call's
      failed set and rotation to an untried key is FREE (no sleep, no budget, no
      ``max_attempts``) for as long as ANY untried key remains — a 20-key pool
      rotates 19 times before it ever waits.  Measured 2026-08-26 with one tiny
      request per key in parallel, three rounds 20 s apart, during the day's
      "high demand" storm — gemini-3.7-flash answered on 15/22, 18/22 and 21/22
      keys while only 5, 4 and 1 keys said 503 at the same instant.  A 503 is per
      key at any moment, so the next key is the cure and a wait is the last
      resort.  Only once EVERY key in the pool has 503'd within this one logical
      call is it a
    * **capacity storm** (the whole pool is out of capacity, the backstop): that gets
      its own patience budget — up to ``storm_attempts`` waits with
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

    **Hedging** (``hedge``, default :data:`DEFAULT_HEDGE`; ``1`` disables).  From the
    FIRST 503/529 of a call onwards, every further attempt is issued on up to ``hedge``
    distinct keys at once and the first success wins.  Why: on the 2026-08-26 storm
    day the cost of a failed 503 was the **21-50 s round-trip** the provider held
    before rejecting, not the <= 5 s sleep (logged sleep was 13 % of the wait), and
    storm streaks averaged **4.7 attempts** — racing two keys cuts the expected number
    of rounds to ~1.7.  A 503 bills nothing, so while it is storming the hedge is free;
    the price is paid only when BOTH keys answer: the second success is discarded and
    that call's tokens are wasted (a few cents for a chat turn — which is why
    ``GeminiImageModel`` passes ``hedge=1``: an image is billed per image).
    Mechanics: the attempt's first key is acquired as usual (it may wait for a slot or
    a cooldown); the extra keys come from :meth:`KeyPool.try_acquire` and are simply
    skipped when no fresh key or ``max_in_flight`` slot is free *right now*, so a hedge
    never waits for a partner and every hedged request holds its own slot.  The calls
    run in a per-attempt ``ThreadPoolExecutor`` that is shut down without waiting: a
    loser keeps running until its own round-trip ends, then reports its outcome to
    the pool (a late success still reports its tokens) and releases its slot; the
    winner is returned the moment it lands.  When every hedged key fails, the attempt
    is ONE attempt for the storm / backoff accounting and the worst error decides the
    branch (a non-retryable error first, then a plain retryable one, a 429, a dead
    key, and a 503 last).

    ``tokens_hint`` is the estimated **prompt** tokens of the pending call
    (:func:`codeverse.models.tokens.request_tokens`); the pool reserves them in
    the per-key TPM bucket at ``acquire`` and the reservation is reconciled
    against the provider's real count on ``report``, so a 200 k-token judge call
    and a 2 k-token caption are scheduled differently.

    ``stats`` (optional, caller-owned dict) receives the call's telemetry on the way
    out — success or failure: ``attempts`` = round-trips issued (hedged siblings
    included; 1 = clean), ``hedged`` = hedged attempts, ``storm`` = storm waits.
    ``GeminiModel`` copies ``attempts`` into ``ChatResponse.raw`` and onto the raised
    ``ModelError`` so the cost ledger can say how hard each call was.
    """
    failed_keys: set[str] = set()  # keys that 429'd / 503'd or looked dead during this call
    dead_keys: set[str] = set()
    counts = {"attempts": 0, "hedged": 0, "storm": 0}

    def bench() -> None:
        # mark keys that failed with key-scoped errors dead — called once the call
        # got past the auth layer on some other key (success, 429, 5xx, bad JSON …)
        for k in dead_keys:
            pool.report(k, "dead")
        dead_keys.clear()

    def run_one(key: str) -> _Try:
        """One round-trip on ``key``: report its outcome to the pool and hand the slot
        back.  Runs inline for a single key and in a worker thread when hedged, so it
        touches none of this call's state and never raises a ``ModelError`` itself."""
        try:
            try:
                result = call(key)
            except Exception as exc:  # noqa: BLE001 - classification is delegated
                err = classify(exc)
                outcome = outcome_of(err)
                # a key that looks dead is reported "error" now and benched only once a
                # sibling proves the request itself is fine (bench())
                pool.report(
                    key,
                    "error" if outcome == "dead" else outcome,
                    reserved=tokens_hint,
                    retry_after_s=retry_after(exc) if (outcome == "429" and retry_after) else None,
                )
                return _Try(key, None, err, exc, outcome)
            pool.report(key, "ok", tokens=tokens_of(result) if tokens_of is not None else 0,
                        reserved=tokens_hint)
            return _Try(key, result, None, None, "ok")
        finally:
            pool.release()

    def run_attempt(keys: Sequence[str]) -> list[_Try]:
        """Issue ``call`` on every key; return the tries that completed up to and
        including the first success, in completion order.  Losers still in flight
        finish on their own (``run_one`` reports and releases for them)."""
        counts["attempts"] += len(keys)
        if len(keys) == 1:
            return [run_one(keys[0])]
        counts["hedged"] += 1
        executor = ThreadPoolExecutor(max_workers=len(keys), thread_name_prefix=f"hedge {label}")
        futures = [executor.submit(run_one, k) for k in keys]
        executor.shutdown(wait=False)  # no new work; the threads end with their round-trips
        done: list[_Try] = []
        for fut in as_completed(futures):
            t = fut.result()
            done.append(t)
            if t.err is None:
                for other in futures:
                    if not other.done():
                        other.add_done_callback(_discard_loser)
                break
        return done

    last_err: ModelError | None = None
    attempt = 0
    storm = 0
    hedging = False  # flips on the first 503/529; every later attempt is hedged
    deadline = monotonic() + max_total_s if max_total_s > 0 else float("inf")

    def out_of_time() -> bool:
        return monotonic() >= deadline

    def is_storm(err: ModelError) -> bool:
        return bool(err.retryable and err.status in (503, 529))

    try:
        while attempt < max_attempts:
            attempt += 1
            # never go back to a key that looked dead this call; throttled keys are
            # excluded while an untried one remains, else acquire() waits for a cooldown
            exclude = dead_keys | (failed_keys if len(failed_keys) < len(pool) else set())
            if storm_gate is not None:
                storm_gate.enter()
            try:
                keys = [pool.acquire(exclude=exclude, tokens_hint=tokens_hint)]
            except KeyPoolExhausted as exc:
                raise classify(exc) from exc
            while hedging and len(keys) < max(1, hedge):
                extra = pool.try_acquire(exclude=exclude | set(keys), tokens_hint=tokens_hint)
                if extra is None:
                    break  # no fresh key / slot right now: never wait for a hedge partner
                keys.append(extra)
            tries = run_attempt(keys)
            # bookkeeping for every failed try of this attempt: a hedged loser's dead
            # key or 503 counts exactly as a lone one would
            free = False
            for t in tries:
                if t.err is None:
                    continue
                if on_free_retry is not None and on_free_retry(t.err):
                    free = True
                elif t.outcome == "dead":
                    dead_keys.add(t.key)
                    failed_keys.add(t.key)
                if is_storm(t.err) and hedge > 1:
                    hedging = True
            won = next((t for t in tries if t.err is None), None)
            if won is not None:
                if storm_gate is not None:
                    storm_gate.ok()
                bench()
                return won.result
            # every key of this attempt failed: the worst error decides the branch
            worst = min(tries, key=_severity)
            err, exc, key = worst.err, worst.exc, worst.key
            assert err is not None
            last_err = err
            if free:
                attempt -= 1
                continue
            if worst.outcome == "dead":
                # key-scoped: move on at once (no budget, no sleep); the key is only
                # benched once another key proves the request itself is fine
                if len(dead_keys) < len(pool):
                    log.warning("%s key …%s looks dead (%s); rotating", label, key[-4:], err)
                    attempt -= 1
                    continue
                raise err from exc  # every key failed the same way: not the keys' fault
            if is_storm(err) and storm < storm_attempts and not out_of_time():
                failed_keys.update(t.key for t in tries)
                if len(failed_keys) < len(pool):
                    # a 503 is per key at any instant (see the docstring): the next
                    # UNTRIED key is the cure — free rotation, no sleep, no budget,
                    # no storm yet.  Same rule as the 429 path: while an untried key
                    # remains, rotation is free; it is only a storm once the WHOLE
                    # pool has 503'd inside this one call.
                    attempt -= 1
                    log.warning("%s key …%s 503 (%s); rotating to a fresh key (%d/%d tried%s)",
                                label, key[-4:], err, len(failed_keys), len(pool),
                                f", hedging x{hedge}" if hedging else "")
                    continue
                # capacity storm: EVERY key in the pool said 503 in this call, so
                # waiting is the only cure — paid from its own budget, not max_attempts
                storm += 1
                counts["storm"] = storm
                # jitter FIRST, cap LAST: capping before a >1 jitter factor let a
                # "<=5 s" wait land at 6.25 s in the wild (observed 2026-08-24).
                raw = base_delay * (2 ** min(storm, 8)) * (0.75 + 0.5 * random.random())
                delay = min(storm_max_delay, raw)
                if storm_gate is not None:
                    # tell every other worker as well: the next one to arrive parks at
                    # the gate instead of spending its own round-trip to find the storm
                    delay = max(delay, storm_gate.hit(retry_after(exc) if (retry_after and exc) else None))
                    log.warning("%s capacity storm %d/%d (%s); gate closed %.0fs",
                                label, storm, storm_attempts, err, delay)
                    storm_gate.enter()
                else:
                    log.warning("%s capacity storm %d/%d (%s); waiting %.0fs",
                                label, storm, storm_attempts, err, delay)
                    sleep(delay)
                attempt -= 1
                continue
            if worst.outcome == "429":
                failed_keys.update(t.key for t in tries if t.outcome == "429")
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
        assert last_err is not None
        bench()
        raise last_err
    finally:
        if stats is not None:
            stats.update(counts)


@dataclass(frozen=True)
class _Try:
    """The outcome of one round-trip on one key (``rotate_with_retries.run_one``)."""

    key: str
    result: Any
    err: ModelError | None
    exc: BaseException | None
    outcome: str


def _severity(t: _Try) -> int:
    """Rank a failed try so the worst one decides a hedged attempt's branch:
    non-retryable (0) < plain retryable (1) < 429 (2) < dead key (3) < 503/529 (4)."""
    err = t.err
    assert err is not None
    if t.outcome == "dead":
        return 3
    if err.retryable and err.status in (503, 529):
        return 4
    if not err.retryable:
        return 0
    return 2 if t.outcome == "429" else 1


def _discard_loser(fut: Future[_Try]) -> None:
    """A hedged sibling that finished after the winner: its pool report and slot
    release already happened in ``run_one``; here it is only logged."""
    try:
        t = fut.result()
    except BaseException as exc:  # noqa: BLE001 - a crashed loser must not crash the caller
        log.debug("hedge loser crashed: %s", exc)
        return
    log.debug("hedge loser …%s finished (%s); discarded", t.key[-4:], t.outcome)
