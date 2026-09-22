"""The machine-wide in-flight slots (``retry.Slots``): flock'd lock files every process shares.

Real processes, real threads, real clocks — the owner asked for the file lock to be tested,
not assumed.  Every test measures with ``time.monotonic`` (CLOCK_MONOTONIC is one clock for
the whole machine on Linux) and prints what it measured (``pytest -s`` shows it).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from codeverse3d.models.retry import KeyPoolExhausted, Slots

HARNESS = Path(__file__).resolve().parents[2]

#: a child that takes a slot ``holds`` times for ``hold_s`` each, after a "go" on stdin,
#: and prints one JSON line per hold: [monotonic taken, monotonic about to give]
_CONTENDER = """
import json, sys, time
from codeverse3d.models.retry import Slots
root, n, holds, hold_s = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
slots = Slots(n, root)
print("ready", flush=True)
sys.stdin.readline()
for _ in range(holds):
    fd = slots.take(30.0)
    t0 = time.monotonic()
    time.sleep(hold_s)
    t1 = time.monotonic()
    Slots.give(fd)
    print(json.dumps([t0, t1]), flush=True)
"""

#: a child that takes a slot, says so, and then sleeps until it is killed
_HOLDER = """
import sys, time
from codeverse3d.models.retry import Slots
fd = Slots(int(sys.argv[2]), sys.argv[1]).take(10.0)
print("held", flush=True)
time.sleep(600)
"""


def _python(code: str, *args: object) -> subprocess.Popen:
    """A child on THIS tree's codeverse3d (the editable install may point at another checkout)."""
    env = {**os.environ, "PYTHONPATH": str(HARNESS)}
    return subprocess.Popen([sys.executable, "-c", code, *map(str, args)], cwd=HARNESS, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _peak(intervals: list[tuple[float, float]]) -> int:
    """Most intervals open at one instant; a give at the same instant as a take is not overlap."""
    events = sorted([(t0, 1) for t0, _ in intervals] + [(t1, -1) for _, t1 in intervals])
    peak = cur = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return peak


def test_n_plus_k_processes_never_have_more_than_n_in_flight(tmp_path: Path) -> None:
    """(a) 8 processes contend for 4 slots, 6 holds of 0.15 s each: at no instant do more
    than 4 hold one, all 4 are used, and the whole workload drains at the rate 4 slots allow."""
    n, k, holds, hold_s = 4, 4, 6, 0.15
    procs = [_python(_CONTENDER, tmp_path, n, holds, hold_s) for _ in range(n + k)]
    try:
        for p in procs:
            assert p.stdout.readline().strip() == "ready", p.stderr.read()
        t_go = time.monotonic()
        for p in procs:
            p.stdin.write("go\n")
            p.stdin.flush()
        intervals: list[tuple[float, float]] = []
        for p in procs:
            out, err = p.communicate(timeout=60)
            assert p.returncode == 0, err
            intervals += [tuple(json.loads(line)) for line in out.splitlines() if line.startswith("[")]
    finally:
        for p in procs:
            p.kill()
    wall = max(t1 for _, t1 in intervals) - t_go
    busy_s = sum(t1 - t0 for t0, t1 in intervals)
    peak = _peak(intervals)
    print(f"\n(a) {n + k} processes x {holds} holds x {hold_s}s on {n} slots: peak {peak} in flight, "
          f"mean {busy_s / wall:.2f}, wall {wall:.2f}s (floor {(n + k) * holds * hold_s / n:.2f}s)")
    assert len(intervals) == (n + k) * holds
    assert peak == n, f"{peak} in flight at once on {n} slots"
    assert wall >= (n + k) * holds * hold_s / n - 0.05, "finished faster than 4 slots allow"


def test_a_smaller_cap_uses_only_its_own_first_slots(tmp_path: Path) -> None:
    """Processes with different caps share one directory: three at N=2 and three at N=4 never
    exceed 4 together, and the N=2 three never exceed 2 among themselves — the machine is
    held to the largest cap anyone asks for, and each process to its own."""
    small = [_python(_CONTENDER, tmp_path, 2, 4, 0.1) for _ in range(3)]
    big = [_python(_CONTENDER, tmp_path, 4, 4, 0.1) for _ in range(3)]
    runs: dict[str, list[tuple[float, float]]] = {"small": [], "big": []}
    try:
        for p in small + big:
            assert p.stdout.readline().strip() == "ready", p.stderr.read()
        for p in small + big:
            p.stdin.write("go\n")
            p.stdin.flush()
        for name, group in (("small", small), ("big", big)):
            for p in group:
                out, err = p.communicate(timeout=60)
                assert p.returncode == 0, err
                runs[name] += [tuple(json.loads(line)) for line in out.splitlines() if line.startswith("[")]
    finally:
        for p in small + big:
            p.kill()
    both, alone = _peak(runs["small"] + runs["big"]), _peak(runs["small"])
    print(f"\n(a') caps 2 and 4 in one directory: peak {both} together, {alone} among the N=2 processes")
    assert len(runs["small"]) == len(runs["big"]) == 12
    assert both <= 4 and alone <= 2


def test_a_process_killed_holding_a_slot_frees_it_at_once(tmp_path: Path) -> None:
    """(b) SIGKILL runs no cleanup code; the kernel drops a dead process's flock anyway, so
    a waiter gets the slot within one poll — no stale-lock timeout, no pid file to reap."""
    slots = Slots(1, tmp_path)
    child = _python(_HOLDER, tmp_path, 1)
    try:
        assert child.stdout.readline().strip() == "held", child.stderr.read()
        assert slots.try_take() is None and slots.busy() == 1, "the child holds the only slot"
        t_kill = time.monotonic()
        os.kill(child.pid, signal.SIGKILL)
        fd = slots.take(timeout_s=5.0)
        freed_after = time.monotonic() - t_kill
    finally:
        child.kill()
        child.wait(10)
    Slots.give(fd)
    print(f"\n(b) slot back {freed_after * 1000:.0f} ms after SIGKILL (poll {Slots.POLL_S * 1000:.0f} ms)")
    assert child.returncode == -signal.SIGKILL
    assert freed_after < 1.0


def test_threads_of_one_process_share_the_slots(tmp_path: Path) -> None:
    """(c) flock locks an open file DESCRIPTION and every take opens its own, so threads of
    one process exclude each other exactly like processes do: 12 threads on 3 slots never
    hold more than 3, and two threads cannot both hold a 1-slot pool."""
    one = Slots(1, tmp_path / "one")
    fd = one.take(1.0)
    got: list[int | None] = []
    t = threading.Thread(target=lambda: got.append(one.try_take()))
    t.start()
    t.join(5)
    assert got == [None], "a second thread of the same process took the held slot"
    Slots.give(fd)

    n, threads, holds, hold_s = 3, 12, 4, 0.05
    slots = Slots(n, tmp_path / "three")
    lock = threading.Lock()
    intervals: list[tuple[float, float]] = []

    def worker() -> None:
        for _ in range(holds):
            fd = slots.take(30.0)
            t0 = time.monotonic()
            time.sleep(hold_s)
            t1 = time.monotonic()
            Slots.give(fd)
            with lock:
                intervals.append((t0, t1))

    t_go = time.monotonic()
    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for w in pool:
        w.start()
    for w in pool:
        w.join(60)
    wall = time.monotonic() - t_go
    peak = _peak(intervals)
    print(f"\n(c) {threads} threads x {holds} holds x {hold_s}s on {n} slots: peak {peak}, "
          f"wall {wall:.2f}s (floor {threads * holds * hold_s / n:.2f}s)")
    assert len(intervals) == threads * holds and peak == n
    assert slots.busy() == 0, "every slot came back"


@pytest.mark.parametrize("timeout_s", [0.0, 0.3])
def test_a_waiter_gives_up_at_its_deadline(tmp_path: Path, timeout_s: float) -> None:
    """(d) Every slot held by another process: a waiter raises the pool's own error at its
    deadline — not before it, and without sleeping on past it (the last nap is clipped)."""
    slots = Slots(2, tmp_path)
    holders = [_python(_HOLDER, tmp_path, 2) for _ in range(2)]
    try:
        for h in holders:
            assert h.stdout.readline().strip() == "held", h.stderr.read()
        t0 = time.monotonic()
        with pytest.raises(KeyPoolExhausted, match="in-flight slots"):
            slots.take(timeout_s=timeout_s)
        waited = time.monotonic() - t0
    finally:
        for h in holders:
            h.kill()
            h.wait(10)
    print(f"\n(d) deadline {timeout_s}s: gave up after {waited:.3f}s")
    assert timeout_s <= waited < timeout_s + 0.2
