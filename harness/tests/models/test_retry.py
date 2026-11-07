"""backoff_delay / rotate_with_retries."""

from __future__ import annotations

import random
import threading

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
    """Read every exception as a retryable 503 — the storm tests' classifier."""
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
    assert pool.acquire() == "k1"  # nothing was benched: the request, not the keys, is suspect


def test_one_call_cannot_retry_for_hours():
    """The wall-clock deadline bounds even a long 503 storm."""
    clock = Clock()

    def call(_key):
        # gemini.py _attempt_config: min(300 s, remaining), floored at SDK_TIMEOUT_FLOOR_S
        clock.t += min(300.0, max(SDK_TIMEOUT_FLOOR_S, RETRY_DEADLINE_S - clock.t))
        raise RuntimeError("503 high demand")

    with pytest.raises(ModelError):
        _rotate503(
            _pool(2),
            call,
            max_attempts=6,
            storm_attempts=60,
            sleep=clock.sleep,
            monotonic=clock,
        )
    # bounded by the clock: the ONLY legal overshoot is the one floored attempt that was
    # already in flight when the deadline passed
    assert clock.t <= RETRY_DEADLINE_S + SDK_TIMEOUT_FLOOR_S, "the 5.1-hour regression"


# --------------------------------------------------------------------------- 503 storms
def test_a_503_rotates_to_a_fresh_key_without_sleeping_and_counts_every_attempt():
    """A 503 is per key: while an untried key remains, rotation is free (sequential, one key at a time)."""
    pool = _pool(3)
    naps: list[float] = []
    calls: list[str] = []
    stats: dict = {}

    def call(key):
        calls.append(key)
        if key != "k3":
            raise RuntimeError("503 high demand")
        return f"ok:{key}"

    out = _rotate503(pool, call, max_attempts=3, storm_attempts=10, sleep=naps.append, stats=stats)
    assert out == "ok:k3" and calls == ["k1", "k2", "k3"] and naps == []
    assert stats == {"attempts": 3, "storm": 0}
    st = pool.stats()
    assert st["ok"] == 1 and st["5xx"] == 2


def test_503_storm_has_its_own_patience_budget():
    """A 503 storm gets a separate budget."""
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
    )
    assert out == "ok" and calls["n"] == 9
    # the first 503 rotates to k2 for free (no nap); k2's 503 leaves no untried key in the
    # 2-key pool, so that one is the storm
    assert len(naps) == 7 and all(d <= 0.05 for d in naps)  # the cap is the cap


def test_storm_waits_never_exceed_the_house_limit_at_production_defaults(monkeypatch):
    monkeypatch.setattr(random, "random", lambda: 1.0)
    naps: list[float] = []
    with pytest.raises(ModelError):
        _rotate503(_pool(2), _boom(), max_attempts=2, storm_attempts=12, sleep=naps.append)
    assert naps and max(naps) == MAX_WAIT_S


def test_backoff_grows_and_caps():
    d = [backoff_delay(i, base_delay=1.0, max_delay=8.0, jitter=False) for i in range(1, 7)]
    assert d == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    j = backoff_delay(3, base_delay=1.0, max_delay=8.0)
    assert 2.0 <= j <= 4.0


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

    out = _rotate503(
        _pool(2),
        call,
        max_attempts=6,
        storm_attempts=60,
        sleep=clock.sleep,
        monotonic=clock,
    )
    assert out == "ok" and calls["n"] == 3


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


def test_one_key_adapters_join_every_billed_attempt_on_success_and_on_failure():
    """``retry_one_key``: failed-but-billed attempts join the call's usage, whether it ends well or not."""
    from codeverse3d.contracts.chat import ChatResponse
    from codeverse3d.contracts.common import Usage
    from codeverse3d.models.parts import retry_one_key

    def run(replies):
        it = iter(replies)

        def attempt():
            r = next(it)
            if isinstance(r, ChatResponse):
                return r
            raise ModelError("bad reply", retryable=True, usage=Usage(input_tokens=100))

        return retry_one_key(attempt, label="t", classify=lambda e: e, attempts=3, sleep=lambda s: None)

    won = run([ModelError("x"), ChatResponse(text="ok", usage=Usage(input_tokens=100))])
    assert won.usage.input_tokens == 200
    with pytest.raises(ModelError) as ei:
        run([ModelError("x")] * 3)
    assert ei.value.usage.input_tokens == 300
