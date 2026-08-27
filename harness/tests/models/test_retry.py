"""with_retries / backoff_delay."""

from __future__ import annotations

import random
import threading
import time

import pytest

from codeverse.models.retry import backoff_delay, with_retries


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


def test_non_retryable_raises_immediately():
    n = 0

    def fn():
        nonlocal n
        n += 1
        raise ValueError("bad")

    with pytest.raises(ValueError):
        with_retries(fn, is_retryable=lambda e: False, attempts=5, sleep=lambda s: None)
    assert n == 1


def test_exhausted_raises_last():
    def fn():
        raise TimeoutError("always")

    with pytest.raises(TimeoutError):
        with_retries(fn, is_retryable=lambda e: True, attempts=3, sleep=lambda s: None)


# --------------------------------------------------------------------------- rotate_with_retries
from codeverse.models.base import ModelError  # noqa: E402
from codeverse.models.keypool import KeyPool, KeyPoolExhausted, Outcome  # noqa: E402
from codeverse.models.retry import rotate_with_retries  # noqa: E402
from codeverse.models.storm import StormGate  # noqa: E402


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


def _rotate(pool, call, **kw):
    kw.setdefault("classify", _classify)
    kw.setdefault("outcome_of", _outcome)
    kw.setdefault("sleep", lambda s: None)
    return rotate_with_retries(pool, call, **kw)


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
    pool = KeyPool(["k1", "k2", "k3"], cooldown_s=30)
    slept: list[float] = []
    calls: list[str] = []

    def call(key: str) -> str:
        calls.append(key)
        if key in ("k1", "k2"):
            raise ModelError("quota", retryable=True, status=429)
        return key

    out = _rotate(pool, call, max_attempts=1, sleep=slept.append)
    assert out == "k3" and calls == ["k1", "k2", "k3"]
    assert slept == [0.5, 0.5]  # courtesy pauses only, no backoff
    assert pool.stats()["n_cooling"] == 2


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


def test_rotate_retryable_backoff_then_exhausted_raises_last():
    pool = KeyPool(["k1"])
    slept: list[float] = []

    def call(key: str) -> str:
        raise ModelError("unavailable", retryable=True, status=500)

    with pytest.raises(ModelError) as ei:
        _rotate(pool, call, max_attempts=3, base_delay=1.0, max_delay=4.0, sleep=slept.append)
    assert ei.value.status == 500 and len(slept) == 2  # backoff between the 3 attempts


def test_rotate_nonretryable_raises_immediately():
    pool = KeyPool(["k1", "k2"])
    calls: list[str] = []

    def call(key: str) -> str:
        calls.append(key)
        raise ModelError("bad request", retryable=False, status=400)

    with pytest.raises(ModelError):
        _rotate(pool, call, max_attempts=5)
    assert calls == ["k1"]


