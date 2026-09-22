"""with_retries / backoff_delay / rotate_with_retries."""

from __future__ import annotations

import random
import threading
import time

import pytest

from codeverse3d.contracts.common import Usage
from codeverse3d.models.base import ModelError
from codeverse3d.models.gemini import HTTP_TIMEOUT_FLOOR_S
from codeverse3d.models.retry import (
    ACQUIRE_TIMEOUT_S,
    MAX_WAIT_S,
    RETRY_DEADLINE_S,
    KeyPool,
    KeyPoolExhausted,
    Outcome,
    StormGate,
    backoff_delay,
    rotate_with_retries,
    with_retries,
)


def test_backoff_grows_and_caps():
    d = [backoff_delay(i, base_delay=1.0, max_delay=8.0, jitter=False) for i in range(1, 7)]
    assert d == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    j = backoff_delay(3, base_delay=1.0, max_delay=8.0)
    assert 2.0 <= j <= 4.0


def test_retries_until_success_and_reports():
    calls: list[int] = []
    slept: list[float] = []
    seen: list[tuple[int, float]] = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("flaky")
        return "ok"

    out = with_retries(
        fn,
        is_retryable=lambda e: isinstance(e, TimeoutError),
        attempts=5,
        base_delay=0.5,
        max_delay=2.0,
        on_retry=lambda n, e, d: seen.append((n, d)),
        sleep=slept.append,
        jitter=False,
    )
    assert out == "ok" and len(calls) == 3
    assert slept == [0.5, 1.0]
    assert [n for n, _ in seen] == [1, 2]


def test_with_retries_stops_on_non_retryable_and_exhaustion():
    calls = 0

    def non_retryable():
        nonlocal calls
        calls += 1
        raise ValueError("bad")

    with pytest.raises(ValueError):
        with_retries(
            non_retryable, is_retryable=lambda error: False, attempts=5, sleep=lambda _: None
        )
    assert calls == 1

    def exhausted():
        nonlocal calls
        calls += 1
        raise TimeoutError("always")

    with pytest.raises(TimeoutError):
        with_retries(exhausted, is_retryable=lambda error: True, attempts=3, sleep=lambda _: None)
    assert calls == 4


def test_with_retries_honours_a_deadline_and_stamps_attempts():
    """The SDK adapters' loop: ``ChatRequest.max_wait_s`` bounds the whole call and the
    raised ModelError says how many round-trips were issued (the ledger's error row)."""
    from codeverse3d.models.base import ModelError

    calls = 0

    def flaky():
        nonlocal calls
        calls += 1
        raise ModelError("busy", retryable=True, status=503)

    with pytest.raises(ModelError) as ei:
        with_retries(flaky, is_retryable=lambda e: True, attempts=6, base_delay=1.0, max_delay=1.0,
                     max_total_s=0.0, sleep=lambda _: None, jitter=False)
    assert calls == 1 and ei.value.attempts == 1  # the first backoff would already cross the deadline
    calls = 0
    with pytest.raises(ModelError) as ei:
        with_retries(flaky, is_retryable=lambda e: True, attempts=3, sleep=lambda _: None)
    assert calls == 3 and ei.value.attempts == 3


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
    """A pool of ``n`` keys with the per-key RPM limiter effectively off."""
    kw.setdefault("rpm_per_key", 10_000)
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


def test_rotate_success_reports_ok_with_tokens():
    pool = KeyPool(["k1", "k2"])
    out = _rotate(pool, lambda key: f"ok:{key}", tokens_of=lambda r: 42)
    assert out == "ok:k1"
    st = pool.stats()
    assert st["ok"] == 1 and st["429"] == 0 and st["dead"] == 0


def test_rotate_dead_key_is_free_and_benched_after_sibling_success():
    pool = KeyPool(["k1", "k2"])
    calls: list[str] = []

    def call(key: str) -> str:
        calls.append(key)
        if key == "k1":
            raise ModelError("suspended", retryable=False, status=403)
        return key

    assert _rotate(pool, call, max_attempts=1) == "k2"  # dead rotation burnt no budget
    assert calls == ["k1", "k2"]
    st = pool.stats()
    assert st["dead"] == 1 and st["n_dead"] == 1  # k1 benched only after k2 proved the request


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


