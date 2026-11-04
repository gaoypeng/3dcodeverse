"""The scheduling machine under every model call.

``KeyPool``: multi-key pool with 429 cooldown, dead-key benching, health scores and a
machine-wide ``max_in_flight`` ceiling (``Slots``, flock'd lock files).  ``rotate_with_retries``:
the one retry loop (pool, hedging, ``RETRY_DEADLINE_S``); SDK adapters run it over a one-key pool.
Every wait clips to ``MAX_WAIT_S``.  ``report`` outcomes: ok | 429 | 5xx | error | dead | skip
(``skip`` = a content failure the key did not cause).  Thread-safe; ``clock`` / ``sleep`` injectable.
"""

from __future__ import annotations

import fcntl
import hashlib
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


# message fragments of key-scoped failures; HTTP 401 / 403 are key-scoped regardless
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
    """True when ``err`` indicts the API key rather than the request."""
    if err.status in (401, 403):
        return True
    text = str(err).lower()
    return err.status == 400 and any(m in text for m in _DEAD_KEY_MARKERS)


def failure_outcome(err: ModelError) -> Outcome:
    """``KeyPool.report`` outcome for a failed call; status-less content failures are ``skip``."""
    if err.status == 429:
        return "429"
    if (err.status or 0) >= 500:
        return "5xx"
    if is_dead_key_error(err):
        return "dead"
    return "error" if err.status else "skip"


#: Longest single retry / cooldown wait (seconds): the pool rotates keys rather than sitting out
#: a long backoff, so patience comes from the number of attempts, never from one sleep.
MAX_WAIT_S = 3.0

