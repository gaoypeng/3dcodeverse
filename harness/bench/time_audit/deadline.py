"""Deadline overshoots: sessions past their 1800-s job timeout, runs killed by the
bench max_minutes with no round completed, and the storm-streak depth at each
'giving up after 900 s' (=> seconds per failed 503 attempt, independent of transcripts)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from corpus import discover, fmt, med_p90, trajectories  # noqa: E402

STORM_RE = re.compile(r"gemini (\S+) capacity storm (\d+)/60 ")
GIVEUP_RE = re.compile(r"gemini (\S+) giving up after (\d+) s")
BUDGET_RE = re.compile(r"elapsed ([\d.]+) min exceeds max_minutes ([\d.]+)")
SESSION_TIMEOUT_S = 1800.0


def main() -> None:
    refs = discover()
    over, killed, zero_rounds, depths = [], [], 0, []
    for ref in refs:
        if ref.bucket != "storm(08-26)":
            continue
        for _, res, _ in trajectories(ref):
            d = float(res.get("duration_s") or 0)
            if d > SESSION_TIMEOUT_S and res.get("exit_reason") in ("timeout", "error"):
                over.append(d - SESSION_TIMEOUT_S)
        rounds_done = sum(1 for e in ref.events if e["event"] == "round.done")
        for e in ref.events:
            if e["event"] == "budget.exceeded":
                m = BUDGET_RE.search(str(e.get("reason", "")))
                if m:
                    killed.append((float(m.group(1)) - float(m.group(2))) * 60)
                    zero_rounds += rounds_done == 0
        cd = ref.cell_dir
        if cd is None:
            continue
        last = 0
        for line in (cd / "worker.log").read_text(errors="replace").splitlines():
            m = STORM_RE.match(line)
            if m and "flash" in m.group(1):
                last = int(m.group(2))
            elif GIVEUP_RE.match(line) and "flash" in line:
                depths.append(last)
    m, p, n = med_p90(over)
    print(f"sessions past the {SESSION_TIMEOUT_S:.0f}-s job timeout (exit timeout/error): n={n} overshoot med/p90 = {fmt(m)}/{fmt(p)} s, sum={sum(over):.0f} s")
    m, p, n = med_p90(killed)
    print(f"runs stopped by bench max_minutes: n={n}, overshoot past the ceiling med/p90 = {fmt(m)}/{fmt(p)} s; with ZERO completed rounds: {zero_rounds}")
    m, p, n = med_p90([float(d) for d in depths])
    if n:
        print(f"flash give-ups in fancy_v1 logs: n={n}; storm-streak depth at give-up med/p90 = {fmt(m)}/{fmt(p)} "
              f"=> 900 s / depth = {fmt(900 / m, 1)} s per failed attempt (sleep included, <= 5 s of it)")
    print(json.dumps({"overshoot_sum_s": round(sum(over)), "killed": len(killed), "zero_rounds": zero_rounds}))


if __name__ == "__main__":
    main()