def test_rotate_429_is_free_while_untried_keys_remain():
    clock = Clock()
    pool = KeyPool(["k1", "k2", "k3"], cooldown_s=30, clock=clock, sleep=clock.sleep)
    calls: list[str] = []
    hinted: list[BaseException] = []

    def call(key: str) -> str:
        calls.append(key)
        if key in ("k1", "k2"):
            raise ModelError("quota", retryable=True, status=429)
        return key

    out = _rotate(
        pool,
        call,
        max_attempts=1,
        sleep=clock.sleep,
        retry_after=lambda error: hinted.append(error) or 7.0,
    )
    assert out == "k3" and calls == ["k1", "k2", "k3"]
    assert clock.naps == [0.5, 0.5]
    stats = pool.stats()
    assert len(hinted) == 2 and stats["n_cooling"] == 2
    assert max(key["cooldown_s"] for key in stats["keys"]) <= MAX_WAIT_S


def test_rotate_on_free_retry_hook_consumes_no_budget():
    pool = KeyPool(["k1"])
    state = {"fixed": False}
    calls: list[int] = []

    def call(key: str) -> str:
        calls.append(1)
        if not state["fixed"]:
            raise ModelError("thinking not supported", retryable=False, status=400)
        return "ok"

    def free(err: ModelError) -> bool:
        if "thinking" in str(err) and not state["fixed"]:
            state["fixed"] = True
            return True
        return False

    assert _rotate(pool, call, max_attempts=1, on_free_retry=free) == "ok"
    assert len(calls) == 2


def test_rotate_retryable_backoff_and_nonretryable_failure_modes():
    pool = KeyPool(["k1"])
    slept: list[float] = []

    def retryable(key: str) -> str:
        raise ModelError("unavailable", retryable=True, status=500)

    with pytest.raises(ModelError) as ei:
        _rotate(
            pool,
            retryable,
            max_attempts=3,
            base_delay=1.0,
            max_delay=4.0,
            sleep=slept.append,
        )
    assert ei.value.status == 500 and len(slept) == 2  # backoff between the 3 attempts

    calls: list[str] = []

    def nonretryable(key: str) -> str:
        calls.append(key)
        raise ModelError("bad request", retryable=False, status=400)

    with pytest.raises(ModelError):
        _rotate(KeyPool(["k1", "k2"]), nonretryable, max_attempts=5)
    assert calls == ["k1"]


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


def test_503_storm_budget_exhausts_then_normal_budget_applies():
    """Exhausted storm patience falls back to the finite normal budget."""
    with pytest.raises(ModelError):
        _rotate503(
            _pool(1),
            _boom("503 forever"),
            max_attempts=2,
            base_delay=0.0,
            storm_attempts=3,
            storm_max_delay=0.0,
        )


