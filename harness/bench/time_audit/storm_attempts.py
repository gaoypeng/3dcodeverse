"""Price the pieces of "waiting": backoff sleeps (worker.log) vs failed 503 round
trips (transcript wait minus sleep, per storm line) vs 900-s give-ups (model_error
rows / turns with wait >= 850 s), plus cell wall vs run span for the fancy_v1 cells."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from corpus import discover, fmt, med_p90, trajectories  # noqa: E402
from turns import parse_turns  # noqa: E402

STORM_RE = re.compile(r"gemini (\S+) capacity storm (\d+)/60 .*; waiting (\d+)s")
GIVEUP_S = 850.0


def hist(xs: list[float], edges: list[float]) -> str:
    c = Counter()
    for x in xs:
        c[next((e for e in edges if x < e), float("inf"))] += 1
    tot = max(1, len(xs))
    return "  ".join(f"<{e:g}s:{100 * c[e] / tot:.0f}%" for e in [*edges, float("inf")])


def main() -> None:
    refs = discover()
    for bucket in ("base(08-23/25)", "storm(08-26)"):
        waits, big, errs = [], [], []
        for ref in [r for r in refs if r.bucket == bucket]:
            for _, _, tr in trajectories(ref):
                for t in parse_turns(tr):
                    if t.latency_s > 0:
                        waits.append(t.wait_s)
                    if t.error_s > 0:
                        errs.append(t.error_s)
        tot = sum(waits) + sum(errs)
        big = [w for w in waits if w >= GIVEUP_S]
        print(f"## {bucket}: {len(waits)} generator turns; wait histogram: {hist(waits, [1, 5, 30, 120, 850])}")
        print(f"   total wait {tot:.0f} s; turns with wait>=850 s: {len(big)} = {100 * sum(big) / max(1, tot):.0f}% of all wait; "
              f"model_error spans: n={len(errs)} sum={sum(errs):.0f} s ({100 * sum(errs) / max(1, tot):.0f}%) "
              f"med/p90={fmt(med_p90(errs)[0])}/{fmt(med_p90(errs)[1])} s")
        print(f"   wait>=850s turn: {hist(big, [900, 1800, 2700])}")
    print("## fancy_v1 cells: transcript wait vs logged sleep -> cost of one failed 503 round trip")
    rt, per_cell, outside = [], [], []
    for ref in refs:
        cd = ref.cell_dir
        if cd is None:
            continue
        lines = (cd / "worker.log").read_text(errors="replace").splitlines()
        n = sleep = 0
        for m in filter(None, map(STORM_RE.match, lines)):
            if "flash" in m.group(1):
                n += 1
                sleep += int(m.group(3))
        turns = [t for _, _, tr in trajectories(ref) for t in parse_turns(tr)]
        w = sum(t.wait_s for t in turns) + sum(t.error_s for t in turns)
        w_nogiveup = sum(min(t.wait_s, 0) if t.wait_s >= GIVEUP_S else t.wait_s for t in turns)
        if n:
            rt.append((w_nogiveup - sleep) / n)
            per_cell.append((ref.name, n, sleep, round(w), round(w_nogiveup)))
        try:
            cell = json.loads((cd / "cell.json").read_text())
            outside.append(float(cell.get("wall_s") or 0) - (ref.events[-1]["t"] - ref.events[0]["t"]))
        except (OSError, json.JSONDecodeError):
            pass
    m, p, k = med_p90(rt)
    print(f"   per-cell (wait_excluding_giveups - sleep) / storm_lines: med {fmt(m, 1)} s  p90 {fmt(p, 1)} s  (n={k} cells)")
    print(f"   cell wall_s - run event span (pairwise + final pro scoring outside the run): med/p90 = "
          f"{fmt(med_p90(outside)[0])}/{fmt(med_p90(outside)[1])} s")
    for row in sorted(per_cell, key=lambda r: -r[3])[:5]:
        print("   worst:", row)


if __name__ == "__main__":
    main()
