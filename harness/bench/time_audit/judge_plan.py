"""Judge + planner stages: stage span vs recorded model latency (the difference is
retry/timeout waiting), per corpus; judge samples that errored and what they cost."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from corpus import cost_rows, discover, fmt, med_p90  # noqa: E402


def main() -> None:
    J: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for ref in discover():
        rows = cost_rows(ref)
        by_round: dict[int, list[dict]] = defaultdict(list)
        for r in rows:
            if r["role"] == "judge":
                by_round[int(r.get("round") or 0)].append(r)
        b = J[ref.bucket]
        for e in ref.events:
            if e["event"] == "judge.done":
                d = float(e.get("duration_s") or 0)
                rs = by_round.get(int(e.get("round") or 0), [])
                ok = sum(r["latency_ms"] / 1000 for r in rs if r["outcome"] == "ok")
                err = [r["latency_ms"] / 1000 for r in rs if r["outcome"] != "ok"]
                b["judge_s"].append(d)
                b["judge_ok_lat"].append(ok)
                b["judge_wait"].append(max(0.0, d - ok))
                b["judge_n_ok"].append(float(sum(1 for r in rs if r["outcome"] == "ok")))
                if err:
                    b["judge_err_rows"].extend(err)
                    b["judge_rounds_with_err"].append(d)
            elif e["event"] == "stage.done" and e.get("stage") == "plan":
                d = float(e.get("duration_s") or 0)
                pl = [r for r in rows if r["role"] == "planner"]
                ok = sum(r["latency_ms"] / 1000 for r in pl if r["outcome"] == "ok")
                b["plan_s"].append(d)
                b["plan_ok_lat"].append(ok)
                b["plan_calls"].append(float(len(pl)))
                b["plan_wait"].append(max(0.0, d - ok))
    for bucket, b in sorted(J.items()):
        print(f"## {bucket}")
        for k in ("judge_s", "judge_ok_lat", "judge_wait", "judge_n_ok", "plan_s", "plan_ok_lat", "plan_calls", "plan_wait"):
            m, p, n = med_p90(b[k])
            print(f"   {k:14s} n={n:3d} med={fmt(m, 1):>7s} p90={fmt(p, 1):>7s} sum={sum(b[k]):.0f}")
        er = b["judge_err_rows"]
        print(f"   judge error rows: n={len(er)} sum={sum(er):.0f} s  each: {[round(x) for x in sorted(er, reverse=True)[:8]]}")
        rw = b["judge_rounds_with_err"]
        print(f"   judge rounds with an errored sample: {len(rw)} / {len(b['judge_s'])}; their judge_s: {[round(x) for x in sorted(rw, reverse=True)[:8]]}")


if __name__ == "__main__":
    main()