def test_one_call_cannot_retry_for_hours():
    """The wall-clock deadline bounds even a long 503 storm."""
    clock = Clock()

    def call(_key):
        # gemini.py _attempt_config: min(300 s, remaining), floored at HTTP_TIMEOUT_FLOOR_S
        clock.t += min(300.0, max(HTTP_TIMEOUT_FLOOR_S, RETRY_DEADLINE_S - clock.t))
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
    assert clock.t <= RETRY_DEADLINE_S + HTTP_TIMEOUT_FLOOR_S, (
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


def test_503_rotates_through_every_untried_key_before_it_is_a_storm():
    """A 503 rotates freely while the pool still has an untried key."""
    keys = [f"k{i}" for i in range(1, 11)]
    gate = StormGate()
    lock = threading.Lock()
    calls: list[str] = []
    naps: list[float] = []

    def call(key):
        with lock:  # hedged siblings run in threads
            calls.append(key)
            nth = len(set(calls))
        if nth < len(keys):  # only the LAST untried key answers
            raise RuntimeError("503 high demand")
        return "ok"

    out = _rotate503(
        _pool(10),
        call,
        max_attempts=3,
        storm_attempts=10,
        storm_max_delay=0.05,
        sleep=naps.append,
        storm_gate=gate,
    )
    # one key per round-trip, never twice: 9 free rotations, then the 10th key lands
    assert out == "ok" and len(calls) == len(keys) and set(calls) == set(keys)
    assert naps == [], "rotation to a fresh key costs no sleep"
    assert gate.n_storms == 0 and not gate.storming, "an untried key remains: not a storm"


@pytest.mark.parametrize(
    ("hedge", "want_calls", "want_hits", "want_stats"),
    [
        (1, {6}, 3, {"attempts": 6, "hedged": 0, "storm": 3}),
        (2, {6, 7}, 2, {"attempts": 7, "hedged": 3, "storm": 2}),
    ],
)
def test_503_on_every_key_is_still_a_storm(hedge, want_calls, want_hits, want_stats):
    """Exhausting every key engages one bounded wait per hedged attempt."""
    gate = StormGate("t", base_delay=0.0, max_wait_s=0.0, probe_lease_s=0.0)  # no real waits
    lock = threading.Lock()
    n = {"c": 0}
    stats: dict = {}

    def call(key):
        with lock:
            n["c"] += 1
            i = n["c"]
        if i <= 5:
            raise RuntimeError("503 high demand")
        return "ok"

    out = _rotate503(
        _pool(3),
        call,
        max_attempts=3,
        storm_attempts=10,
        storm_max_delay=0.05,
        storm_gate=gate,
        hedge=hedge,
        stats=stats,
    )
    assert out == "ok" and n["c"] in want_calls
    assert gate.n_storms == 1 and gate.n_hits == want_hits, (
        "declared only after all three keys failed, then waited out"
    )
    assert stats == want_stats


# --------------------------------------------------------------------------- hedging (audit 2026-08-26 §5.2)
def _wait_idle(pool, timeout: float = 5.0) -> None:
    """Block until every hedged loser has finished and released its slot."""
    t_end = time.monotonic() + timeout
    while pool.stats()["in_flight"] and time.monotonic() < t_end:
        time.sleep(0.005)
    assert pool.stats()["in_flight"] == 0


def test_after_the_first_503_the_next_attempt_is_hedged_and_the_first_success_wins():
    """After a 503, fresh keys race and the first success wins."""
    pool = KeyPool(["k1", "k2", "k3", "k4"], rpm_per_key=10_000, max_in_flight=8)
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
        tokens_of=lambda r: 7,
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


def test_a_loser_that_succeeds_later_is_discarded_but_its_tokens_are_reported():
    """A late successful loser is discarded but still charged to its key."""
    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000, tpm_per_key=1_000_000)
    release_k2 = threading.Event()

    def call(key):
        if key == "k1":
            raise ModelError("503 high demand", retryable=True, status=503)
        if key == "k2":
            assert release_k2.wait(5.0)
            return "late:k2"
        return "ok:k3"

    out = _rotate(
        pool,
        call,
        max_attempts=3,
        storm_attempts=10,
        tokens_of=lambda r: 900 if r.endswith("k2") else 100,
    )
    assert out == "ok:k3"
    release_k2.set()
    _wait_idle(pool)
    assert pool.stats()["ok"] == 2
    assert pool._by_key["k2"].tokens_used == 900 and pool._by_key["k3"].tokens_used == 100  # noqa: SLF001


def test_hedge_1_disables_and_a_full_pool_of_slots_degrades_to_one_key():
    lock = threading.Lock()

    def make_call():
        n = {"c": 0}

        def call(key):
            with lock:
                n["c"] += 1
                i = n["c"]
            if i <= 3:
                raise ModelError("503 high demand", retryable=True, status=503)
            return "ok"

        return call

    stats: dict = {}
    _rotate(
        _pool(3),
        make_call(),
        max_attempts=3,
        storm_attempts=10,
        storm_max_delay=0.0,
        hedge=1,
        stats=stats,
    )
    assert stats["hedged"] == 0 and stats["attempts"] == 4
    # max_in_flight=1: the primary holds the only slot, so a partner is never waited for
    pool = _pool(3, max_in_flight=1)
    stats = {}
    _rotate(pool, make_call(), max_attempts=3, storm_attempts=10, storm_max_delay=0.0, stats=stats)
    assert stats["hedged"] == 0 and stats["attempts"] == 4 and pool.stats()["peak_in_flight"] == 1
    # max_in_flight=2: a hedged attempt holds both slots
    pool = _pool(3, max_in_flight=2)
    stats = {}
    _rotate(pool, make_call(), max_attempts=3, storm_attempts=10, storm_max_delay=0.0, stats=stats)
    _wait_idle(pool)
    assert stats["hedged"] >= 1 and pool.stats()["peak_in_flight"] == 2


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