def test_rotate_retry_after_hint_reaches_the_pool():
    clock = {"t": 0.0}
    pool = KeyPool(["k1", "k2"], cooldown_s=30, clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    seen: list[BaseException] = []

    def call(key: str) -> str:
        if key == "k1":
            raise ModelError("quota retryDelay 7s", retryable=True, status=429)
        return key

    out = _rotate(pool, call, max_attempts=1, retry_after=lambda e: seen.append(e) or 7.0)
    assert out == "k2" and len(seen) == 1
    cooling = {k["key"]: k["cooldown_s"] for k in pool.stats()["keys"]}
    assert 0 < cooling["…k1"] <= 7.0


@pytest.mark.parametrize(("hedge", "want_calls", "want_naps"), [(1, {9}, 7), (2, {9, 10}, 4)])
def test_503_storm_has_its_own_patience_budget(hedge, want_calls, want_naps):
    """A model-wide 503 storm must not burn the normal attempt budget.

    hedge=1: eight sequential 503s then ok — seven storm waits, more than max_attempts=3
    could survive.  hedge=2 (the default, audit 2026-08-26 §5.2): from the second 503 on
    every attempt races both keys, so the same eight failures are waited out in FOUR storm
    waits, and the winning attempt's sibling may land too (a tenth call, discarded)."""
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import KeyPool
    from codeverse.models.retry import rotate_with_retries

    pool = KeyPool(["k1", "k2"], rpm_per_key=10_000)
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

    def classify(exc):
        return ModelError(str(exc), retryable=True, status=503)

    out = rotate_with_retries(
        pool, call, classify=classify, outcome_of=lambda e: "5xx",
        max_attempts=3, base_delay=0.01, storm_attempts=10, storm_max_delay=0.05,
        sleep=naps.append, hedge=hedge,
    )
    assert out == "ok" and calls["n"] in want_calls
    # the first 503 rotates to k2 for free (no nap); k2's 503 leaves no untried key in the
    # 2-key pool, so that one is the storm
    assert len(naps) == want_naps and all(d <= 0.05 for d in naps)  # the cap is the cap


def test_storm_waits_never_exceed_the_house_limit_at_production_defaults():
    """Jitter must be applied BEFORE the cap.  Regression: the storm branch capped
    first and then multiplied by up to 1.25, so a documented "<=5 s" wait was
    observed at 6 s in a live bench run (2026-08-24)."""
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import MAX_WAIT_S, KeyPool
    from codeverse.models.retry import rotate_with_retries

    naps: list[float] = []
    for seed in range(50):  # exercise many jitter draws, not one lucky one
        random.seed(seed)
        pool = KeyPool(["k1", "k2"], rpm_per_key=10_000)
        with pytest.raises(ModelError):
            rotate_with_retries(
                pool,
                lambda key: (_ for _ in ()).throw(RuntimeError("503 high demand")),
                classify=lambda e: ModelError(str(e), retryable=True, status=503),
                outcome_of=lambda e: "5xx",
                max_attempts=2,
                storm_attempts=12,  # deep enough that the exponent saturates the cap
                sleep=naps.append,
            )
    assert naps, "expected the storm path to sleep"
    assert max(naps) <= MAX_WAIT_S, f"a storm wait exceeded {MAX_WAIT_S}s: {max(naps)}"
    assert max(naps) > MAX_WAIT_S * 0.9, "the cap should still be reached, not just respected"


def test_503_storm_budget_exhausts_then_normal_budget_applies():
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import KeyPool
    from codeverse.models.retry import rotate_with_retries

    pool = KeyPool(["k1"], rpm_per_key=10_000)

    def call(key):
        raise RuntimeError("503 forever")

    with pytest.raises(ModelError):
        rotate_with_retries(
            pool, call, classify=lambda e: ModelError(str(e), retryable=True, status=503),
            outcome_of=lambda e: "5xx", max_attempts=2, base_delay=0.0,
            storm_attempts=3, storm_max_delay=0.0, sleep=lambda d: None,
        )


def test_live_tests_are_opt_in_by_default():
    """A bare `pytest` must never fire real API calls.

    On 2026-08-24 a plain run on a keyed machine hung for the whole timeout because
    the live suite was selected by default and the provider was in a capacity storm.
    """
    import tomllib  # 3.10 floor: stdlib tomllib is 3.11+
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "pyproject.toml").read_text())
    addopts = cfg["tool"]["pytest"]["ini_options"].get("addopts", [])
    joined = " ".join(addopts) if isinstance(addopts, list) else str(addopts)
    assert "not live" in joined, "live tests must be deselected by default (see pyproject addopts)"


