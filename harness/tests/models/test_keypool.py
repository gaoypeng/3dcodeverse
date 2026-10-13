"""KeyPool: rotation, 429 cooldown, health, exhaustion, the in-flight cap, stats."""

from __future__ import annotations

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


def test_dedupes_and_rejects_empty():
    pool, _ = make(keys=("a", "a", "", "b"))
    assert pool.keys == ["a", "b"]
    with pytest.raises(ValueError):
        KeyPool([])


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


def test_dead_key_is_benched_for_an_hour_and_all_dead_raises_at_once():
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
    # every key dead: raise at once instead of waiting out the timeout
    pool, clock = make(keys=("a", "b"), dead_cooldown_s=3600)
    pool.report("a", "dead")
    pool.report("b", "dead")
    t0 = clock.t
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(timeout_s=120)
    assert clock.t == t0  # no pointless 120 s wait: availability only moves later


def test_try_acquire_never_waits_for_a_key_or_a_slot(tmp_path):
    """The extra keys of a hedged retry (``rotate_with_retries(hedge=...)``) come from
    ``try_acquire``: a key that is usable right now, else None — never a wait."""
    never = lambda s: (_ for _ in ()).throw(AssertionError(f"try_acquire slept {s}s"))  # noqa: E731
    pool = KeyPool(["k1", "k2"], max_in_flight=2, slots_dir=tmp_path, cooldown_s=30, sleep=never)
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


def test_the_in_flight_slot_wait_is_bounded_and_a_failed_acquire_gives_it_back(tmp_path):
    pool = KeyPool(["a"], max_in_flight=1, slots_dir=tmp_path)
    pool.acquire()
    t0 = time.monotonic()
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(timeout_s=0.05)
    assert time.monotonic() - t0 < 2.0, "the slot wait must honour the timeout"
    pool.release()
    assert pool.acquire(timeout_s=0.5) == "a", "the slot came back; nothing leaked"
    pool.release()
    for _ in range(3):  # KeyPoolExhausted must not leak a concurrency slot either
        with pytest.raises(KeyPoolExhausted):
            pool.acquire(exclude={"a"}, timeout_s=0.0)
    assert pool.acquire(timeout_s=0.0) == "a"  # the slot is still free


def test_a_capped_pool_names_its_slot_directory_and_a_session_key_takes_no_slot(tmp_path):
    """The slots are machine-wide lock files, so a cap without a directory is refused rather
    than silently becoming a per-process cap; a vendor CLI session picks a key (rotation and
    cooldowns included) but holds no slot and has nothing to release."""
    with pytest.raises(ValueError, match="slots_dir"):
        KeyPool(["a"], max_in_flight=1)
    pool = KeyPool(["a", "b"], max_in_flight=1, slots_dir=tmp_path)
    assert pool.acquire(timeout_s=0.0) == "a"
    assert pool.session_key(timeout_s=0.0) == "b", "the only slot is held, a session still gets a key"
    pool.report("a", "429")
    assert pool.session_key(timeout_s=0.0) == "b", "and it skips a cooling key like acquire does"
    assert pool.stats()["in_flight"] == 1 and pool.slots.busy() == 1
    pool.release()
    assert pool.stats()["in_flight"] == 0 and pool.slots.busy() == 0


def test_the_rotation_starts_where_it_is_told_and_the_shared_pool_starts_anywhere(monkeypatch, tmp_path):
    """Parallel ``3dcode make`` processes make the same calls in the same order: every pool
    starting at key 0 put four runs' planner calls on one key (live, 2026-10-13)."""
    from codeverse3d.config import get_settings
    from codeverse3d.models import gemini

    pool, _ = make(start=4)
    assert [pool.session_key() for _ in range(3)] == ["k2", "k3", "k1"]
    monkeypatch.setattr(get_settings(), "cache_dir", tmp_path)
    firsts = set()
    for _ in range(40):
        gemini._pools.clear()
        firsts.add(gemini.shared_pool([f"s{j}" for j in range(12)], max_in_flight=0).session_key())
    assert len(firsts) > 3
    gemini._pools.clear()


def test_a_session_lease_keeps_one_session_per_key_across_pools_until_keys_run_out(tmp_path):
    """Two pools stand in for two ``3dcode make`` processes: each session leases a key no
    other live session holds; with every key leased the next one shares (lease ``None``);
    closing a lease frees its key for the next session."""
    a, _ = make(keys=("k1", "k2"))
    b, _ = make(keys=("k1", "k2"))
    k1, l1 = a.lease_session_key(tmp_path)
    k2, l2 = b.lease_session_key(tmp_path)
    assert {k1, k2} == {"k1", "k2"} and l1 is not None and l2 is not None
    k3, l3 = a.lease_session_key(tmp_path)
    assert k3 in ("k1", "k2") and l3 is None   # every key in a session: shared, not refused
    KeyPool.end_lease(l1)
    k4, l4 = b.lease_session_key(tmp_path)
    assert (k4, l4 is not None) == (k1, True)
    KeyPool.end_lease(l2)
    KeyPool.end_lease(l4)
