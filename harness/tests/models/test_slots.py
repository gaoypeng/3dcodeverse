"""The machine-wide in-flight slots (``retry.Slots``): flock'd lock files every process shares, tested with real processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

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
    peak = _peak(intervals)
    assert len(intervals) == (n + k) * holds
    assert peak == n, f"{peak} in flight at once on {n} slots"
    assert wall >= (n + k) * holds * hold_s / n - 0.05, "finished faster than 4 slots allow"