def test_one_call_cannot_retry_for_hours():
    """The storm branch was bounded in ATTEMPTS, not in time.

    60 storm attempts x (a 300 s read timeout + a <=5 s wait) is 5.1 hours for ONE
    logical call, while the docstring claimed "~5 min".  Measured 2026-08-24: combined
    with a wall-clock ceiling that is only checked when a call is BILLED (a stalled
    call bills nothing), a run with --max-minutes 90 reached 200 minutes.

    Since 2026-08-27 the deadline is threaded through every branch of the loop and
    ``GeminiModel`` clips each attempt's HTTP read timeout to the remaining budget,
    floored at ``HTTP_TIMEOUT_FLOOR_S`` (simulated by ``call`` below) — so ONE call
    may overshoot by at most one floored in-flight attempt, never by a full 300 s
    socket.
    """
    from codeverse.models.base import ModelError
    from codeverse.models.gemini import HTTP_TIMEOUT_FLOOR_S
    from codeverse.models.keypool import KeyPool
    from codeverse.models.retry import RETRY_DEADLINE_S, rotate_with_retries

    clock = {"t": 0.0}
    naps: list[float] = []

    def sleep(d):  # every wait advances the fake clock
        naps.append(d)
        clock["t"] += d

    def call(_key):
        # the budget-clipped read timeout each doomed attempt burns
        # (gemini.py _attempt_config: min(300 s, remaining), floored at 20 s)
        remaining = RETRY_DEADLINE_S - clock["t"]
        clock["t"] += min(300.0, max(HTTP_TIMEOUT_FLOOR_S, remaining))
        raise RuntimeError("503 high demand")

    with pytest.raises(ModelError):
        rotate_with_retries(
            KeyPool(["k1", "k2"], rpm_per_key=10_000), call,
            classify=lambda e: ModelError(str(e), retryable=True, status=503),
            outcome_of=lambda e: "5xx", max_attempts=6, storm_attempts=60,
            sleep=sleep, monotonic=lambda: clock["t"],
            hedge=1,  # the fake clock is serial; two hedged 300 s calls would add 600 s, not 300
        )
    # the whole point: bounded by the clock — the ONLY legal overshoot is the one
    # floored attempt that was already in flight when the deadline passed
    assert clock["t"] <= RETRY_DEADLINE_S + HTTP_TIMEOUT_FLOOR_S, (
        f"one call burned {clock['t'] / 3600:.2f} h; the deadline is {RETRY_DEADLINE_S / 60:.0f} min")
    assert clock["t"] < 5 * 3600, "this is the 5.1-hour regression"


def test_the_deadline_does_not_cut_a_call_that_is_making_progress():
    """A slow-but-succeeding call must not be killed by the retry deadline."""
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import KeyPool
    from codeverse.models.retry import rotate_with_retries

    clock = {"t": 0.0}
    calls = {"n": 0}

    def call(_key):
        calls["n"] += 1
        clock["t"] += 280.0           # slow, but under the per-attempt timeout
        if calls["n"] < 3:
            raise RuntimeError("503 high demand")
        return "ok"

    out = rotate_with_retries(
        KeyPool(["k1", "k2"], rpm_per_key=10_000), call,
        classify=lambda e: ModelError(str(e), retryable=True, status=503),
        outcome_of=lambda e: "5xx", max_attempts=6, storm_attempts=60,
        sleep=lambda d: clock.__setitem__("t", clock["t"] + d),
        monotonic=lambda: clock["t"],
        hedge=1,  # serial fake clock and call counter (see the hedge tests for the raced form)
    )
    assert out == "ok" and calls["n"] == 3


def test_503_rotates_through_every_untried_key_before_it_is_a_storm():
    """Owner's rule (2026-08-27): a 503 on one key is simply replaced by the next key —
    exactly like the 429 path, free while an untried key remains.  Measured 2026-08-26:
    one tiny request per key in parallel during the day's storm — flash answered on
    15/22, 18/22, 21/22 keys while only 5/4/1 keys said 503 at the same instant.  So with
    a 10-key pool, NINE consecutive 503s on distinct keys still rotate: no sleep, no
    storm, no ``max_attempts`` spent (``max_attempts=3`` here proves the budget is free)."""
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import KeyPool
    from codeverse.models.retry import rotate_with_retries
    from codeverse.models.storm import StormGate

    keys = [f"k{i}" for i in range(1, 11)]
    pool = KeyPool(keys, rpm_per_key=10_000)
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

    out = rotate_with_retries(pool, call, classify=lambda e: ModelError(str(e), retryable=True, status=503),
                              outcome_of=lambda e: "5xx", max_attempts=3, storm_attempts=10, storm_max_delay=0.05,
                              sleep=naps.append, storm_gate=gate)
    # one key per round-trip, never twice: 9 free rotations, then the 10th key lands
    assert out == "ok" and len(calls) == len(keys) and set(calls) == set(keys)
    assert naps == [], "rotation to a fresh key costs no sleep"
    assert gate.n_storms == 0 and not gate.storming, "an untried key remains: not a storm"


