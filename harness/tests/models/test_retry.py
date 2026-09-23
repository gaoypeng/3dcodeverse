"""backoff_delay / rotate_with_retries."""

from __future__ import annotations

import random
import threading
import time

import pytest

from codeverse3d.models.base import ModelError
from codeverse3d.models.parts import SDK_TIMEOUT_FLOOR_S
from codeverse3d.models.retry import (
    MAX_WAIT_S,
    RETRY_DEADLINE_S,
    KeyPool,
    KeyPoolExhausted,
    Outcome,
    backoff_delay,
    rotate_with_retries,
)


def test_backoff_grows_and_caps():
    d = [backoff_delay(i, base_delay=1.0, max_delay=8.0, jitter=False) for i in range(1, 7)]
    assert d == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    j = backoff_delay(3, base_delay=1.0, max_delay=8.0)
    assert 2.0 <= j <= 4.0


# --------------------------------------------------------------------------- rotate_with_retries
def _classify(exc: BaseException) -> ModelError:
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, KeyPoolExhausted):
        return ModelError(f"pool exhausted: {exc}", retryable=False, status=429)
    return ModelError(str(exc), retryable=False)


def _outcome(err: ModelError) -> Outcome:
    if err.status == 429:
        return "429"
    if (err.status or 0) >= 500:
        return "5xx"
    if err.status in (401, 403):
        return "dead"
    return "error" if err.status else "ok"


def _503(e: BaseException) -> ModelError:
    """Read every exception as a retryable 503 — the storm/hedge tests' classifier."""
    return ModelError(str(e), retryable=True, status=503)


def _pool(n: int = 3, **kw) -> KeyPool:
    """A pool of ``n`` keys."""
    return KeyPool([f"k{i}" for i in range(1, n + 1)], **kw)


def _rotate(pool, call, **kw):
    kw.setdefault("classify", _classify)
    kw.setdefault("outcome_of", _outcome)
    kw.setdefault("sleep", lambda s: None)
    return rotate_with_retries(pool, call, **kw)


def _rotate503(pool, call, **kw):
    """``_rotate`` under the 503 classifier + ``"5xx"`` outcome every storm test wants."""
    kw.setdefault("outcome_of", lambda e: "5xx")
    return _rotate(pool, call, classify=_503, **kw)


