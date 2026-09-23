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
import time
from pathlib import Path

from codeverse3d.models.retry import Slots

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