@pytest.mark.parametrize(("hedge", "want_calls", "want_hits"), [(1, {6}, 3), (2, {6, 7}, 2)])
def test_503_on_every_key_is_still_a_storm(hedge, want_calls, want_hits):
    """The backstop: once EVERY key in the pool has 503'd inside this one call there is
    no untried key left to rotate to, so the bounded storm wait engages.
    hedge=1: k1, k2, k3 fail one by one (the whole pool), then two storm waits, then ok.
    hedge=2: k1 fails alone, k2+k3 fail together (pool exhausted: storm 1), two more fail
    together (storm 2), then the raced pair lands — one storm wait per hedged attempt,
    not per key."""
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import KeyPool
    from codeverse.models.retry import rotate_with_retries
    from codeverse.models.storm import StormGate

    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000)
    gate = StormGate("t", base_delay=0.0, max_wait_s=0.0, probe_lease_s=0.0)  # no real waits
    lock = threading.Lock()
    n = {"c": 0}
    naps: list[float] = []

    def call(key):
        with lock:
            n["c"] += 1
            i = n["c"]
        if i <= 5:
            raise RuntimeError("503 high demand")
        return "ok"

    out = rotate_with_retries(pool, call, classify=lambda e: ModelError(str(e), retryable=True, status=503),
                              outcome_of=lambda e: "5xx", max_attempts=3, storm_attempts=10, storm_max_delay=0.05,
                              sleep=naps.append, storm_gate=gate, hedge=hedge)
    assert out == "ok" and n["c"] in want_calls
    assert gate.n_storms == 1 and gate.n_hits == want_hits, "the storm is declared only after all three keys failed, then waited out"


# --------------------------------------------------------------------------- hedging (audit 2026-08-26 §5.2)
def _wait_idle(pool, timeout: float = 5.0) -> None:
    """Block until every hedged loser has finished and released its slot."""
    t_end = time.monotonic() + timeout
    while pool.stats()["in_flight"] and time.monotonic() < t_end:
        time.sleep(0.005)
    assert pool.stats()["in_flight"] == 0


def _503(e):
    return ModelError(str(e), retryable=True, status=503)


def test_after_the_first_503_the_next_attempt_is_hedged_and_the_first_success_wins():
    """Measured 2026-08-26: a failed 503 costs the 21-50 s round-trip the provider holds
    before rejecting, not the <= 5 s sleep (logged sleep was 13 % of the wait), and storm
    streaks average 4.7 attempts.  So from the first 503 on, the next attempt goes out on
    two fresh keys at once and the first success is returned while the other is still in
    flight; the loser reports its own outcome and releases its own slot when it lands."""
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

    out = rotate_with_retries(pool, call, classify=_503, outcome_of=lambda e: "5xx", max_attempts=3,
                              storm_attempts=10, sleep=naps.append, tokens_of=lambda r: 7, stats=stats)
    assert out == "ok:k3"
    assert naps == [], "a hedged retry sleeps for nothing"
    assert calls[0] == "k1" and sorted(calls[1:]) == ["k2", "k3"], "two distinct fresh keys, k1 excluded"
    assert stats == {"attempts": 3, "hedged": 1, "storm": 0}
    st = pool.stats()
    assert st["in_flight"] == 1 and st["peak_in_flight"] == 2, "the loser still holds its max_in_flight slot"
    assert st["ok"] == 1 and st["5xx"] == 1
    release_k2.set()
    _wait_idle(pool)
    st = pool.stats()
    assert st["5xx"] == 2 and st["in_flight"] == 0, "the loser's 503 is reported when it lands"


