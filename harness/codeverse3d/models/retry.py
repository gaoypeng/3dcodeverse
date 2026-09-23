"""The scheduling machine under every model call (merged 2026-08-28, d512ccc):

1. ``KeyPool``: a multi-key pool with 429 cooldown, dead-key benching, a health score
   and the ``max_in_flight`` ceiling, which is machine-wide (``Slots``: flock'd lock
   files every process shares) — used by ``GeminiModel`` (22 keys on the owner's box)
   but provider-neutral;
2. ``rotate_with_retries``, the one retry loop (the pool, hedging, the ``RETRY_DEADLINE_S``
   deadline) — the SDK adapters run it over a one-key pool (``parts.retry_one_key``).

Every wait anywhere in here clips to ``MAX_WAIT_S``.

The pool:

* ``acquire()`` picks the next healthy key round-robin, honouring 429 cool-downs; it
  blocks (bounded) when every key is cooling and raises ``KeyPoolExhausted`` after
  ``timeout_s``.  ``max_in_flight`` additionally caps how many calls may be out at
  once on the whole machine, and :meth:`KeyPool.release` (a ``finally`` in
  ``rotate_with_retries``) hands the slot back.  ``try_acquire()`` is the never-waiting
  variant a hedged retry uses for its extra keys; ``session_key()`` picks a key for a
  vendor CLI session, which makes its own calls and holds no slot.  There is no RPM /
  TPM bucket: the ledgers since the api-agent went (2026-08-29 .. 09-22, 2 080 calls)
  peaked at 3.9 % of one key's TPM and 2.6 % of its RPM, so the buckets never engaged
  (docs/COST.md §19).
* ``report(key, outcome)`` feeds back ``ok | 429 | 5xx | error | dead | skip`` so the
  pool can cool a key down and adjust its health score.  ``skip`` is a content
  failure the key did not cause (bad JSON, empty candidates): health and counters are
  untouched.  ``dead`` is for key-scoped auth/permission failures (revoked / suspended /
  invalid key): the key is benched for ``dead_cooldown_s`` (default one hour) and
  re-probed once that elapses — a dead key must never keep failing its share of calls.
* ``stats()`` exposes counters for logs / ``3dcode doctor --live``: per-key health and
  cooldown, in-flight and peak in-flight.

Thread-safe; ``clock`` / ``sleep`` are injectable for deterministic tests.
"""

from __future__ import annotations

import fcntl
import logging
import os
import random
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

Outcome = Literal["ok", "429", "5xx", "error", "dead", "skip"]


class KeyPoolExhausted(RuntimeError):
    """Every key is cooling down (or every slot is busy) and the wait budget ran out."""


# message fragments of key-scoped failures (revoked / suspended / expired / disabled
# project); HTTP 401 / 403 are key-scoped regardless of wording
_DEAD_KEY_MARKERS = (
    "api_key_invalid",
    "api key not valid",
    "api key expired",
    "permission_denied",
    "unauthenticated",
    "suspended",
    "leaked",
    "service_disabled",
    "has not been used in project",
)


def is_dead_key_error(err: ModelError) -> bool:
    """True when ``err`` indicts the API key rather than the request, so the call
    should move to another key instead of failing."""
    if err.status in (401, 403):
        return True
    text = str(err).lower()
    return err.status == 400 and any(m in text for m in _DEAD_KEY_MARKERS)


def failure_outcome(err: ModelError) -> Outcome:
    """``KeyPool.report`` outcome for a failed call (``rotate_with_retries``' default
    ``outcome_of``).  Content-level failures (bad JSON, empty candidates) carry no status
    and are not the key's fault: ``skip`` leaves health / counters alone."""
    if err.status == 429:
        return "429"
    if (err.status or 0) >= 500:
        return "5xx"
    if is_dead_key_error(err):
        return "dead"
    return "error" if err.status else "skip"


MAX_WAIT_S = 3.0
"""The longest any single retry / cooldown wait may be (seconds).

House rule (owner, 2026-08-24, tightened 5 s -> 3 s on 2026-08-27): with 22 keys
there is always another key to try, so the harness rotates rather than sitting
out a long backoff.  Patience comes from the NUMBER of attempts, never from the
length of one sleep.  This is the single source for every wait — generic
backoff, storm sleeps and 429 cooldowns all clip to it.
"""

