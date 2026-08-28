"""Multi-key pool with per-key rate limiting, 429 cooldown and a health score.

Used by ``GeminiModel`` (22 keys on the owner's box) but provider-neutral.

* ``acquire()`` picks the next healthy key round-robin, honouring per-key
  RPM / TPM token buckets and 429 cool-downs; it blocks (bounded) when every
  key is throttled and raises ``KeyPoolExhausted`` after ``timeout_s``.
  ``tokens_hint`` reserves the pending call's estimated prompt tokens, so a
  200 k-token judge verdict and a 2 k-token caption are scheduled differently
  (``docs/COST.md`` Part III); ``max_in_flight`` additionally caps how many calls
  may be out at once, and :meth:`KeyPool.release` (a ``finally`` in
  ``rotate_with_retries``) hands the slot back.  ``try_acquire()`` is the
  never-waiting variant a hedged retry uses for its extra keys.
* ``report(key, outcome)`` feeds back ``ok | 429 | 5xx | error | dead`` so the
  pool can cool a key down and adjust its health score.  ``dead`` is for
  key-scoped auth/permission failures (revoked / suspended / invalid key): the
  key is benched for ``dead_cooldown_s`` (default one hour) and re-probed once
  that elapses — a dead key must never keep failing its share of calls.
  ``report(..., tokens=actual, reserved=hint)`` reconciles the reservation with
  the provider's real prompt-token count (refund or top-up).
* ``stats()`` exposes counters for logs / ``3dcv doctor --live``: per-key health
  and cooldown, pool RPM/TPM capacity and headroom, in-flight and peak in-flight.

Thread-safe; ``clock`` / ``sleep`` are injectable for deterministic tests.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from codeverse.contracts.chat import ChatRequest, ImagePart, TextPart, ToolResultPart

Outcome = Literal["ok", "429", "5xx", "error", "dead"]


class KeyPoolExhausted(RuntimeError):
    """Every key is cooling down / throttled and the wait budget ran out."""


MAX_WAIT_S = 3.0
"""The longest any single retry / cooldown wait may be (seconds).

House rule (owner, 2026-08-24, tightened 5 s -> 3 s on 2026-08-27): with 22 keys
there is always another key to try, so the harness rotates rather than sitting
out a long backoff.  Patience comes from the NUMBER of attempts, never from the
length of one sleep.  This is the single source for every wait — generic
backoff, storm sleeps, 429 cooldowns and the storm gate's park all clip to it.
"""

ACQUIRE_TIMEOUT_S = 120.0
"""Default total wait budget of one :meth:`KeyPool.acquire` (seconds).

Covers BOTH the ``max_in_flight`` slot wait and the key/cooldown wait; a caller
with a deadline passes something smaller (``retry.rotate_with_retries`` passes
``min(ACQUIRE_TIMEOUT_S, remaining budget)``).  ``None`` waits forever.
"""


@dataclass
class TokenBucket:
    """Classic token bucket: ``capacity`` tokens, refilled at ``rate`` per second."""

    capacity: float
    rate: float
    tokens: float = field(default=0.0)
    updated: float = field(default=0.0)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def _refill(self, now: float) -> None:
        if now > self.updated:
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now

    def try_take(self, n: float, now: float) -> bool:
        self._refill(now)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def charge(self, n: float, now: float) -> None:
        """Take ``n`` tokens unconditionally; ``n < 0`` gives tokens back.

        Unlike :meth:`try_take` the bucket is allowed to go negative, so an
        under-estimated reservation is paid off by the next refill instead of
        being silently forgiven.  Used to reconcile a TPM reservation with the
        provider's real prompt-token count."""
        self._refill(now)
        self.tokens = min(self.capacity, self.tokens - n)

    def wait_for(self, n: float, now: float) -> float:
        """Seconds until ``n`` tokens are available (0 if already)."""
        self._refill(now)
        if self.tokens >= n:
            return 0.0
        return (n - self.tokens) / self.rate if self.rate > 0 else float("inf")

    def headroom(self, now: float) -> float:
        """Fraction of capacity currently available (0.0 – 1.0)."""
        self._refill(now)
        return max(0.0, min(1.0, self.tokens / self.capacity)) if self.capacity > 0 else 1.0


