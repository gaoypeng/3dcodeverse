"""KeyPool: rotation, 429 cooldown, health, exhaustion, the in-flight cap, stats."""

from __future__ import annotations

import pytest

from codeverse3d.models.retry import KeyPool, KeyPoolExhausted


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


def test_dead_key_is_benched_for_an_hour_and_all_dead_raises_at_once():
    pool, clock = make(keys=("dead", "ok"), cooldown_s=30, dead_cooldown_s=3600)
    pool.report("dead", "dead")
    # passive health recovery must not bring it back: it stays benched for an hour
    clock.t += 1800
    assert [pool.acquire() for _ in range(6)] == ["ok"] * 6
    clock.t += 1801
    assert "dead" in [pool.acquire() for _ in range(3)]  # re-probed once the bench ends
    # every key dead: raise at once instead of waiting out the timeout
    pool, clock = make(keys=("a", "b"), dead_cooldown_s=3600)
    pool.report("a", "dead")
    pool.report("b", "dead")
    t0 = clock.t
    with pytest.raises(KeyPoolExhausted):
        pool.acquire(timeout_s=120)
    assert clock.t == t0  # no pointless 120 s wait: availability only moves later


def test_a_session_lease_keeps_one_session_per_key_across_pools_until_keys_run_out(tmp_path):
    """Two pools = two processes: each leases a key no other live session holds; all leased -> shared (None)."""
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