ACQUIRE_TIMEOUT_S = 120.0
"""Default total wait budget of one :meth:`KeyPool.acquire` (seconds).

Covers BOTH the ``max_in_flight`` slot wait and the key/cooldown wait; a caller
with a deadline passes something smaller (``retry.rotate_with_retries`` passes
``min(ACQUIRE_TIMEOUT_S, remaining budget)``).  ``None`` waits forever.
"""


@dataclass
class _KeyState:
    key: str
    health: float = 1.0
    cooldown_until: float = 0.0
    dead_until: float = 0.0
    n_ok: int = 0
    n_429: int = 0
    n_5xx: int = 0
    n_error: int = 0
    n_dead: int = 0
    n_acquired: int = 0
    health_ts: float = 0.0

    def recover(self, now: float, rate: float = 0.01) -> None:
        """Passive health recovery: +``rate`` per idle second, capped at 1.0."""
        if now > self.health_ts:
            self.health = min(1.0, self.health + (now - self.health_ts) * rate)
            self.health_ts = now


class Slots:
    """``n`` machine-wide in-flight slots: the docs/COST.md §20 knee, enforced across processes.

    One lock file per slot, ``<root>/NN.lock``, held with ``fcntl.flock`` while ONE model call
    is out.  Every process pointed at the same ``root`` draws from ``00 .. n-1``, so the
    machine never has more calls in flight than the largest ``n`` any of them runs with, and
    a process with a smaller cap only ever uses its own first ``n`` files.  flock locks an
    open file DESCRIPTION and every take opens its own, so two threads of one process
    exclude each other exactly as two processes do.  The kernel drops the lock when its
    holder dies — SIGKILL and the OOM killer included — so a dead process never strands a
    slot: the staleness problem that kept a machine-wide limiter deferred in §23 does not
    exist for flock (``proc.exclusive`` relies on the same property).
    """

    #: how long a waiter naps between two sweeps of the files while every slot is busy
    #: (seconds, jittered x0.5..1.5).  A model call takes seconds, so this bounds the extra
    #: latency a free slot costs a waiter, and a sweep is n non-blocking flock calls.
    POLL_S = 0.05

    def __init__(self, n: int, root: Path | str) -> None:
        if n < 1:
            raise ValueError(f"Slots needs n >= 1, got {n}")
        self.n = int(n)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, i: int) -> Path:
        return self.root / f"{i:02d}.lock"

    def try_take(self) -> int | None:
        """A held slot (its file descriptor), or ``None`` when all ``n`` are busy — never
        waits.  The sweep starts at a random slot, so a process with a small cap is not
        starved by a big one that always fills the low numbers first."""
        start = random.randrange(self.n)
        for step in range(self.n):
            fd = os.open(self._path((start + step) % self.n), os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            return fd
        return None

    def take(self, timeout_s: float | None) -> int:
        """Wait for a slot, at most ``timeout_s`` (``None`` = forever); its descriptor.
        Raises ``KeyPoolExhausted`` once the wait is over."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while (fd := self.try_take()) is None:
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise KeyPoolExhausted(f"all {self.n} in-flight slots in {self.root} busy; waited {timeout_s}s")
            nap = random.uniform(0.5, 1.5) * self.POLL_S
            time.sleep(nap if left is None else min(nap, left))
        return fd

    @staticmethod
    def give(fd: int) -> None:
        """Hand a slot back.  LOCK_UN first: a descriptor a forked child inherited must not
        keep the slot held after this process is done with it."""
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def busy(self) -> int:
        """How many slots somebody holds right now, machine-wide (``3dcode doctor``).  A
        shared probe per file, released at once: it never takes a slot from a caller."""
        n = 0
        for i in range(self.n):
            try:
                fd = os.open(self._path(i), os.O_RDONLY)
            except OSError:
                continue  # never created, so never taken
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                n += 1
            finally:
                os.close(fd)
        return n


class KeyPool:
    """Round-robin key pool with cooldowns and an in-flight ceiling.  See module docstring."""

    def __init__(
        self,
        keys: list[str] | tuple[str, ...],
        *,
        max_in_flight: int = 0,
        slots_dir: Path | str | None = None,
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
        # Machine-wide ceiling on concurrent model calls, independent of the caller's
        # thread pools: blender/node/chrome workers are CPU-bound and sized by cores,
        # while this is sized by the provider (docs/COST.md Part III).  0 = unlimited.
        self._max_in_flight = int(max_in_flight or 0)
        if self._max_in_flight and slots_dir is None:
            raise ValueError("max_in_flight needs slots_dir: the slots are lock files every process shares")
        #: the machine-wide slots (``None`` when uncapped); ``3dcode doctor`` reads it
        self.slots = Slots(self._max_in_flight, slots_dir) if self._max_in_flight else None
        self._held: list[int] = []  # slot descriptors of this process's in-flight calls
        now = clock()
        self._states = [_KeyState(key=k, health_ts=now) for k in uniq]
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
        exclude: set[str] | frozenset[str] | None = None,
        timeout_s: float | None = ACQUIRE_TIMEOUT_S,
    ) -> str:
        """Return the next usable key, blocking (bounded by ``timeout_s``) while all
        keys are cooling down.  ``exclude`` skips keys that already failed this call.
        Raises ``KeyPoolExhausted`` at once when no key can become available before
        the deadline (e.g. every key is dead) — availability only moves later.

        When ``max_in_flight`` is set the call also waits for a free machine-wide slot;
        :meth:`release` hands it back.  ``timeout_s`` bounds the slot wait AND the key
        wait together (``None`` = wait forever): the slot wait used to be unbounded, so
        a caller 20 s from its deadline could sit on the semaphore for minutes."""
        slot = None
        if self.slots is not None:
            t0 = self._clock()
            slot = self.slots.take(timeout_s)
            if timeout_s is not None:
                # the slot wait spent part of the budget; the key wait gets the rest
                timeout_s = max(0.0, timeout_s - (self._clock() - t0))
        try:
            key = self._acquire_key(exclude, timeout_s)
        except BaseException:
            if slot is not None:
                Slots.give(slot)
            raise
        self._hold(slot)
        return key

    def try_acquire(self, *, exclude: set[str] | frozenset[str] | None = None) -> str | None:
        """:meth:`acquire` that never waits: a key usable *right now* (and a free
        ``max_in_flight`` slot), else ``None`` — nothing is reserved or counted then.

        For the extra keys of a hedged retry (``rotate_with_retries(hedge=...)``): the
        primary request is already in flight, so a partner that is not free at once
        is not worth waiting for.  A returned key holds a slot like any other and
        must be :meth:`release`-d."""
        slot = None
        if self.slots is not None and (slot := self.slots.try_take()) is None:
            return None
        try:
            key = self._acquire_key(exclude, 0.0)
        except BaseException as exc:
            if slot is not None:
                Slots.give(slot)
            if isinstance(exc, KeyPoolExhausted):
                return None
            raise
        self._hold(slot)
        return key

    def session_key(
        self,
        *,
        exclude: set[str] | frozenset[str] | None = None,
        timeout_s: float | None = ACQUIRE_TIMEOUT_S,
    ) -> str:
        """A key for a session that makes its own calls — a vendor CLI: rotation, 429
        cooldowns and dead-key benching exactly like :meth:`acquire`, but no in-flight slot
        and nothing to release.  A session is minutes of agent time; a machine-wide slot
        held that long would let a few dozen sessions starve every planner and judge call
        on the box (docs/COST.md §23)."""
        return self._acquire_key(exclude, timeout_s)

    def _hold(self, slot: int | None) -> None:
        with self._lock:
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
            if slot is not None:
                self._held.append(slot)

    def _acquire_key(self, exclude: set[str] | frozenset[str] | None, timeout_s: float | None) -> str:
        deadline = None if timeout_s is None else self._clock() + timeout_s
        while True:
            with self._lock:
                now = self._clock()
                chosen = self._pick(now, exclude or ())
                if chosen is not None:
                    chosen.n_acquired += 1
                    return chosen.key
                soonest = min(
                    (max(now, s.cooldown_until) for s in self._states if s.key not in (exclude or ())),
                    default=float("inf"),
                )
            if soonest == float("inf"):
                raise KeyPoolExhausted("all keys excluded")
            if deadline is not None and soonest > deadline:
                raise KeyPoolExhausted(
                    f"all {len(self._states)} keys cooling or dead; the earliest becomes "
                    f"available in {soonest - now:.0f}s (> {timeout_s}s wait budget)"
                )
            wait = max(0.01, min(soonest - now, MAX_WAIT_S))
            if deadline is not None and self._clock() + wait > deadline:
                raise KeyPoolExhausted(f"all {len(self._states)} keys cooling; waited {timeout_s}s")
            self._sleep(wait)

    def release(self) -> None:
        """Mark one :meth:`acquire`-d call finished (call it in a ``finally``).

        Frees the in-flight gauge and one of this process's slots (they are
        interchangeable: the count is what the machine sees); outcome accounting is
        :meth:`report`, which may legitimately be called more than once for one call (a
        key that looked dead is reported again once a sibling proves the request fine).
        A stray ``release`` is a no-op."""
        with self._lock:
            if self._in_flight <= 0:
                return
            self._in_flight -= 1
            slot = self._held.pop() if self._held else None
        if slot is not None:
            Slots.give(slot)

    def report(self, key: str, outcome: Outcome, *, retry_after_s: float | None = None) -> None:
        """Feed back the result of a call made with ``key`` (``retry_after_s``: a 429's
        suggested delay, clipped to ``MAX_WAIT_S``)."""
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
            elif outcome != "skip":
                st.n_error += 1
                st.health *= 0.9

    def stats(self) -> dict[str, Any]:
        """Counters per key (keys are redacted to their last 4 chars)."""
        with self._lock:
            now = self._clock()
            per_key = [
                {
                    "key": f"…{s.key[-4:]}",
                    "health": round(s.health, 3),
                    "cooldown_s": round(max(0.0, s.cooldown_until - now), 1),
                    "acquired": s.n_acquired,
                    "ok": s.n_ok,
                    "429": s.n_429,
                    "5xx": s.n_5xx,
                    "error": s.n_error,
                    "dead": s.n_dead,
                }
                for s in self._states
            ]
            return {
                "n_keys": len(self._states),
                "n_cooling": sum(1 for s in self._states if s.cooldown_until > now),
                "n_dead": sum(1 for s in self._states if s.dead_until > now),
                "in_flight": self._in_flight,
                "peak_in_flight": self._peak_in_flight,
                "acquired": sum(s.n_acquired for s in self._states),
                "ok": sum(s.n_ok for s in self._states),
                "429": sum(s.n_429 for s in self._states),
                "5xx": sum(s.n_5xx for s in self._states),
                "error": sum(s.n_error for s in self._states),
                "dead": sum(s.n_dead for s in self._states),
                "keys": per_key,
            }

    # ----------------------------------------------------------------- private
    def _pick(self, now: float, exclude) -> _KeyState | None:
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
            if st.cooldown_until > now:
                continue
            if st.health >= self._min_health:
                self._rr = (idx + 1) % n
                return st
            if best is None or st.health > best.health:
                best, best_idx = st, idx
        if best is not None:
            self._rr = (best_idx + 1) % n
        return best


# ===================================================================== retry
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
    from codeverse3d.models.base import ModelError

log = logging.getLogger(__name__)


def cause_for(err: BaseException, exc: BaseException | None) -> BaseException | None:
    """The cause to raise ``err`` from — ``None`` when it would be ``err`` itself.

    Every adapter's ``classify()`` returns an already-classified ``ModelError``
    unchanged (gemini.py, openai.py, anthropic.py), so ``raise err from exc`` below is
    often ``raise e from e``, and CPython's ``raise ... from ...`` does NOT check for a
    cycle the way it does for ``__context__``.  The result — ``e.__cause__ is e`` — is a
    chain no naive walker survives: on 2026-08-27 it took ``eval/bench/_infra.py`` to a
    RecursionError inside ``run_cell``'s except handler and a compare matrix lost 11
    finished cells.  Dropping the self-cause is invisible otherwise: none of these
    raises sits inside an ``except`` block, and ``__suppress_context__`` is already set.
    """
    return None if exc is err else exc

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


def rotate_with_retries[T](
    pool: KeyPool,
    call: Callable[[str], T],
    *,
    classify: Callable[[BaseException], ModelError],
    outcome_of: Callable[[ModelError], Outcome] = failure_outcome,
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
    label: str = "model",
    stats: dict[str, Any] | None = None,
    on_attempt: OnAttempt | None = None,
) -> T:
    """Run ``call(key)`` against a rotating :class:`KeyPool` until it succeeds.

    The state machine (shared by ``GeminiModel`` / ``GeminiImageModel``):

    * success → ``pool.report(key, "ok")``; keys that looked dead during this call are
      benched (another key proved the request itself is fine) and the result is returned.
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
      backoff capped at ``storm_max_delay`` = ``MAX_WAIT_S`` (3 s) per wait (the house
      rule), so patience comes from the NUMBER of waits (60 x <=3 s) rather than from
      long sleeps that do NOT consume ``max_attempts`` — all inside the
      ``RETRY_DEADLINE_S`` / ``max_total_s`` deadline.  Observed 2026-08-23: a
      multi-minute gemini-3.7-flash "high demand" outage killed 8 bench runs
      under the plain 6-attempt budget.
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
    the pool and releases its slot; the winner is returned the moment it lands.  When
    every hedged key fails, the attempt is ONE attempt for the storm / backoff
    accounting and the worst error decides the branch (a non-retryable error first,
    then a plain retryable one, a 429, a dead key, and a 503 last).

    ``stats`` (optional, caller-owned dict) receives the call's telemetry on the way
    out — success or failure: ``attempts`` = round-trips issued (hedged siblings
    included; 1 = clean), ``hedged`` = hedged attempts, ``storm`` = storm waits.  A
    raised ``ModelError`` carries ``attempts`` itself (the cost ledger's error row);
    ``GeminiModel`` copies it into ``ChatResponse.raw`` on success.

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
                pool.report(key, "error" if outcome == "dead" else outcome,
                            retry_after_s=retry_after(exc) if (outcome == "429" and retry_after) else None)
                return _Try(key, None, err, exc, outcome)
            pool.report(key, "ok")
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

    def gave_up(err: ModelError) -> ModelError:
        log.warning("%s giving up after %.0f s of retrying (%s)", label, max_total_s, err)
        return err

    def is_storm(err: ModelError) -> bool:
        return bool(err.retryable and err.status in (503, 529))

    try:
        while attempt < max_attempts:
            if out_of_time() and last_err is not None:
                # checked wherever the loop can spend time, not only around the
                # sleeps: a free rotation must not out-live the caller's budget
                raise gave_up(last_err) from cause_for(last_err, last_exc)
            attempt += 1
            # never go back to a key that looked dead this call; throttled keys are
            # excluded while an untried one remains, else acquire() waits for a cooldown
            exclude = dead_keys | (failed_keys if len(failed_keys) < len(pool) else set())
            budget_left = None if deadline == float("inf") else max(0.0, deadline - monotonic())
            try:
                # the key/slot wait must fit the remaining budget, never outlive it
                keys = [pool.acquire(
                    exclude=exclude,
                    timeout_s=ACQUIRE_TIMEOUT_S if budget_left is None
                    else min(ACQUIRE_TIMEOUT_S, budget_left),
                )]
            except KeyPoolExhausted as exc:
                raise classify(exc) from exc
            while hedging and len(keys) < max(1, hedge):
                extra = pool.try_acquire(exclude=exclude | set(keys))
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
                        raise gave_up(err) from cause_for(err, exc)
                    log.warning("%s key …%s looks dead (%s); rotating", label, key[-4:], err)
                    attempt -= 1
                    continue
                raise err from cause_for(err, exc)  # every key failed the same way: not the keys' fault
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
                log.warning("%s capacity storm %d/%d (%s); waiting %.0fs",
                            label, storm, storm_attempts, err, delay)
                sleep(delay)
                attempt -= 1
                continue
            if worst.outcome == "429":
                failed_keys.update(t.key for t in tries if t.outcome == "429")
                if len(failed_keys) < len(pool):
                    if out_of_time():
                        raise gave_up(err) from cause_for(err, exc)
                    # an untried key remains: rotation is free, only a courtesy pause
                    attempt -= 1
                    log.warning(
                        "%s key …%s throttled (%s); rotating to a fresh key", label, key[-4:], err
                    )
                    sleep(clip(0.5))
                    continue
            if not err.retryable or attempt >= max_attempts:
                bench()
                raise err from cause_for(err, exc)
            if out_of_time():
                raise gave_up(err) from cause_for(err, exc)
            delay = backoff_delay(attempt, base_delay=base_delay, max_delay=max_delay)
            log.warning(
                "%s attempt %d/%d failed (%s); retrying in %.1fs",
                label, attempt, max_attempts, err, delay,
            )
            sleep(clip(delay))
        assert last_err is not None
        bench()
        raise last_err
    except BaseException as exc:
        if hasattr(exc, "attempts"):  # a ModelError: how hard the call tried
            exc.attempts = counts["attempts"]
        raise
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