#: Default total wait budget of one :meth:`KeyPool.acquire` (seconds): the in-flight slot wait plus
#: the key / cooldown wait; ``None`` waits forever.
ACQUIRE_TIMEOUT_S = 120.0


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
    """``n`` machine-wide in-flight slots: one lock file per slot, ``<root>/NN.lock``, held with
    ``fcntl.flock`` while one model call is out.  The kernel drops a dead holder's lock, so a
    killed process never strands a slot.
    """

    #: nap between sweeps while every slot is busy (seconds, jittered x0.5..1.5)
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
        """A held slot (its file descriptor), or ``None`` when all are busy; never waits.
        The sweep starts at a random slot so a small-cap process is not starved."""
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
        """Wait up to ``timeout_s`` (``None`` = forever) for a slot; raises ``KeyPoolExhausted``."""
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
        """Hand a slot back; LOCK_UN first so a forked child's inherited descriptor does not keep it held."""
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def busy(self) -> int:
        """How many slots are held machine-wide (``3dcode doctor``); never takes one."""
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
    """Round-robin key pool with cooldowns and an in-flight ceiling."""

    def __init__(
        self,
        keys: list[str] | tuple[str, ...],
        *,
        max_in_flight: int = 0,
        slots_dir: Path | str | None = None,
        cooldown_s: float = MAX_WAIT_S,
        dead_cooldown_s: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        start: int = 0,
    ) -> None:
        """``start``: the key the round-robin begins at (``shared_pool`` draws it at random)."""
        uniq = [k for i, k in enumerate(keys) if k and k not in keys[:i]]
        if not uniq:
            raise ValueError("KeyPool needs at least one non-empty key")
        self._clock = clock
        self._sleep = sleep
        self._cooldown_s = float(cooldown_s)
        self._dead_cooldown_s = float(dead_cooldown_s)
        self._lock = threading.Lock()
        self._rr = start % len(uniq)
        self._in_flight = 0
        self._peak_in_flight = 0
        # machine-wide ceiling on concurrent model calls; 0 = unlimited
        self._max_in_flight = int(max_in_flight or 0)
        if self._max_in_flight and slots_dir is None:
            raise ValueError("max_in_flight needs slots_dir: the slots are lock files every process shares")
        #: the machine-wide slots (``None`` when uncapped)
        self.slots = Slots(self._max_in_flight, slots_dir) if self._max_in_flight else None
        self._held: list[int] = []  # slot descriptors of this process's in-flight calls
        now = clock()
        self._states = [_KeyState(key=k, health_ts=now) for k in uniq]
        self._by_key = {s.key: s for s in self._states}

    # ------------------------------------------------------------------ public
    def __len__(self) -> int:
        return len(self._states)

    def acquire(
        self,
        *,
        exclude: set[str] | frozenset[str] | None = None,
        timeout_s: float | None = ACQUIRE_TIMEOUT_S,
    ) -> str:
        """Next usable key, blocking up to ``timeout_s`` (slot wait + key wait) while all keys cool.
        ``exclude`` skips keys that already failed this call; raises ``KeyPoolExhausted`` at once
        when no key can become available before the deadline."""
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
        """:meth:`acquire` that never waits: a key usable now, else ``None``.  Used for hedge
        partners; a returned key holds a slot and must be :meth:`release`-d."""
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

    def lease_session_key(self, leases: Path | str, *, timeout_s: float | None = ACQUIRE_TIMEOUT_S) -> tuple[str, int | None]:
        """A key for a vendor-CLI session: rotation, cooldowns and benching like :meth:`acquire`
        but no in-flight slot, and one live session per key machine-wide while keys suffice
        (a ``flock`` on ``<leases>/<key hash>.lock``).  Returns ``(key, lease)``; close the lease
        with :meth:`end_lease`.  When every ready key is in a session one is shared (lease ``None``)."""
        root = Path(leases)
        root.mkdir(parents=True, exist_ok=True)
        busy: set[str] = set()
        while True:
            try:
                key = self._acquire_key(busy, 0.0 if busy else timeout_s)
            except KeyPoolExhausted:
                if not busy:
                    raise
                return self._acquire_key(None, timeout_s), None   # every ready key is in a session
            fd = os.open(root / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.lock", os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                busy.add(key)
                with self._lock:
                    self._by_key[key].n_acquired -= 1   # passed over, not handed out
                continue
            return key, fd

    @staticmethod
    def end_lease(lease: int | None) -> None:
        if lease is not None:
            os.close(lease)

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
        """Mark one :meth:`acquire`-d call finished (call in a ``finally``); a stray release is a no-op."""
        with self._lock:
            if self._in_flight <= 0:
                return
            self._in_flight -= 1
            slot = self._held.pop() if self._held else None
        if slot is not None:
            Slots.give(slot)

    def report(self, key: str, outcome: Outcome, *, retry_after_s: float | None = None) -> None:
        """Feed back a call's result (``retry_after_s``: a 429's delay, clipped to ``MAX_WAIT_S``)."""
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
                # rotate to another key rather than sit out the provider's delay
                cool = min(cool, MAX_WAIT_S)
                st.cooldown_until = max(st.cooldown_until, now + cool)
            elif outcome == "5xx":
                st.n_5xx += 1
                st.health *= 0.8
            elif outcome == "dead":
                # key-scoped failure: bench until the long cooldown passes
                st.n_dead += 1
                st.health = 0.0
                st.dead_until = max(st.dead_until, now + self._dead_cooldown_s)
                st.cooldown_until = max(st.cooldown_until, st.dead_until)
            elif outcome != "skip":
                st.n_error += 1
                st.health *= 0.9

    def stats(self) -> dict[str, Any]:
        """Counters per key (keys redacted to their last 4 chars)."""
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
        """Round-robin: the first healthy ready key, else the healthiest ready key."""
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
            if st.health >= MIN_HEALTH:
                self._rr = (idx + 1) % n
                return st
            if best is None or st.health > best.health:
                best, best_idx = st, idx
        if best is not None:
            self._rr = (best_idx + 1) % n
        return best


# ===================================================================== retry
#: a key below this health is passed over while a healthier one is ready
MIN_HEALTH = 0.3
RETRY_DEADLINE_S = 1800.0   # ceiling of one logical call, retries included

#: keys a retry is raced on once a call has met its first 503/529
DEFAULT_HEDGE = 2

if TYPE_CHECKING:  # pragma: no cover
    from codeverse3d.models.base import ModelError

log = logging.getLogger(__name__)


def cause_for(err: BaseException, exc: BaseException | None) -> BaseException | None:
    """The cause to raise ``err`` from, ``None`` when it would be ``err`` itself (a
    self-cause chain recurses forever in naive walkers)."""
    return None if exc is err else exc

#: per-round-trip hook of :func:`rotate_with_retries`: ``(try, attempt_no, discarded)``; fired once per round-trip
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

    * success: report ok, bench keys that looked dead, return.
    * ``on_free_retry(err)`` true: retry at once, free (e.g. thinking-config downgrade).
    * dead key (auth failure): rotate free; benched only once a sibling key succeeds.
    * 429 / 503 / 529: the key joins the call's failed set; rotation to an untried key is
      free.  Only when every key has failed within this call is a 503 a capacity storm:
      up to ``storm_attempts`` waits (capped at ``storm_max_delay``) outside ``max_attempts``.
    * other retryable errors: exponential backoff + jitter up to ``max_attempts``;
      non-retryable raise at once.
    * everything is bounded by ``max_total_s``.

    Hedging (``hedge``; 1 disables): from the first 503/529 on, each attempt runs on up to
    ``hedge`` distinct keys at once and the first success wins.  Extra keys come from
    ``try_acquire`` (never waited for); losers finish on their own and report to the pool and
    ``on_attempt``.  When all fail, the worst error decides the branch (``_severity``).

    ``stats`` receives ``attempts``, ``hedged`` and ``storm`` counts.  ``on_attempt`` hears
    every round-trip once (exceptions from it are swallowed).
    """
    failed_keys: set[str] = set()  # keys that 429'd / 503'd or looked dead during this call
    dead_keys: set[str] = set()
    counts = {"attempts": 0, "hedged": 0, "storm": 0}
    finished: list[tuple[int, _Try]] = []  # (attempt_no, try): the on_attempt flush buffer
    winner: _Try | None = None

    def bench() -> None:
        # bench dead-looking keys once another key got past the auth layer
        for k in dead_keys:
            pool.report(k, "dead")
        dead_keys.clear()

    def run_one(key: str) -> _Try:
        """One round-trip on ``key``: report to the pool and release the slot.  Touches none of
        this call's state (it may run in a hedge thread)."""
        try:
            try:
                result = call(key)
            except Exception as exc:  # noqa: BLE001 - classification is delegated
                err = classify(exc)
                outcome = outcome_of(err)
                # a dead-looking key is benched later, by bench()
                pool.report(key, "error" if outcome == "dead" else outcome,
                            retry_after_s=retry_after(exc) if (outcome == "429" and retry_after) else None)
                return _Try(key, None, err, exc, outcome)
            pool.report(key, "ok")
            return _Try(key, result, None, None, "ok")
        finally:
            pool.release()

    def run_attempt(keys: Sequence[str]) -> list[_Try]:
        """Issue ``call`` on every key; return tries completed up to the first success."""
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
                        # fires once, at once if the sibling already landed
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
        """A sleep never outlives the budget."""
        return delay if deadline == float("inf") else min(delay, max(0.0, deadline - monotonic()))

    def gave_up(err: ModelError) -> ModelError:
        log.warning("%s giving up after %.0f s of retrying (%s)", label, max_total_s, err)
        return err

    def is_storm(err: ModelError) -> bool:
        return bool(err.retryable and err.status in (503, 529))

    try:
        while attempt < max_attempts:
            if out_of_time() and last_err is not None:
                # free rotations must not out-live the budget
                raise gave_up(last_err) from cause_for(last_err, last_exc)
            attempt += 1
            # skip dead-looking keys; skip throttled ones while an untried key remains
            exclude = dead_keys | (failed_keys if len(failed_keys) < len(pool) else set())
            budget_left = None if deadline == float("inf") else max(0.0, deadline - monotonic())
            try:
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
            worst = min(tries, key=_severity)
            err, exc, key = worst.err, worst.exc, worst.key
            assert err is not None
            last_err = err
            last_exc = exc
            if free:
                attempt -= 1
                continue
            if worst.outcome == "dead":
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
                    # a 503 is per key: while an untried key remains rotation is free
                    attempt -= 1
                    log.warning("%s key …%s 503 (%s); rotating to a fresh key (%d/%d tried%s)",
                                label, key[-4:], err, len(failed_keys), len(pool),
                                f", hedging x{hedge}" if hedging else "")
                    continue
                # capacity storm: every key said 503, so wait (own budget, not max_attempts)
                storm += 1
                counts["storm"] = storm
                # jitter first, cap last, so the wait never exceeds the cap
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
            # flush completed round-trips; a loser still in flight reports via _discard_loser
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
    """Rank a failed try: non-retryable 0 < retryable 1 < 429 2 < dead key 3 < 503/529 4."""
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
    """A hedge sibling that finished after the winner: handed to ``on_attempt`` as discarded."""
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
