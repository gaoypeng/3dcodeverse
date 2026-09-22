"""KeyPool: rotation, 429 cooldown, health, exhaustion, the in-flight cap, stats."""

from __future__ import annotations

import threading
import time

import pytest

from codeverse3d.models.retry import MAX_WAIT_S, KeyPool, KeyPoolExhausted


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


def test_all_cooling_blocks_until_cooldown_ends():
    # a cooldown longer than the house cap is clipped to it: no single wait may
    # exceed MAX_WAIT_S, patience comes from the number of attempts
    pool, clock = make(keys=("a", "b"), cooldown_s=5)
    for k in ("a", "b"):
        pool.report(k, "429")
    t0 = clock.t
    assert pool.acquire(timeout_s=60) in ("a", "b")
    assert MAX_WAIT_S - 1e-6 <= clock.t - t0 <= 5 + 1e-6
    pool.report("a", "429")
    pool.report("b", "429")
    with pytest.raises(KeyPoolExhausted):
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


def test_thread_safety_smoke():
    pool = KeyPool([f"k{i}" for i in range(5)])
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


def test_dead_key_is_benched_for_a_long_time_then_reprobed():
    pool, clock = make(keys=("dead", "ok"), cooldown_s=30, dead_cooldown_s=3600)
    pool.report("dead", "dead")
    # passive health recovery must not bring it back: it stays benched for an hour
    clock.t += 1800
    assert [pool.acquire() for _ in range(6)] == ["ok"] * 6
    st = pool.stats()
    assert st["n_dead"] == 1 and st["dead"] == 1
    by = {k["key"]: k for k in st["keys"]}
    assert by["…dead"]["dead"] == 1 and by["…dead"]["cooldown_s"] > 0
    clock.t += 1801
    assert "dead" in [pool.acquire() for _ in range(3)]  # re-probed once the bench ends
    assert pool.stats()["n_dead"] == 0


def test_all_keys_dead_raises_immediately_instead_of_waiting_out_the_timeout():
    pool, clock = make(keys=("a", "b"), dead_cooldown_s=3600)
    pool.report("a", "dead")
    pool.report("b", "dead")
    t0 = clock.t
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(timeout_s=120)
    assert clock.t == t0  # no pointless 120 s wait: availability only moves later


def test_try_acquire_never_waits_for_a_key_or_a_slot():
    """The extra keys of a hedged retry (``rotate_with_retries(hedge=...)``) come from
    ``try_acquire``: a key that is usable right now, else None — never a wait."""
    never = lambda s: (_ for _ in ()).throw(AssertionError(f"try_acquire slept {s}s"))  # noqa: E731
    pool = KeyPool(["k1", "k2"], max_in_flight=2, cooldown_s=30, sleep=never)
    a = pool.try_acquire()
    assert a == "k1" and pool.stats()["in_flight"] == 1
    assert pool.try_acquire(exclude={a}) == "k2"
    assert pool.try_acquire() is None, "both max_in_flight slots are held"
    pool.release()
    pool.release()
    assert pool.stats()["in_flight"] == 0
    pool.report("k1", "429")
    pool.report("k2", "429")
    assert pool.try_acquire() is None, "every key is cooling down: no wait, no key"
    assert pool.stats()["in_flight"] == 0, "a refused try_acquire holds nothing"


def test_the_in_flight_slot_wait_is_bounded_by_timeout():
    """acquire()'s max_in_flight semaphore used to be an unbounded wait; the same
    timeout budget now bounds it and raises the pool's own error shape."""
    pool = KeyPool(["a"], max_in_flight=1)
    pool.acquire()
    t0 = time.monotonic()
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(timeout_s=0.05)
    assert time.monotonic() - t0 < 2.0, "the slot wait must honour the timeout"
    pool.release()
    assert pool.acquire(timeout_s=0.5) == "a", "the slot came back; nothing leaked"


def test_skip_leaves_health_and_counters_alone():
    """A charged-but-invalid reply (bad JSON) is not the key's fault: it used to be
    reported "ok", boosting health and counting as a success in doctor --live."""
    pool, _ = make(keys=("a",))
    pool.report("a", "429")
    before = {k["key"]: k for k in pool.stats()["keys"]}["…a"]
    pool.report("a", "skip")
    after = {k["key"]: k for k in pool.stats()["keys"]}["…a"]
    assert after["health"] == before["health"] and after["ok"] == before["ok"] and after["error"] == before["error"]


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
    pool = KeyPool(["a", "b", "c"], max_in_flight=2)
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
    pool = KeyPool(["a"], max_in_flight=1)
    for _ in range(3):
        with pytest.raises(KeyPoolExhausted):
            pool.acquire(exclude={"a"}, timeout_s=0.0)
    assert pool.acquire(timeout_s=0.0) == "a"  # the slot is still free
