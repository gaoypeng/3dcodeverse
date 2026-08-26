"""Task 3: per agent session (api-agent transcripts) — turns, tool calls by name
with durations, and the model-thinking / tool-execution / waiting split, one table
per track x corpus."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from corpus import discover, fmt, med_p90, trajectories  # noqa: E402
from turns import parse_turns, split  # noqa: E402


def main() -> None:
    sess: dict[tuple[str, str], list[tuple[int, float, float, float, float]]] = defaultdict(list)
    tools: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ref in discover():
        k = (ref.bucket, ref.track)
        for _, res, tr in trajectories(ref):
            turns = parse_turns(tr)
            if not turns:
                continue
            think, tool, wait = split(turns)
            sess[k].append((len([t for t in turns if t.latency_s > 0]), float(res.get("duration_s") or 0), think, tool, wait))
            for t in turns:
                for name, d in t.tools:
                    tools[k][name].append(d)
    print("## per session: turns, duration, and the split (medians; shares are of summed session time)")
    print("| corpus | track | sessions | turns med/p90 | dur s med/p90 | think s | tool s | wait s | think% | tool% | wait% |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for k in sorted(sess):
        rows = sess[k]
        tm, tp, n = med_p90([float(r[0]) for r in rows])
        dm, dp, _ = med_p90([r[1] for r in rows])
        th, _, _ = med_p90([r[2] for r in rows])
        to, _, _ = med_p90([r[3] for r in rows])
        wa, _, _ = med_p90([r[4] for r in rows])
        T = sum(r[2] + r[3] + r[4] for r in rows) or 1.0
        print(f"| {k[0]} | {k[1]} | {n} | {fmt(tm)}/{fmt(tp)} | {fmt(dm)}/{fmt(dp)} | {fmt(th)} | {fmt(to)} | {fmt(wa)} | "
              f"{100 * sum(r[2] for r in rows) / T:.0f} | {100 * sum(r[3] for r in rows) / T:.0f} | {100 * sum(r[4] for r in rows) / T:.0f} |")
    print("\n## tool calls by name: count, median s, p90 s, total s per session (top tools per track; storm-day + baseline pooled by track)")
    pooled: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    nsess: dict[str, int] = defaultdict(int)
    for k, d in tools.items():
        for name, xs in d.items():
            pooled[k[1]][name].extend(xs)
        nsess[k[1]] += len(sess[k])
    for track in sorted(pooled):
        items = sorted(pooled[track].items(), key=lambda kv: -sum(kv[1]))
        print(f"  {track} ({nsess[track]} sessions):")
        for name, xs in items[:12]:
            m, p, n = med_p90(xs)
            print(f"    {name:18s} n={n:5d} med={fmt(m, 2):>6s} p90={fmt(p, 1):>6s} total/session={sum(xs) / max(1, nsess[track]):6.1f} s")


if __name__ == "__main__":
    main()