def test_a_dead_key_among_the_hedge_is_benched_once_the_sibling_wins():
    pool = _pool(3)

    def call(key):
        if key == "k1":
            raise ModelError("503 high demand", retryable=True, status=503)
        if key == "k2":
            raise ModelError("suspended", retryable=False, status=403)
        return "ok:k3"

    assert _rotate(pool, call, max_attempts=3, storm_attempts=10) == "ok:k3"
    _wait_idle(pool)
    assert pool.stats()["n_dead"] == 1, (
        "k3 proved the request fine, so k2's 403 was the key's fault"
    )


# --------------------------------------------------------------------------- deadline threading (2026-08-27)
def test_the_key_wait_is_clipped_to_the_remaining_budget():
    """Pool acquisition cannot outwait the remaining call budget."""
    seen: list[float | None] = []

    class SpyPool(KeyPool):
        def acquire(self, *, tokens_hint=0, exclude=None, timeout_s=None):
            seen.append(timeout_s)
            return super().acquire(tokens_hint=tokens_hint, exclude=exclude, timeout_s=timeout_s)

    pool = SpyPool(["k1"], rpm_per_key=10_000)
    assert _rotate(pool, lambda key: "ok", max_total_s=50.0) == "ok"
    assert _rotate(pool, lambda key: "ok") == "ok"  # the default budget: the pool default caps
    assert seen[0] == pytest.approx(50.0, abs=1.0)
    assert seen[1] == pytest.approx(ACQUIRE_TIMEOUT_S, abs=1.0)


def test_a_storm_wait_is_clipped_to_the_remaining_budget():
    """Sleeping past the deadline only to give up on waking helps nobody."""
    clock = Clock()
    with pytest.raises(ModelError):
        _rotate503(
            _pool(1),
            _boom(),
            max_attempts=6,
            storm_attempts=60,
            base_delay=4.0,
            storm_max_delay=5.0,
            max_total_s=3.0,
            sleep=clock.sleep,
            monotonic=clock,
            hedge=1,
        )
    assert clock.naps, "expected the storm path to sleep"
    assert clock.t <= 3.0 + 1e-6, f"slept past the deadline: {clock.t}"


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


def test_the_pool_is_not_refunded_for_a_charged_but_invalid_reply():
    """A billed invalid response must consume its actual TPM usage."""
    clock = Clock(1000.0)
    pool = _pool(1, tpm_per_key=100_000, clock=clock, sleep=clock.sleep)
    calls = {"n": 0}

    def call(key):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModelError(
                "structured output is not valid JSON",
                retryable=True,
                usage=Usage(input_tokens=42_000),
            )
        return "ok"

    out = _rotate(pool, call, max_attempts=3, tokens_hint=1_000, tokens_of=lambda r: 100)
    assert out == "ok" and calls["n"] == 2
    left = pool._by_key["k1"].tpm.tokens  # noqa: SLF001 - white-box on purpose
    assert left == pytest.approx(100_000 - 42_000 - 100)


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


def test_error_causes_avoid_cycles_and_preserve_real_wrapped_errors():
    """Classified errors avoid self-cycles while preserving genuine causes."""

    def call(key):
        raise ModelError(
            "structured output unavailable (finish_reason=MAX_TOKENS)", retryable=False, status=None
        )

    with pytest.raises(ModelError) as ei:
        _rotate(_pool(), call, max_attempts=1)
    err = ei.value
    assert err.__cause__ is not err, "the classified error became its own __cause__"

    # walk the whole chain: no link may repeat, whatever the branch
    seen, node = set(), err
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        node = node.__cause__ if node.__cause__ is not None else node.__context__
    assert node is None, "the cause chain closes into a cycle"

    def timeout(key):
        raise TimeoutError("read timed out")

    with pytest.raises(ModelError) as ei:
        _rotate(_pool(), timeout, max_attempts=1)
    assert isinstance(ei.value.__cause__, TimeoutError)


def test_a_budget_spent_at_the_storm_gate_issues_no_round_trip():
    """A worker waking past its gate deadline issues no request."""
    clock = Clock(1000.0)
    calls: list[str] = []

    class Gate:  # parks the caller for longer than the whole budget
        def enter(self, deadline=None):
            clock.t += 30.0
            return 30.0

        def hit(self, retry_after=None):
            return 0.0

        def ok(self):
            pass

    with pytest.raises(ModelError) as e:
        _rotate(
            _pool(1),
            lambda key: calls.append(key),
            max_total_s=5.0,
            monotonic=clock,
            storm_gate=Gate(),
            label="t",
        )
    assert calls == [], "a round-trip was issued after the budget was already spent"
    assert "capacity" in str(e.value)