def test_a_loser_that_succeeds_later_is_discarded_but_its_tokens_are_reported():
    """A success-then-success is the one case the hedge is not free: the second answer is
    thrown away, its tokens still counted against the key (a 503 bills nothing, so while it
    storms the hedge costs nothing)."""
    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000, tpm_per_key=1_000_000)
    release_k2 = threading.Event()

    def call(key):
        if key == "k1":
            raise ModelError("503 high demand", retryable=True, status=503)
        if key == "k2":
            assert release_k2.wait(5.0)
            return "late:k2"
        return "ok:k3"

    out = _rotate(pool, call, max_attempts=3, storm_attempts=10, tokens_of=lambda r: 900 if r.endswith("k2") else 100)
    assert out == "ok:k3"
    release_k2.set()
    _wait_idle(pool)
    assert pool.stats()["ok"] == 2
    assert pool._by_key["k2"].tokens_used == 900 and pool._by_key["k3"].tokens_used == 100  # noqa: SLF001


def test_a_hedged_attempt_that_fails_on_every_key_is_one_storm_attempt():
    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000)
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

    out = rotate_with_retries(pool, call, classify=_503, outcome_of=lambda e: "5xx", max_attempts=3,
                              storm_attempts=10, storm_max_delay=0.05, sleep=lambda d: None, storm_gate=gate,
                              stats=stats)
    assert out == "ok"
    # k1 alone; k2+k3 (all 3 keys 503'd → storm 1); two more (storm 2); the raced pair lands
    assert stats == {"attempts": 7, "hedged": 3, "storm": 2}
    assert gate.n_hits == 2, "two hedged failures = two storm waits, not four"


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
    _rotate(KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000), make_call(), max_attempts=3, storm_attempts=10,
            storm_max_delay=0.0, hedge=1, stats=stats)
    assert stats["hedged"] == 0 and stats["attempts"] == 4
    # max_in_flight=1: the primary holds the only slot, so a partner is never waited for
    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000, max_in_flight=1)
    stats = {}
    _rotate(pool, make_call(), max_attempts=3, storm_attempts=10, storm_max_delay=0.0, stats=stats)
    assert stats["hedged"] == 0 and stats["attempts"] == 4 and pool.stats()["peak_in_flight"] == 1
    # max_in_flight=2: a hedged attempt holds both slots
    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000, max_in_flight=2)
    stats = {}
    _rotate(pool, make_call(), max_attempts=3, storm_attempts=10, storm_max_delay=0.0, stats=stats)
    _wait_idle(pool)
    assert stats["hedged"] >= 1 and pool.stats()["peak_in_flight"] == 2


def test_the_worst_error_decides_a_hedged_attempt():
    """One key says 503 and the other says the request itself is bad: raise the 400 at once
    instead of storming on a request that can never succeed."""
    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000)
    stats: dict = {}

    def call(key):
        if key == "k3":
            raise ModelError("bad request", retryable=False, status=400)
        raise ModelError("503 high demand", retryable=True, status=503)

    with pytest.raises(ModelError) as ei:
        _rotate(pool, call, max_attempts=6, storm_attempts=10, stats=stats)
    assert ei.value.status == 400 and stats == {"attempts": 3, "hedged": 1, "storm": 0}


def test_a_dead_key_among_the_hedge_is_benched_once_the_sibling_wins():
    pool = KeyPool(["k1", "k2", "k3"], rpm_per_key=10_000)

    def call(key):
        if key == "k1":
            raise ModelError("503 high demand", retryable=True, status=503)
        if key == "k2":
            raise ModelError("suspended", retryable=False, status=403)
        return "ok:k3"

    assert _rotate(pool, call, max_attempts=3, storm_attempts=10) == "ok:k3"
    _wait_idle(pool)
    assert pool.stats()["n_dead"] == 1, "k3 proved the request fine, so k2's 403 was the key's fault"
