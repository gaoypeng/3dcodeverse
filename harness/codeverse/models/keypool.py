"""Multi-key pool with per-key rate limiting, 429 cooldown and a health score.

Used by ``GeminiModel`` (22 keys on the owner's box) but provider-neutral.

* ``acquire()`` picks the next healthy key round-robin, honouring per-key
  RPM / TPM token buckets and 429 cool-downs; it blocks (bounded) when every
  key is throttled and raises ``KeyPoolExhausted`` after ``timeout_s``.
* ``report(key, outcome)`` feeds back ``ok | 429 | 5xx | error`` so the pool
  can cool a key down and adjust its health score.
* ``stats()`` exposes counters for logs / ``c3v doctor``.

Thread-safe; ``clock`` / ``sleep`` are injectable for deterministic tests.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["ok", "429", "5xx", "error"]


class KeyPoolExhausted(RuntimeError):
    """Every key is cooling down / throttled and the wait budget ran out."""


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

    def wait_for(self, n: float, now: float) -> float:
        """Seconds until ``n`` tokens are available (0 if already)."""
        self._refill(now)
        if self.tokens >= n:
            return 0.0
        return (n - self.tokens) / self.rate if self.rate > 0 else float("inf")


@dataclass
class _KeyState:
    key: str
    rpm: TokenBucket
    tpm: TokenBucket | None
    health: float = 1.0
    cooldown_until: float = 0.0
    n_ok: int = 0
    n_429: int = 0
    n_5xx: int = 0
    n_error: int = 0
    n_acquired: int = 0
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
        cooldown_s: float = 30.0,
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
        self._min_health = min_health
        self._lock = threading.Lock()
        self._rr = 0
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
        keys are throttled.  ``exclude`` skips keys that already failed this call."""
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
            wait = max(0.01, min(soonest - now, 5.0))
            if deadline is not None and self._clock() + wait > deadline:
                raise KeyPoolExhausted(
                    f"all {len(self._states)} keys throttled; waited {timeout_s}s"
                )
            self._sleep(wait)

    def report(
        self,
        key: str,
        outcome: Outcome,
        *,
        tokens: int = 0,
        retry_after_s: float | None = None,
    ) -> None:
        """Feed back the result of a call made with ``key``."""
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
                st.cooldown_until = max(st.cooldown_until, now + cool)
            elif outcome == "5xx":
                st.n_5xx += 1
                st.health *= 0.8
            else:
                st.n_error += 1
                st.health *= 0.9
            if tokens > 0 and st.tpm is not None:
                # charge actual usage (callers that pass a tokens_hint at acquire
                # should report tokens=0 to avoid double charging)
                st.tpm.try_take(float(tokens), now)

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
                    "acquired": s.n_acquired,
                    "ok": s.n_ok,
                    "429": s.n_429,
                    "5xx": s.n_5xx,
                    "error": s.n_error,
                }
                for s in self._states
            ]
            return {
                "n_keys": len(self._states),
                "n_cooling": sum(1 for s in self._states if s.cooldown_until > now),
                "acquired": sum(s.n_acquired for s in self._states),
                "ok": sum(s.n_ok for s in self._states),
                "429": sum(s.n_429 for s in self._states),
                "5xx": sum(s.n_5xx for s in self._states),
                "error": sum(s.n_error for s in self._states),
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
