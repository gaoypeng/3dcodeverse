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

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["ok", "429", "5xx", "error", "dead"]


class KeyPoolExhausted(RuntimeError):
    """Every key is cooling down / throttled and the wait budget ran out."""


MAX_WAIT_S = 5.0
"""The longest any single retry / cooldown wait may be (seconds).

House rule (owner, 2026-08-24): with 22 keys there is always another key to try,
so the harness rotates rather than sitting out a long backoff.  Patience comes
from the NUMBER of attempts, never from the length of one sleep.
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
        timeout_s: float | None = 120.0,
    ) -> str:
        """Return the next usable key, blocking (bounded by ``timeout_s``) while all
        keys are throttled.  ``exclude`` skips keys that already failed this call.
        Raises ``KeyPoolExhausted`` at once when no key can become available before
        the deadline (e.g. every key is dead) — availability only moves later.

        ``tokens_hint`` is the estimated prompt tokens of the pending call: they
        are reserved in the chosen key's TPM bucket now and reconciled by
        :meth:`report`.  When ``max_in_flight`` is set the call also waits for a
        free concurrency slot; :meth:`release` hands it back."""
        if self._slots is not None:
            self._slots.acquire()
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
            wait = max(0.01, min(soonest - now, 5.0))
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