@dataclass
class _KeyState:
    key: str
    rpm: TokenBucket
    tpm: TokenBucket | None
    health: float = 1.0
    cooldown_until: float = 0.0
    dead_until: float = 0.0
    n_ok: int = 0
    n_429: int = 0
    n_5xx: int = 0
    n_error: int = 0
    n_dead: int = 0
    n_acquired: int = 0
    tokens_used: int = 0
    last_used: float = 0.0
    health_ts: float = 0.0

    def recover(self, now: float, rate: float = 0.01) -> None:
        """Passive health recovery: +``rate`` per idle second, capped at 1.0."""
        if now > self.health_ts:
            self.health = min(1.0, self.health + (now - self.health_ts) * rate)
            self.health_ts = now

    def available_at(self, now: float, tokens_hint: int) -> float:
        """Earliest time this key can be used (``now`` if ready)."""
        t = max(now, self.cooldown_until)
        t = max(t, now + self.rpm.wait_for(1.0, now))
        if self.tpm is not None and tokens_hint > 0:
            t = max(t, now + self.tpm.wait_for(float(tokens_hint), now))
        return t


class KeyPool:
    """Round-robin key pool with per-key limiters.  See module docstring."""

    def __init__(
        self,
        keys: list[str] | tuple[str, ...],
        *,
        rpm_per_key: int = 900,
        tpm_per_key: int | None = None,
        max_in_flight: int = 0,
        cooldown_s: float = MAX_WAIT_S,
        dead_cooldown_s: float = 3600.0,
        min_health: float = 0.3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        uniq = [k for i, k in enumerate(keys) if k and k not in keys[:i]]
        if not uniq:
            raise ValueError("KeyPool needs at least one non-empty key")
        self._clock = clock
        self._sleep = sleep
        self._cooldown_s = float(cooldown_s)
        self._dead_cooldown_s = float(dead_cooldown_s)
        self._min_health = min_health
        self._lock = threading.Lock()
        self._rr = 0
        self._in_flight = 0
        self._peak_in_flight = 0
        # Process-wide ceiling on concurrent model calls, independent of the caller's
        # thread pools: blender/node/chrome workers are CPU-bound and sized by cores,
        # while this is sized by the provider (docs/COST.md Part III).  0 = unlimited.
        self._max_in_flight = int(max_in_flight or 0)
        self._slots = threading.BoundedSemaphore(self._max_in_flight) if self._max_in_flight else None
        now = clock()
        self._states: list[_KeyState] = []
        for k in uniq:
            rpm = TokenBucket(capacity=float(rpm_per_key), rate=rpm_per_key / 60.0, updated=now)
            tpm = None
            if tpm_per_key:
                tpm = TokenBucket(capacity=float(tpm_per_key), rate=tpm_per_key / 60.0, updated=now)
            self._states.append(_KeyState(key=k, rpm=rpm, tpm=tpm, health_ts=now))
        self._by_key = {s.key: s for s in self._states}

    # ------------------------------------------------------------------ public
    def __len__(self) -> int:
        return len(self._states)

    @property
    def keys(self) -> list[str]:
        return [s.key for s in self._states]

    def acquire(
        self,
        *,
        tokens_hint: int = 0,
        exclude: set[str] | frozenset[str] | None = None,
        timeout_s: float | None = ACQUIRE_TIMEOUT_S,
    ) -> str:
        """Return the next usable key, blocking (bounded by ``timeout_s``) while all
        keys are throttled.  ``exclude`` skips keys that already failed this call.
        Raises ``KeyPoolExhausted`` at once when no key can become available before
        the deadline (e.g. every key is dead) — availability only moves later.

        ``tokens_hint`` is the estimated prompt tokens of the pending call: they
        are reserved in the chosen key's TPM bucket now and reconciled by
        :meth:`report`.  When ``max_in_flight`` is set the call also waits for a
        free concurrency slot; :meth:`release` hands it back.  ``timeout_s`` bounds
        the slot wait AND the key wait together (``None`` = wait forever): the slot
        wait used to be unbounded, so a caller 20 s from its deadline could sit on
        the semaphore for minutes."""
        if self._slots is not None:
            t0 = self._clock()
            if not self._slots.acquire(timeout=timeout_s):
                raise KeyPoolExhausted(
                    f"all {self._max_in_flight} in-flight slots busy; waited {timeout_s}s"
                )
            if timeout_s is not None:
                # the slot wait spent part of the budget; the key wait gets the rest
                timeout_s = max(0.0, timeout_s - (self._clock() - t0))
        try:
            return self._acquire_key(tokens_hint, exclude, timeout_s)
        except BaseException:
            if self._slots is not None:
                self._slots.release()
            raise

    def try_acquire(
        self,
        *,
        tokens_hint: int = 0,
        exclude: set[str] | frozenset[str] | None = None,
    ) -> str | None:
        """:meth:`acquire` that never waits: a key usable *right now* (and a free
        ``max_in_flight`` slot), else ``None`` — nothing is reserved or counted then.

        For the extra keys of a hedged retry (``rotate_with_retries(hedge=...)``): the
        primary request is already in flight, so a partner that is not free at once
        is not worth waiting for.  A returned key holds a slot like any other and
        must be :meth:`release`-d."""
        if self._slots is not None and not self._slots.acquire(blocking=False):
            return None
        try:
            return self._acquire_key(tokens_hint, exclude, 0.0)
        except KeyPoolExhausted:
            if self._slots is not None:
                self._slots.release()
            return None
        except BaseException:
            if self._slots is not None:
                self._slots.release()
            raise

    def _acquire_key(
        self,
        tokens_hint: int,
        exclude: set[str] | frozenset[str] | None,
        timeout_s: float | None,
    ) -> str:
        deadline = None if timeout_s is None else self._clock() + timeout_s
        while True:
            with self._lock:
                now = self._clock()
                chosen = self._pick(now, tokens_hint, exclude or ())
                if chosen is not None:
                    chosen.rpm.try_take(1.0, now)
                    if chosen.tpm is not None and tokens_hint > 0:
                        chosen.tpm.try_take(float(tokens_hint), now)
                    chosen.n_acquired += 1
                    chosen.last_used = now
                    self._in_flight += 1
                    self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
                    return chosen.key
                soonest = min(
                    (
                        s.available_at(now, tokens_hint)
                        for s in self._states
                        if s.key not in (exclude or ())
                    ),
                    default=float("inf"),
                )
            if soonest == float("inf"):
                raise KeyPoolExhausted("all keys excluded")
            if deadline is not None and soonest > deadline:
                raise KeyPoolExhausted(
                    f"all {len(self._states)} keys throttled or dead; the earliest becomes "
                    f"available in {soonest - now:.0f}s (> {timeout_s}s wait budget)"
                )
            wait = max(0.01, min(soonest - now, MAX_WAIT_S))
            if deadline is not None and self._clock() + wait > deadline:
                raise KeyPoolExhausted(
                    f"all {len(self._states)} keys throttled; waited {timeout_s}s"
                )
            self._sleep(wait)

    def release(self) -> None:
        """Mark one :meth:`acquire`-d call finished (call it in a ``finally``).

        Frees the in-flight gauge and the ``max_in_flight`` slot; outcome
        accounting is :meth:`report`, which may legitimately be called more than
        once for one call (a key that looked dead is reported again once a
        sibling proves the request fine).  A stray ``release`` is a no-op."""
        with self._lock:
            if self._in_flight <= 0:
                return
            self._in_flight -= 1
        if self._slots is not None:
            self._slots.release()

    def report(
        self,
        key: str,
        outcome: Outcome,
        *,
        tokens: int = 0,
        reserved: int = 0,
        retry_after_s: float | None = None,
    ) -> None:
        """Feed back the result of a call made with ``key``.

        ``tokens`` is what the provider says the call really cost (prompt
        tokens); ``reserved`` is the ``tokens_hint`` :meth:`acquire` already took
        out of the TPM bucket for it.  The pool charges the *difference*, so an
        over-estimate is refunded and an under-estimate is paid off — pass both,
        or neither, and never the same tokens twice."""
        with self._lock:
            st = self._by_key.get(key)
            if st is None:
                return
            now = self._clock()
            st.recover(now)
            if outcome == "ok":
                st.n_ok += 1
                st.health = min(1.0, st.health + 0.2 * (1.0 - st.health))
            elif outcome == "429":
                st.n_429 += 1
                st.health *= 0.5
                cool = retry_after_s if retry_after_s and retry_after_s > 0 else self._cooldown_s
                # House rule (owner, 2026-08-24): no single wait exceeds MAX_WAIT_S — we
                # rotate to another key instead of sitting out a provider's suggested delay.
                cool = min(cool, MAX_WAIT_S)
                st.cooldown_until = max(st.cooldown_until, now + cool)
            elif outcome == "5xx":
                st.n_5xx += 1
                st.health *= 0.8
            elif outcome == "dead":
                # key-scoped failure: bench it; re-probed once the long cooldown passes
                st.n_dead += 1
                st.health = 0.0
                st.dead_until = max(st.dead_until, now + self._dead_cooldown_s)
                st.cooldown_until = max(st.cooldown_until, st.dead_until)
            else:
                st.n_error += 1
                st.health *= 0.9
            if st.tpm is not None and (tokens or reserved):
                # reconcile the reservation with reality; a call that never reached
                # the model (429 / capacity storm) reports tokens=0 and is refunded
                st.tpm.charge(float(tokens) - float(reserved), now)
            st.tokens_used += max(0, int(tokens))

    def stats(self) -> dict[str, Any]:
        """Counters per key (keys are redacted to their last 4 chars)."""
        with self._lock:
            now = self._clock()
            per_key = [
                {
                    "key": f"…{s.key[-4:]}",
                    "health": round(s.health, 3),
                    "cooldown_s": round(max(0.0, s.cooldown_until - now), 1),
                    "rpm_tokens": round(s.rpm.tokens, 1),
                    "rpm_headroom": round(s.rpm.headroom(now), 3),
                    "tpm_headroom": None if s.tpm is None else round(s.tpm.headroom(now), 3),
                    "tokens_used": s.tokens_used,
                    "acquired": s.n_acquired,
                    "ok": s.n_ok,
                    "429": s.n_429,
                    "5xx": s.n_5xx,
                    "error": s.n_error,
                    "dead": s.n_dead,
                }
                for s in self._states
            ]
            n = len(self._states)
            return {
                "n_keys": n,
                "n_cooling": sum(1 for s in self._states if s.cooldown_until > now),
                "n_dead": sum(1 for s in self._states if s.dead_until > now),
                "in_flight": self._in_flight,
                "peak_in_flight": self._peak_in_flight,
                "rpm_capacity": int(sum(s.rpm.capacity for s in self._states)),
                "tpm_capacity": int(sum(s.tpm.capacity for s in self._states if s.tpm)),
                "rpm_headroom": round(sum(s.rpm.headroom(now) for s in self._states) / n, 3),
                "tpm_headroom": (
                    None if self._states[0].tpm is None
                    else round(sum(s.tpm.headroom(now) for s in self._states if s.tpm) / n, 3)
                ),
                "tokens_used": sum(s.tokens_used for s in self._states),
                "acquired": sum(s.n_acquired for s in self._states),
                "ok": sum(s.n_ok for s in self._states),
                "429": sum(s.n_429 for s in self._states),
                "5xx": sum(s.n_5xx for s in self._states),
                "error": sum(s.n_error for s in self._states),
                "dead": sum(s.n_dead for s in self._states),
                "keys": per_key,
            }

    # ----------------------------------------------------------------- private
    def _pick(self, now: float, tokens_hint: int, exclude) -> _KeyState | None:
        """Round-robin scan; prefer the first healthy, ready key; otherwise the
        healthiest ready key.  Advances the RR cursor past the chosen key."""
        n = len(self._states)
        best: _KeyState | None = None
        best_idx = -1
        for step in range(n):
            idx = (self._rr + step) % n
            st = self._states[idx]
            if st.key in exclude:
                continue
            st.recover(now)
            if st.available_at(now, tokens_hint) > now:
                continue
            if st.health >= self._min_health:
                self._rr = (idx + 1) % n
                return st
            if best is None or st.health > best.health:
                best, best_idx = st, idx
        if best is not None:
            self._rr = (best_idx + 1) % n
        return best


# ===================================================================== storm
# (merged from codeverse/models/storm.py, 2026-08-28)
class StormGate:
    """Shared 503 back-pressure for one model.  See the module docstring."""

    def __init__(
        self,
        name: str = "model",
        *,
        base_delay: float = 1.0,
        max_wait_s: float = MAX_WAIT_S,
        probe_lease_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self._base = float(base_delay)
        self._max_wait = float(max_wait_s)
        self._probe_lease = float(probe_lease_s)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._storm = False
        self._closed_until = 0.0
        self._probe_until = 0.0
        self._streak = 0
        self.n_hits = 0
        self.n_probes = 0
        self.n_storms = 0
        self.parked_s = 0.0

    # ------------------------------------------------------------------ public
    @property
    def storming(self) -> bool:
        return self._storm

    def enter(self, deadline: float | None = None) -> float:
        """Block until this thread may issue a call; returns the seconds waited.

        ``deadline`` (a point on this gate's ``clock``) bounds the parking: each
        wait is clipped to it and once it passes ``enter`` returns even though the
        storm may still be on — the caller's own deadline machinery
        (``retry.rotate_with_retries``), not the gate, decides to give up."""
        t0 = self._clock()
        while True:
            with self._lock:
                now = self._clock()
                if not self._storm:
                    return now - t0
                if deadline is not None and now >= deadline:
                    return now - t0  # budget exhausted: hand control back
                if now < self._closed_until:
                    wait = min(self._max_wait, self._closed_until - now)
                elif now >= self._probe_until:
                    # the window elapsed and no probe is in flight: this thread is it
                    self._probe_until = now + self._probe_lease
                    self._n_probe_hit()
                    return now - t0
                else:
                    wait = min(self._max_wait, self._probe_until - now)
                wait = max(0.01, wait)
                if deadline is not None:
                    wait = min(wait, max(0.01, deadline - now))
                self.parked_s += wait
            self._sleep(wait)

    def hit(self, retry_after_s: float | None = None) -> float:
        """Record a capacity 503/529.  Returns how long the gate is now closed."""
        with self._lock:
            now = self._clock()
            if not self._storm:
                self._storm = True
                self.n_storms += 1
            self.n_hits += 1
            self._streak += 1
            wanted = retry_after_s if retry_after_s and retry_after_s > 0 else (
                self._base * (2 ** min(self._streak - 1, 8))
            )
            delay = min(self._max_wait, wanted)
            self._closed_until = max(self._closed_until, now + delay)
            self._probe_until = 0.0  # a fresh probe may go once the window elapses
            return self._closed_until - now

    def ok(self) -> None:
        """Record a success: the model is answering again, so open the gate."""
        if not self._storm:
            return
        with self._lock:
            self._storm = False
            self._streak = 0
            self._closed_until = 0.0
            self._probe_until = 0.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            return {
                "name": self.name,
                "storming": self._storm,
                "closed_for_s": round(max(0.0, self._closed_until - now), 1),
                "storms": self.n_storms,
                "hits": self.n_hits,
                "probes": self.n_probes,
                "parked_s": round(self.parked_s, 1),
            }

    # ----------------------------------------------------------------- private
    def _n_probe_hit(self) -> None:
        self.n_probes += 1


_gates: dict[str, StormGate] = {}
_gates_lock = threading.Lock()


def storm_gate(name: str) -> StormGate:
    """The process-wide gate for ``name`` (``"gemini:gemini-3.7-flash"``)."""
    with _gates_lock:
        gate = _gates.get(name)
        if gate is None:
            gate = StormGate(name)
            _gates[name] = gate
        return gate


def all_gates() -> list[StormGate]:
    """Every gate this process has created (for ``3dcv doctor --live``)."""
    with _gates_lock:
        return list(_gates.values())


# ===================================================================== retry
# (merged from codeverse/models/retry.py, 2026-08-28)
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
RETRY_DEADLINE_S = 1800.0   # owner 2026-08-27: generous time, zero timeouts (was 900)

#: how many keys a retry is spread over once a call has met its first 503/529.
#: Measured 2026-08-26 §5.2: a failed 503 costs the 21-50 s round-trip the provider
#: holds before rejecting (not the <= MAX_WAIT_S sleep), storm streaks average 4.7 attempts,
#: and a 503 bills nothing — so racing the next attempt on two fresh keys is free.
DEFAULT_HEDGE = 2

if TYPE_CHECKING:  # pragma: no cover
    from codeverse.models.base import ModelError
    from codeverse.models.retry import KeyPool, Outcome, StormGate

log = logging.getLogger(__name__)

T = TypeVar("T")

OnRetry = Callable[[int, BaseException, float], None]

#: per-round-trip hook of :func:`rotate_with_retries`: ``(try, attempt_no, discarded)``.
#: ``attempt_no`` is the 1-based issue order within the logical call; ``discarded``
#: is True for every round-trip that is not the winning one (hedge losers included).
#: Fired exactly once per round-trip: buffered tries flush when the call settles,
#: and a hedge loser still in flight reports the moment it lands (its own thread).
OnAttempt = Callable[["_Try", int, bool], None]


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
    on_attempt: OnAttempt | None = None,
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
      backoff capped at ``storm_max_delay`` = ``MAX_WAIT_S`` per wait (the house rule), so
      patience comes from the NUMBER of waits (60 x <=5 s ~ 5 min) rather than
      from long sleeps that do NOT consume ``max_attempts``.  Observed 2026-08-23: a
      multi-minute gemini-3.7-flash "high demand" outage killed 8 bench runs
      under the plain 6-attempt budget.  A ``storm_gate`` (see
      :mod:`codeverse.models.retry`) shares that discovery across the process:
      workers park at the gate instead of each spending a round-trip to learn
      the model is out of capacity, and one probe at a time reopens it.
    * other retryable errors → exponential backoff + jitter until
      ``max_attempts``; non-retryable errors raise immediately.

    **Hedging** (``hedge``, default :data:`DEFAULT_HEDGE`; ``1`` disables).  From the
    FIRST 503/529 of a call onwards, every further attempt is issued on up to ``hedge``
    distinct keys at once and the first success wins.  Why: on the 2026-08-26 storm
    day the cost of a failed 503 was the **21-50 s round-trip** the provider held
    before rejecting, not the <= MAX_WAIT_S sleep (logged sleep was 13 % of the wait), and
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
    (:func:`codeverse.models.retry.request_tokens`); the pool reserves them in
    the per-key TPM bucket at ``acquire`` and the reservation is reconciled
    against the provider's real count on ``report``, so a 200 k-token judge call
    and a 2 k-token caption are scheduled differently.

    ``stats`` (optional, caller-owned dict) receives the call's telemetry on the way
    out — success or failure: ``attempts`` = round-trips issued (hedged siblings
    included; 1 = clean), ``hedged`` = hedged attempts, ``storm`` = storm waits.
    ``GeminiModel`` copies ``attempts`` into ``ChatResponse.raw`` and onto the raised
    ``ModelError`` so the cost ledger can say how hard each call was.

    ``on_attempt`` (optional) hears every round-trip exactly once — see
    :data:`OnAttempt`; the cost layer uses it to write per-attempt ledger rows so a
    hedge loser's and a charged-but-invalid reply's tokens stop vanishing.
    Exceptions from the hook are swallowed: accounting must never break a call.
    """
    failed_keys: set[str] = set()  # keys that 429'd / 503'd or looked dead during this call
    dead_keys: set[str] = set()
    counts = {"attempts": 0, "hedged": 0, "storm": 0}
    finished: list[tuple[int, _Try]] = []  # (attempt_no, try): the on_attempt flush buffer
    winner: _Try | None = None

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
                # a charged-but-invalid reply (bad JSON with real usage) DID consume
                # its prompt tokens: report them so the pool does not refund a spent
                # reservation (a 42k-token invalid reply used to hand the key 42k TPM
                # back).  Failures billed nothing (429 / 503 / transport) carry an
                # empty ``ModelError.usage`` and are refunded exactly as before.
                consumed = getattr(err, "usage", None)
                pool.report(
                    key,
                    "error" if outcome == "dead" else outcome,
                    tokens=int(consumed.input_tokens) if consumed is not None else 0,
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
        finish on their own (``run_one`` reports and releases for them, and the
        ``on_attempt`` hook hears their tokens when they land)."""
        first_no = counts["attempts"] + 1
        counts["attempts"] += len(keys)
        if len(keys) == 1:
            t = run_one(keys[0])
            finished.append((first_no, t))
            return [t]
        counts["hedged"] += 1
        executor = ThreadPoolExecutor(max_workers=len(keys), thread_name_prefix=f"hedge {label}")
        futures = [executor.submit(run_one, k) for k in keys]
        nos = {id(f): first_no + i for i, f in enumerate(futures)}
        executor.shutdown(wait=False)  # no new work; the threads end with their round-trips
        done: list[_Try] = []
        consumed_futs: set[int] = set()
        for fut in as_completed(futures):
            consumed_futs.add(id(fut))
            t = fut.result()
            finished.append((nos[id(fut)], t))
            done.append(t)
            if t.err is None:
                for other in futures:
                    if id(other) not in consumed_futs:
                        # fires at once when the sibling already landed — either way
                        # exactly once, so the hook still hears a loser's tokens
                        other.add_done_callback(
                            partial(_discard_loser, on_attempt=on_attempt,
                                    attempt_no=nos[id(other)])
                        )
                break
        return done

    last_err: ModelError | None = None
    last_exc: BaseException | None = None
    attempt = 0
    storm = 0
    hedging = False  # flips on the first 503/529; every later attempt is hedged
    deadline = monotonic() + max_total_s if max_total_s > 0 else float("inf")

    def out_of_time() -> bool:
        return monotonic() >= deadline

    def clip(delay: float) -> float:
        """A sleep never outlives the budget: waking only to give up helps nobody."""
        return delay if deadline == float("inf") else min(delay, max(0.0, deadline - monotonic()))

    def is_storm(err: ModelError) -> bool:
        return bool(err.retryable and err.status in (503, 529))

    try:
        while attempt < max_attempts:
            if out_of_time() and last_err is not None:
                # checked wherever the loop can spend time, not only around the
                # sleeps: a free rotation must not out-live the caller's budget
                log.warning("%s giving up after %.0f s of retrying (%s)",
                            label, max_total_s, last_err)
                raise last_err from last_exc
            attempt += 1
            # never go back to a key that looked dead this call; throttled keys are
            # excluded while an untried one remains, else acquire() waits for a cooldown
            exclude = dead_keys | (failed_keys if len(failed_keys) < len(pool) else set())
            if storm_gate is not None:
                storm_gate.enter(None if deadline == float("inf") else deadline)
                if out_of_time():
                    # the gate held us past the budget.  The check at the top of the loop
                    # cannot cover this: a worker parked at a gate ANOTHER thread closed
                    # has no last_err, and would go on to spend a full round-trip.
                    log.warning("%s budget of %.0f s spent waiting at the storm gate", label, max_total_s)
                    raise (last_err or classify(TimeoutError(
                        f"{label}: retry budget spent waiting for capacity"))) from last_exc
            budget_left = None if deadline == float("inf") else max(0.0, deadline - monotonic())
            try:
                # the key/slot wait must fit the remaining budget, never outlive it
                keys = [pool.acquire(
                    exclude=exclude, tokens_hint=tokens_hint,
                    timeout_s=ACQUIRE_TIMEOUT_S if budget_left is None
                    else min(ACQUIRE_TIMEOUT_S, budget_left),
                )]
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
                winner = won
                if storm_gate is not None:
                    storm_gate.ok()
                bench()
                return won.result
            # every key of this attempt failed: the worst error decides the branch
            worst = min(tries, key=_severity)
            err, exc, key = worst.err, worst.exc, worst.key
            assert err is not None
            last_err = err
            last_exc = exc
            if free:
                attempt -= 1
                continue
            if worst.outcome == "dead":
                # key-scoped: move on at once (no budget, no sleep); the key is only
                # benched once another key proves the request itself is fine
                if len(dead_keys) < len(pool):
                    if out_of_time():
                        log.warning("%s giving up after %.0f s of retrying (%s)",
                                    label, max_total_s, err)
                        raise err from exc
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
                delay = clip(min(storm_max_delay, raw))
                if storm_gate is not None:
                    # tell every other worker as well: the next one to arrive parks at
                    # the gate instead of spending its own round-trip to find the storm
                    delay = max(delay, storm_gate.hit(retry_after(exc) if (retry_after and exc) else None))
                    log.warning("%s capacity storm %d/%d (%s); gate closed %.0fs",
                                label, storm, storm_attempts, err, delay)
                    storm_gate.enter(None if deadline == float("inf") else deadline)
                else:
                    log.warning("%s capacity storm %d/%d (%s); waiting %.0fs",
                                label, storm, storm_attempts, err, delay)
                    sleep(delay)
                attempt -= 1
                continue
            if worst.outcome == "429":
                failed_keys.update(t.key for t in tries if t.outcome == "429")
                if len(failed_keys) < len(pool):
                    if out_of_time():
                        log.warning("%s giving up after %.0f s of retrying (%s)",
                                    label, max_total_s, err)
                        raise err from exc
                    # an untried key remains: rotation is free, only a courtesy pause
                    attempt -= 1
                    log.warning(
                        "%s key …%s throttled (%s); rotating to a fresh key", label, key[-4:], err
                    )
                    sleep(clip(0.5))
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
            sleep(clip(delay))
        assert last_err is not None
        bench()
        raise last_err
    finally:
        if stats is not None:
            stats.update(counts)
        if on_attempt is not None:
            # flush every completed round-trip now that the winner is known; a hedge
            # loser still in flight reports itself when it lands (_discard_loser)
            for no, t in finished:
                try:
                    on_attempt(t, no, t is not winner)
                except Exception as hook_exc:  # noqa: BLE001 - accounting is best-effort
                    log.debug("on_attempt hook failed for round-trip %d: %s", no, hook_exc)


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


def _discard_loser(
    fut: Future[_Try], on_attempt: OnAttempt | None = None, attempt_no: int = 0
) -> None:
    """A hedged sibling that finished after the winner: its pool report and slot
    release already happened in ``run_one``; here it is logged and handed to the
    ``on_attempt`` hook — always as discarded, a late success included — so its
    paid tokens reach the cost ledger at all."""
    try:
        t = fut.result()
    except BaseException as exc:  # noqa: BLE001 - a crashed loser must not crash the caller
        log.debug("hedge loser crashed: %s", exc)
        return
    if on_attempt is not None:
        try:
            on_attempt(t, attempt_no, True)
        except Exception as hook_exc:  # noqa: BLE001 - accounting is best-effort
            log.debug("on_attempt hook failed for hedge loser: %s", hook_exc)
    log.debug("hedge loser …%s finished (%s); discarded", t.key[-4:], t.outcome)


# ===================================================================== tokens
# (merged from codeverse/models/tokens.py, 2026-08-28)
#: longest edge we assume for an image part whose size we do not measure; the
#: harness caps judge payloads at ``Settings.judge.max_px`` = 1024 (docs/COST.md §3)
DEFAULT_IMAGE_PX = 1024


def request_parts(request: ChatRequest) -> tuple[list[str], int]:
    """``(text blocks, number of image parts)`` of a request, system prompt and
    tool schemas included — everything the provider will count as prompt."""
    blocks: list[str] = []
    images = 0
    if request.system:
        blocks.append(request.system)
    for tool in request.tools or ():
        blocks.append(f"{tool.name}{tool.description}{tool.parameters}")
    if request.response_schema is not None:
        blocks.append(str(request.response_schema))
    for msg in request.messages:
        for part in msg.parts:
            if isinstance(part, TextPart):
                blocks.append(part.text)
            elif isinstance(part, ImagePart):
                images += 1
            elif isinstance(part, ToolResultPart):
                blocks.append(part.content)
                images += len(part.images)
            else:  # ToolCallPart
                blocks.append(f"{part.name}{part.arguments}")
    return blocks, images


def request_tokens(request: ChatRequest, *, model_id: str = "", image_px: int = DEFAULT_IMAGE_PX) -> int:
    """Estimated **input** tokens of ``request`` (never negative)."""
    from codeverse.cost.guard import estimate_call

    blocks, images = request_parts(request)
    est = estimate_call(model_id or "gemini:unknown", prompt=blocks, n_images=images, image_px=image_px)
    return max(0, est.input_tokens)
