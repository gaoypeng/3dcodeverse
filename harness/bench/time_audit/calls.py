"""Task 2: per-model-call latency distributions (baseline vs storm day), calls per
run, calls with >= 1 retry, and total retry-wait per run; plus the worker.log storm
lines for the fancy_v1 cells (the only corpus whose driver keeps a per-cell log)."""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from corpus import RunRef, cost_rows, discover, fmt, med_p90, trajectories  # noqa: E402
from turns import parse_turns  # noqa: E402

RETRY_THRESHOLD_S = 1.0  # min storm sleep is 1.5 s (2^1 * 0.75); 429 rotation pause 0.5 s
STORM_RE = re.compile(r"gemini (\S+) capacity storm (\d+)/60 .*; waiting (\d+)s")
FAIL_RE = re.compile(r"gemini (\S+) attempt (\d)/6 failed \((.*?)\); retrying in ([\d.]+)s")
GIVEUP_RE = re.compile(r"gemini (\S+) giving up after (\d+) s")


def pct(xs: list[float]) -> str:
    if not xs:
        return "-"
    s = sorted(xs)
    q = lambda p: s[min(len(s) - 1, int(round(p * (len(s) - 1))))]  # noqa: E731
    return f"n={len(s)} p50={q(.5):.1f} p90={q(.9):.1f} p99={q(.99):.1f} max={q(1):.0f}"


def latency_table(refs: list[RunRef]) -> None:
    lat: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    per_run: dict[tuple[str, str], list[int]] = defaultdict(list)
    err: Counter[tuple[str, str]] = Counter()
    for ref in refs:
        n: Counter[str] = Counter()
        for r in cost_rows(ref):
            key = (ref.bucket, r["model"], r["role"])
            if r["outcome"] == "ok":
                lat[key].append(r["latency_ms"] / 1000)
                n[r["model"]] += 1
            else:
                err[(ref.bucket, r["model"])] += 1
        for m, c in n.items():
            per_run[(ref.bucket, m)].append(c)
    print("## successful-call latency (s) by corpus x model x role")
    for k in sorted(lat):
        print(f"  {k[0]:14s} {k[1]:24s} {k[2]:9s} {pct(lat[k])}")
    print("## ok calls per run (median / p90) and error rows")
    for k in sorted(per_run):
        m, p, cnt = med_p90([float(x) for x in per_run[k]])
        print(f"  {k[0]:14s} {k[1]:24s} runs={cnt} calls/run {fmt(m)}/{fmt(p)}  error rows={err[k]}")


def retry_table(refs: list[RunRef]) -> None:
    print("## generator turns (transcripts): waiting = wall - recorded latency")
    agg: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ref in refs:
        turns = [t for _, _, tr in trajectories(ref) for t in parse_turns(tr)]
        if not turns:
            continue
        real = [t for t in turns if t.latency_s > 0]
        retried = [t for t in real if t.wait_s > RETRY_THRESHOLD_S]
        a = agg[(ref.bucket, ref.track)]
        a["calls"].append(len(real))
        a["retried"].append(len(retried))
        a["retried_pct"].append(100.0 * len(retried) / max(1, len(real)))
        a["wait_s"].append(sum(t.wait_s for t in real) + sum(t.error_s for t in turns))
        a["wait_per_retried"].extend(t.wait_s for t in retried)
        a["errors"].append(sum(1 for t in turns if t.error_s > 0))
    for k in sorted(agg):
        a = agg[k]
        c, cp, n = med_p90(a["calls"])
        r, rp, _ = med_p90(a["retried"])
        pc, _, _ = med_p90(a["retried_pct"])
        w, wp, _ = med_p90(a["wait_s"])
        wr, wrp, _ = med_p90(a["wait_per_retried"])
        e = sum(a["errors"])
        print(f"  {k[0]:14s} {k[1]:16s} runs={n:2d} calls/run {fmt(c)}/{fmt(cp)}  retried/run {fmt(r)}/{fmt(rp)} "
              f"({fmt(pc)}%)  wait/run {fmt(w)}/{fmt(wp)} s  wait/retried-call {fmt(wr, 1)}/{fmt(wrp, 1)} s  model_error rows={e}")


def worker_logs(refs: list[RunRef]) -> None:
    print("## fancy_v1 worker.log (per cell; sleeps only — the failed round-trips are not logged)")
    per: dict[str, list[float]] = defaultdict(list)
    tot: Counter[str] = Counter()
    sleep: Counter[str] = Counter()
    depth: Counter[int] = Counter()
    for ref in refs:
        cd = ref.cell_dir
        if cd is None:
            continue
        s_flash = s_pro = 0.0
        for line in (cd / "worker.log").read_text(errors="replace").splitlines():
            m = STORM_RE.match(line)
            if m:
                tot[f"storm:{m.group(1)}"] += 1
                sleep[m.group(1)] += int(m.group(3))
                depth[int(m.group(2))] += 1
                if "flash" in m.group(1):
                    s_flash += int(m.group(3))
                else:
                    s_pro += int(m.group(3))
                continue
            m = FAIL_RE.match(line)
            if m:
                tot[f"fail:{m.group(1)}:{m.group(3)[:28]}"] += 1
                continue
            m = GIVEUP_RE.match(line)
            if m:
                tot[f"giveup:{m.group(1)}"] += 1
        per["flash_sleep_s"].append(s_flash)
        per["pro_sleep_s"].append(s_pro)
    for k, v in sorted(tot.items()):
        print(f"  {v:5d}  {k}")
    for k, v in sleep.items():
        print(f"  total logged sleep {k}: {v} s over {len(per['flash_sleep_s'])} cells")
    for k in ("flash_sleep_s", "pro_sleep_s"):
        m, p, n = med_p90(per[k])
        print(f"  {k} per cell med/p90 = {fmt(m)}/{fmt(p)} (n={n})")
    mx = max(depth) if depth else 0
    print(f"  storm streak depth: max {mx}; streaks >= 10: {sum(v for d, v in depth.items() if d >= 10)} lines; "
          f"streak 1: {depth[1]} (= distinct storm episodes)")


if __name__ == "__main__":
    refs = discover()
    latency_table(refs)
    retry_table(refs)
    worker_logs(refs)
