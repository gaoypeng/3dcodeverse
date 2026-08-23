"""with_retries / backoff_delay."""

from __future__ import annotations

import pytest
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


def test_503_storm_has_its_own_patience_budget():
    """A model-wide 503 storm must not burn the normal attempt budget."""
    from codeverse.models.base import ModelError
    from codeverse.models.keypool import KeyPool
    from codeverse.models.retry import rotate_with_retries

    pool = KeyPool(["k1", "k2"], rpm_per_key=10_000)
    calls = {"n": 0}
    naps: list[float] = []

    def call(key):
        calls["n"] += 1
        if calls["n"] <= 8:  # longer than max_attempts=3 could survive
            raise RuntimeError("503 storm")
        return "ok"

    def classify(exc):
        return ModelError(str(exc), retryable=True, status=503)

    out = rotate_with_retries(
        pool, call, classify=classify, outcome_of=lambda e: "5xx",
        max_attempts=3, base_delay=0.01, storm_attempts=10, storm_max_delay=0.05,
        sleep=naps.append,
    )
    assert out == "ok" and calls["n"] == 9
    assert len(naps) == 8 and all(d <= 0.05 * 1.25 for d in naps)


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