# --------------------------------------------------------------------------- deadline threading (2026-08-27)
def test_the_key_wait_is_clipped_to_the_remaining_budget():
    """pool.acquire used to be called with its own 120 s default no matter how little
    budget the call had left; now it gets min(ACQUIRE_TIMEOUT_S, remaining)."""
    from codeverse.models.keypool import ACQUIRE_TIMEOUT_S

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
    clock = {"t": 0.0}
    naps: list[float] = []

    def sleep(d):
        naps.append(d)
        clock["t"] += d

    with pytest.raises(ModelError):
        rotate_with_retries(
            KeyPool(["k1"], rpm_per_key=10_000),
            lambda key: (_ for _ in ()).throw(RuntimeError("503 high demand")),
            classify=lambda e: ModelError(str(e), retryable=True, status=503),
            outcome_of=lambda e: "5xx", max_attempts=6, storm_attempts=60,
            base_delay=4.0, storm_max_delay=5.0, max_total_s=3.0,
            sleep=sleep, monotonic=lambda: clock["t"], hedge=1,
        )
    assert naps, "expected the storm path to sleep"
    assert clock["t"] <= 3.0 + 1e-6, f"slept past the deadline: {clock['t']}"


def test_free_429_rotation_cannot_outlive_the_deadline():
    """Free rotation costs no attempts but DOES cost wall clock (0.5 s per hop):
    with many keys it used to spin long past the budget."""
    clock = {"t": 0.0}
    keys = [f"k{i}" for i in range(1, 11)]
    pool = KeyPool(keys, rpm_per_key=10_000, cooldown_s=0.0,
                   clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    calls: list[str] = []

    def call(key):
        calls.append(key)
        raise ModelError("quota", retryable=True, status=429)

    def sleep(d):
        clock["t"] += d

    with pytest.raises(ModelError) as ei:
        rotate_with_retries(
            pool, call, classify=_classify, outcome_of=_outcome, max_attempts=6,
            max_total_s=2.0, sleep=sleep, monotonic=lambda: clock["t"],
        )
    assert ei.value.status == 429
    assert clock["t"] <= 2.5, f"free rotation outlived the deadline: {clock['t']}"
    assert len(calls) <= 5, "ten keys would all have been tried before"


def test_the_pool_is_not_refunded_for_a_charged_but_invalid_reply():
    """retry.py used to report tokens=0 for a bad-JSON failure, refunding a TPM
    reservation the provider had actually consumed — a 42k-token invalid reply
    handed the key 42k TPM back.  ``ModelError.usage`` now carries the real bill."""
    from codeverse.contracts.common import Usage

    clock = {"t": 1000.0}
    pool = KeyPool(["k1"], rpm_per_key=10_000, tpm_per_key=100_000,
                   clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    calls = {"n": 0}

    def call(key):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModelError("structured output is not valid JSON", retryable=True,
                             usage=Usage(input_tokens=42_000))
        return "ok"

    out = _rotate(pool, call, max_attempts=3, tokens_hint=1_000, tokens_of=lambda r: 100)
    assert out == "ok" and calls["n"] == 2
    left = pool._by_key["k1"].tpm.tokens  # noqa: SLF001 - white-box on purpose
    assert left == pytest.approx(100_000 - 42_000 - 100)


# --------------------------------------------------------------------------- on_attempt hook (2026-08-27)
def test_on_attempt_hears_every_round_trip_and_a_late_hedge_loser():
    """The per-attempt hook: the winner flushes as not-discarded, every failed try
    as discarded, and a hedge loser still in flight reports (discarded — a late
    success included) when it lands.  That is how a loser's paid tokens reach the
    cost ledger at all."""
    pool = KeyPool(["k1", "k2", "k3", "k4"], rpm_per_key=10_000)
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
    pool = KeyPool(["k1"], rpm_per_key=10_000)

    def hook(t, no, discarded):
        raise RuntimeError("accounting on fire")

    assert _rotate(pool, lambda key: "ok", on_attempt=hook) == "ok"
