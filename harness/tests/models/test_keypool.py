"""KeyPool: rotation, 429 cooldown, buckets, health, exhaustion, stats."""

from __future__ import annotations

import threading

import pytest

from codeverse.models.keypool import KeyPool, KeyPoolExhausted, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def make(keys=("k1", "k2", "k3"), **kw) -> tuple[KeyPool, Clock]:
    c = Clock()
    return KeyPool(list(keys), clock=c, sleep=c.sleep, **kw), c


def test_round_robin_order():
    pool, _ = make()
    assert [pool.acquire() for _ in range(4)] == ["k1", "k2", "k3", "k1"]


def test_dedupes_and_rejects_empty():
    pool, _ = make(keys=("a", "a", "", "b"))
    assert pool.keys == ["a", "b"]
    with pytest.raises(ValueError):
        KeyPool([])


def test_429_cools_key_down_and_rotates():
    pool, clock = make(cooldown_s=30)
    k = pool.acquire()
    assert k == "k1"
    pool.report(k, "429")
    # next acquires skip k1 while it cools
    assert [pool.acquire() for _ in range(4)] == ["k2", "k3", "k2", "k3"]
    clock.t += 31
    assert "k1" in [pool.acquire() for _ in range(3)]
    st = pool.stats()
    assert st["429"] == 1 and st["n_keys"] == 3


def test_exclude_skips_failed_keys():
    pool, _ = make()
    assert pool.acquire(exclude={"k1", "k2"}) == "k3"
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(exclude={"k1", "k2", "k3"})


def test_rpm_bucket_blocks_then_refills():
    pool, clock = make(keys=("solo",), rpm_per_key=60)  # 1 token / s, capacity 60
    for _ in range(60):
        pool.acquire(timeout_s=0.0)
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(timeout_s=0.0)
    # blocking acquire advances the fake clock until a token refills
    t0 = clock.t
    assert pool.acquire(timeout_s=10.0) == "solo"
    assert clock.t > t0


def test_all_cooling_blocks_until_cooldown_ends():
    pool, clock = make(keys=("a", "b"), cooldown_s=5)
    for k in ("a", "b"):
        pool.report(k, "429")
    t0 = clock.t
    assert pool.acquire(timeout_s=60) in ("a", "b")
    assert clock.t - t0 >= 5 - 1e-6
    with pytest.raises(KeyPoolExhausted):
        pool.report("a", "429")
        pool.report("b", "429")
        pool.acquire(timeout_s=1.0)


def test_health_score_prefers_healthy_keys():
    pool, clock = make(keys=("bad", "good"), cooldown_s=0.0)
    for _ in range(3):
        pool.report("bad", "429")  # health 0.125, cooldown 0 → still "available"
    picks = [pool.acquire() for _ in range(4)]
    assert picks == ["good"] * 4
    pool.report("good", "ok")
    s = {k["key"]: k for k in pool.stats()["keys"]}
    assert s["…good"]["health"] == 1.0 and s["…bad"]["health"] < 0.3
    # passive recovery over time
    clock.t += 200
    assert pool.acquire() in ("bad", "good")
    s = {k["key"]: k for k in pool.stats()["keys"]}
    assert s["…bad"]["health"] > 0.9


def test_tpm_bucket_honours_hint():
    pool, _ = make(keys=("a", "b"), tpm_per_key=1000)
    assert pool.acquire(tokens_hint=800) == "a"
    assert pool.acquire(tokens_hint=800) == "b"  # a has only 200 left
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(tokens_hint=800, timeout_s=0.0)


def test_thread_safety_smoke():
    pool = KeyPool([f"k{i}" for i in range(5)], rpm_per_key=100000)
    counts: dict[str, int] = {}
    lock = threading.Lock()

    def worker():
        for _ in range(200):
            k = pool.acquire()
            pool.report(k, "ok")
            with lock:
                counts[k] = counts.get(k, 0) + 1

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sum(counts.values()) == 1600 and len(counts) == 5


def test_token_bucket_math():
    b = TokenBucket(capacity=10, rate=1.0, updated=0.0)
    assert b.try_take(10, 0.0) and not b.try_take(1, 0.0)
    assert b.wait_for(5, 0.0) == 5.0
    assert b.try_take(5, 5.0)