class Clock:
    """Fake monotonic clock whose sleeps advance recorded time."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t
        self.naps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.naps.append(d)
        self.t += d


def _boom(msg: str = "503 high demand"):
    """A ``call`` that always raises ``msg``."""

    def call(_key):
        raise RuntimeError(msg)

    return call


def test_rotate_every_key_dead_raises_without_benching():
    pool = KeyPool(["k1", "k2", "k3"])
    calls: list[str] = []

    def call(key: str) -> str:
        calls.append(key)
        raise ModelError("suspended", retryable=False, status=403)

    with pytest.raises(ModelError) as ei:
        _rotate(pool, call)
    assert ei.value.status == 403 and calls == ["k1", "k2", "k3"]
    assert pool.stats()["dead"] == 0  # the request, not the keys, is suspect


# --------------------------------------------------------------------------- 503 storms
@pytest.mark.parametrize(("hedge", "want_calls", "want_naps"), [(1, {9}, 7), (2, {9, 10}, 4)])
def test_503_storm_has_its_own_patience_budget(hedge, want_calls, want_naps):
    """A 503 storm gets a separate budget; hedging reduces its waits."""
    lock = threading.Lock()
    calls = {"n": 0}
    naps: list[float] = []

    def call(key):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if n <= 8:  # longer than max_attempts=3 could survive
            raise RuntimeError("503 storm")
        return "ok"

    out = _rotate503(
        _pool(2),
        call,
        max_attempts=3,
        base_delay=0.01,
        storm_attempts=10,
        storm_max_delay=0.05,
        sleep=naps.append,
        hedge=hedge,
    )
    assert out == "ok" and calls["n"] in want_calls
    # the first 503 rotates to k2 for free (no nap); k2's 503 leaves no untried key in the
    # 2-key pool, so that one is the storm
    assert len(naps) == want_naps and all(d <= 0.05 for d in naps)  # the cap is the cap


def test_storm_waits_never_exceed_the_house_limit_at_production_defaults(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 1.0)
    naps: list[float] = []
    with pytest.raises(ModelError):
        _rotate503(_pool(2), _boom(), max_attempts=2, storm_attempts=12, sleep=naps.append)
    assert naps and max(naps) == MAX_WAIT_S


def test_one_call_cannot_retry_for_hours():
    """The wall-clock deadline bounds even a long 503 storm."""
    clock = Clock()

    def call(_key):
        # gemini.py _attempt_config: min(300 s, remaining), floored at SDK_TIMEOUT_FLOOR_S
        clock.t += min(300.0, max(SDK_TIMEOUT_FLOOR_S, RETRY_DEADLINE_S - clock.t))
        raise RuntimeError("503 high demand")

    with pytest.raises(ModelError):
        # hedge=1: the fake clock is serial, so two hedged 300 s calls would add 600 s
        _rotate503(
            _pool(2),
            call,
            max_attempts=6,
            storm_attempts=60,
            sleep=clock.sleep,
            monotonic=clock,
            hedge=1,
        )
    # bounded by the clock: the ONLY legal overshoot is the one floored attempt that was
    # already in flight when the deadline passed
    assert clock.t <= RETRY_DEADLINE_S + SDK_TIMEOUT_FLOOR_S, (
        f"one call burned {clock.t / 3600:.2f} h; the deadline is {RETRY_DEADLINE_S / 60:.0f} min"
    )
    assert clock.t < 5 * 3600, "this is the 5.1-hour regression"


def test_the_deadline_does_not_cut_a_call_that_is_making_progress():
    """A slow-but-succeeding call must not be killed by the retry deadline."""
    clock = Clock()
    calls = {"n": 0}

    def call(_key):
        calls["n"] += 1
        clock.t += 280.0  # slow, but under the per-attempt timeout
        if calls["n"] < 3:
            raise RuntimeError("503 high demand")
        return "ok"

    # hedge=1: serial fake clock and call counter (see the hedge tests for the raced form)
    out = _rotate503(
        _pool(2),
        call,
        max_attempts=6,
        storm_attempts=60,
        sleep=clock.sleep,
        monotonic=clock,
        hedge=1,
    )
    assert out == "ok" and calls["n"] == 3


# --------------------------------------------------------------------------- hedging (audit 2026-08-26 §5.2)
def _wait_idle(pool, timeout: float = 5.0) -> None:
    """Block until every hedged loser has finished and released its slot."""
    t_end = time.monotonic() + timeout
    while pool.stats()["in_flight"] and time.monotonic() < t_end:
        time.sleep(0.005)
    assert pool.stats()["in_flight"] == 0


def test_after_the_first_503_the_next_attempt_is_hedged_and_the_first_success_wins(tmp_path):
    """After a 503, fresh keys race and the first success wins."""
    pool = KeyPool(["k1", "k2", "k3", "k4"], max_in_flight=8, slots_dir=tmp_path)
    release_k2 = threading.Event()
    lock = threading.Lock()
    calls: list[str] = []
    naps: list[float] = []
    stats: dict = {}

    def call(key):
        with lock:
            calls.append(key)
        if key == "k1":
            raise RuntimeError("503 high demand")
        if key == "k2":  # the slow loser: still held by the provider when k3 answers
            assert release_k2.wait(5.0)
            raise RuntimeError("503 high demand")
        return f"ok:{key}"

    out = rotate_with_retries(
        pool,
        call,
        classify=_503,
        outcome_of=lambda e: "5xx",
        max_attempts=3,
        storm_attempts=10,
        sleep=naps.append,
        stats=stats,
    )
    assert out == "ok:k3"
    assert naps == [], "a hedged retry sleeps for nothing"
    assert calls[0] == "k1" and sorted(calls[1:]) == ["k2", "k3"], (
        "two distinct fresh keys, k1 excluded"
    )
    assert stats == {"attempts": 3, "hedged": 1, "storm": 0}
    st = pool.stats()
    assert st["in_flight"] == 1 and st["peak_in_flight"] == 2, (
        "the loser still holds its max_in_flight slot"
    )
    assert st["ok"] == 1 and st["5xx"] == 1
    release_k2.set()
    _wait_idle(pool)
    st = pool.stats()
    assert st["5xx"] == 2 and st["in_flight"] == 0, "the loser's 503 is reported when it lands"


def test_the_worst_error_decides_a_hedged_attempt():
    """A nonretryable hedge error outranks a sibling 503."""
    pool = _pool(3)
    stats: dict = {}

    def call(key):
        if key == "k3":
            raise ModelError("bad request", retryable=False, status=400)
        raise ModelError("503 high demand", retryable=True, status=503)

    with pytest.raises(ModelError) as ei:
        _rotate(pool, call, max_attempts=6, storm_attempts=10, stats=stats)
    assert ei.value.status == 400 and stats == {"attempts": 3, "hedged": 1, "storm": 0}


# --------------------------------------------------------------------------- deadline threading (2026-08-27)
def test_free_429_rotation_cannot_outlive_the_deadline():
    """Free key rotation still consumes wall-clock budget."""
    clock = Clock()
    pool = _pool(10, cooldown_s=0.0, clock=clock, sleep=clock.sleep)
    calls: list[str] = []

    def call(key):
        calls.append(key)
        raise ModelError("quota", retryable=True, status=429)

    with pytest.raises(ModelError) as ei:
        _rotate(pool, call, max_attempts=6, max_total_s=2.0, sleep=clock.sleep, monotonic=clock)
    assert ei.value.status == 429
    assert clock.t <= 2.5, f"free rotation outlived the deadline: {clock.t}"
    assert len(calls) <= 5, "ten keys would all have been tried before"


# --------------------------------------------------------------------------- on_attempt hook (2026-08-27)
def test_on_attempt_hears_every_round_trip_and_a_late_hedge_loser():
    """The attempt hook reports winners, failures, and late hedge losers."""
    pool = _pool(4)
    release_k2 = threading.Event()
    lock = threading.Lock()
    recs: list[tuple[int, str, str, bool]] = []  # (attempt_no, key, outcome, discarded)

    def on_attempt(t, no, discarded):
        with lock:
            recs.append((no, t.key, t.outcome, discarded))

    def call(key):
        if key == "k1":
            raise ModelError("503 high demand", retryable=True, status=503)
        if key == "k2":
            assert release_k2.wait(5.0)
            return "late:k2"
        return "ok:k3"

    out = _rotate(pool, call, max_attempts=3, storm_attempts=10, on_attempt=on_attempt)
    assert out == "ok:k3"
    with lock:
        flushed = list(recs)
    assert (1, "k1", "5xx", True) in flushed
    winner = next(r for r in flushed if not r[3])
    assert winner[1] == "k3" and winner[2] == "ok"
    release_k2.set()
    t_end = time.monotonic() + 5.0
    while time.monotonic() < t_end:
        with lock:
            if len(recs) == 3:
                break
        time.sleep(0.005)
    with lock:
        loser = next(r for r in recs if r[1] == "k2")
    assert loser[3] is True and loser[2] == "ok", "a loser that succeeds late is still discarded"
    _wait_idle(pool)


def test_a_broken_on_attempt_hook_never_breaks_the_call():
    pool = _pool(1)

    def hook(t, no, discarded):
        raise RuntimeError("accounting on fire")

    assert _rotate(pool, lambda key: "ok", on_attempt=hook) == "ok"

