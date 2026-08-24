"""StormGate: one shared 503 wait instead of one per worker.  Fake clock."""

from __future__ import annotations

import threading

import pytest

from codeverse.models.keypool import MAX_WAIT_S
from codeverse.models.storm import StormGate, all_gates, storm_gate


class Clock:
    """Fake clock; ``sleep`` just moves time forward (single-threaded tests)."""

    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def make(**kw) -> tuple[StormGate, Clock]:
    c = Clock()
    return StormGate("test", clock=c, sleep=c.sleep, **kw), c


def test_open_gate_never_waits():
    gate, clock = make()
    assert gate.enter() == 0.0
    assert clock.t == 100.0
    assert not gate.storming


def test_a_hit_closes_the_gate_and_the_next_worker_parks():
    gate, clock = make(base_delay=4.0)
    closed = gate.hit()
    assert closed == 4.0
    assert gate.storming
    waited = gate.enter()
    assert waited >= 4.0
    assert gate.parked_s >= 4.0


class Parked(Exception):
    """Raised by the test clock the first time a caller is put to sleep."""


def test_one_probe_at_a_time_while_the_storm_lasts():
    """After the window elapses exactly one worker is let through as a probe;
    every other worker parks instead of probing as well."""
    clock = Clock()

    def sleep_once(s: float) -> None:
        clock.t += s
        raise Parked

    gate = StormGate("test", base_delay=1.0, probe_lease_s=30.0, clock=clock, sleep=sleep_once)
    gate.hit()
    clock.t += 5  # the closure window has passed
    assert gate.enter() == 0.0  # this thread is the probe
    assert gate.n_probes == 1
    with pytest.raises(Parked):
        gate.enter()  # a second thread parks on the probe's lease
    assert gate.n_probes == 1
    assert gate.parked_s > 0


def test_a_wedged_probe_cannot_block_forever():
    gate, clock = make(base_delay=1.0, probe_lease_s=10.0)
    gate.hit()
    clock.t += 5
    gate.enter()  # probe 1, which then dies without reporting
    clock.t += 11  # its lease expires
    assert gate.enter() == 0.0
    assert gate.n_probes == 2


def test_a_success_reopens_the_gate_for_everybody():
    gate, clock = make(base_delay=4.0)
    gate.hit()
    gate.ok()
    assert not gate.storming
    assert gate.enter() == 0.0
    assert clock.t == 100.0


def test_no_single_wait_exceeds_the_house_rule():
    """Patience comes from the number of waits, never the length of one."""
    gate, clock = make(base_delay=1.0)
    for _ in range(10):
        assert gate.hit() <= MAX_WAIT_S + 1e-9
    gate2, clock2 = make(base_delay=1.0)
    assert gate2.hit(retry_after_s=600.0) <= MAX_WAIT_S + 1e-9


def test_escalates_then_resets_on_success():
    gate, _ = make(base_delay=0.5)
    first = gate.hit()
    gate2 = gate.hit()
    assert gate2 >= first  # the streak escalates the closure
    gate.ok()
    assert gate.hit() == first  # a success resets the streak


def test_snapshot_counts_storms_not_hits():
    gate, clock = make(base_delay=0.5)
    gate.hit()
    gate.hit()
    gate.hit()
    gate.ok()
    gate.hit()
    snap = gate.snapshot()
    assert snap["hits"] == 4
    assert snap["storms"] == 2
    assert snap["name"] == "test"


def test_registry_is_per_model_and_shared():
    a = storm_gate("gemini:flash-x")
    b = storm_gate("gemini:flash-x")
    c = storm_gate("gemini:pro-x")
    assert a is b
    assert a is not c
    assert a in all_gates() and c in all_gates()


def test_many_workers_share_one_storm_discovery():
    """The point of the gate: N workers meeting a storm cost ONE failed call,
    not N.  Here 12 threads race; only the probes may 'call' the provider."""
    gate = StormGate("race", base_delay=0.05, probe_lease_s=0.5)
    calls: list[int] = []
    lock = threading.Lock()
    stop = threading.Event()
    gate.hit()  # a storm is already running when the workers arrive

    def worker(i: int) -> None:
        gate.enter()
        with lock:
            calls.append(i)
        stop.set()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(12)]
    for t in threads:
        t.start()
    stop.wait(3)
    with lock:
        n_first = len(calls)
    assert n_first <= 2, f"{n_first} workers issued a call into a known storm"
    gate.ok()
    for t in threads:
        t.join(3)
    assert len(calls) == 12  # everybody gets through once the gate reopens
