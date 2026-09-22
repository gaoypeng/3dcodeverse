"""TPM-aware scheduling: reservation at ``acquire``, reconciliation at ``report``,
and the process-wide in-flight cap.  Everything runs on a fake clock.
"""

from __future__ import annotations

import threading

import pytest

from codeverse3d.models.retry import KeyPool, KeyPoolExhausted


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def make(keys=("a", "b"), **kw) -> tuple[KeyPool, Clock]:
    c = Clock()
    return KeyPool(list(keys), clock=c, sleep=c.sleep, **kw), c


def tpm_left(pool: KeyPool, key: str) -> float:
    return pool._by_key[key].tpm.tokens  # noqa: SLF001 - white-box on purpose


# --------------------------------------------------------------- reservations
def test_a_big_call_and_a_small_call_are_scheduled_differently():
    """A judge-sized reservation exhausts a key; a caption-sized one does not."""
    pool, _ = make(keys=("a",), tpm_per_key=250_000)
    assert pool.acquire(tokens_hint=200_000) == "a"
    # 50k left: the 2k caption still fits, another 200k judge call does not
    assert pool.acquire(tokens_hint=2_000) == "a"
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(tokens_hint=200_000, timeout_s=0.0)


def test_reservations_reconcile_to_actual_provider_consumption():
    for reserved, outcome, actual, expected in (
        (60_000, "ok", 10_000, 90_000),
        (10_000, "ok", 95_000, 5_000),
        (90_000, "5xx", 0, 100_000),
        (50_000, "error", 42_000, 58_000),
    ):
        pool, _ = make(keys=("a",), tpm_per_key=100_000, cooldown_s=0.0)
        pool.acquire(tokens_hint=reserved)
        pool.report("a", outcome, tokens=actual, reserved=reserved)
        assert tpm_left(pool, "a") == pytest.approx(expected)


def test_the_bucket_may_go_negative_and_is_paid_off_by_refill():
    pool, clock = make(keys=("a",), tpm_per_key=60_000)  # 1000 tokens/s
    pool.acquire(tokens_hint=1_000)
    pool.report("a", "ok", tokens=100_000, reserved=1_000)
    assert tpm_left(pool, "a") < 0
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(tokens_hint=1_000, timeout_s=0.0)
    clock.t += 60  # a full minute of refill clears the debt
    assert pool.acquire(tokens_hint=1_000) == "a"


def test_tpm_stats_reflect_capacity_and_spend():
    pool, _ = make(keys=("a", "b"), tpm_per_key=100_000)
    assert pool.stats()["tpm_headroom"] == 1.0
    pool.acquire(tokens_hint=50_000)
    pool.report("a", "ok", tokens=7_500, reserved=50_000)
    stats = pool.stats()
    assert stats["tokens_used"] == 7_500
    assert stats["tpm_capacity"] == 200_000
    assert stats["tpm_headroom"] == pytest.approx(0.963)


def test_no_tpm_bucket_means_no_tpm_accounting():
    pool, _ = make(keys=("a",))
    assert pool.acquire(tokens_hint=10**9) == "a"  # unlimited without a bucket
    assert pool.stats()["tpm_headroom"] is None
    assert pool.stats()["tpm_capacity"] == 0


# ------------------------------------------------------------- in-flight cap
def test_in_flight_gauge_tracks_acquire_and_release():
    pool, _ = make(keys=("a", "b"))
    pool.acquire()
    pool.acquire()
    assert pool.stats()["in_flight"] == 2
    assert pool.stats()["peak_in_flight"] == 2
    pool.release()
    assert pool.stats()["in_flight"] == 1
    assert pool.stats()["peak_in_flight"] == 2  # peak is a high-water mark
    pool.release()
    pool.release()  # a stray release is a no-op, not a crash
    assert pool.stats()["in_flight"] == 0


def test_max_in_flight_blocks_the_third_caller():
    pool = KeyPool(["a", "b", "c"], max_in_flight=2, rpm_per_key=10_000)
    pool.acquire()
    pool.acquire()
    started = threading.Event()
    got = threading.Event()

    def third() -> None:
        started.set()
        pool.acquire()
        got.set()

    t = threading.Thread(target=third, daemon=True)
    t.start()
    started.wait(2)
    assert not got.wait(0.2), "the 3rd call should be held by the in-flight cap"
    pool.release()
    assert got.wait(2), "releasing a slot must let the queued call through"
    t.join(2)


def test_a_failed_acquire_gives_its_slot_back():
    """KeyPoolExhausted must not leak a concurrency slot."""
    pool = KeyPool(["a"], max_in_flight=1, rpm_per_key=10_000)
    for _ in range(3):
        with pytest.raises(KeyPoolExhausted):
            pool.acquire(exclude={"a"}, timeout_s=0.0)
    assert pool.acquire(timeout_s=0.0) == "a"  # the slot is still free
