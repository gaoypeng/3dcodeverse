"""The rate table behind a :mod:`bench.plan_stage_bench` run.

Per arm: valid / ``PlanningError`` / provider counts, the failure rate with a Wilson
interval, the per-prompt split, and the distinct validation failures.  Provider failures
are excluded from the denominator — they say nothing about the code under test.

    python bench/plan_stage_report.py bench/data/plan_stage/*.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def outcome(row: dict) -> str:
    if row["ok"]:
        return "valid"
    return "planning_error" if "PlanningError" in row.get("error", "") else "provider"


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — usable at the counts a rate this small produces."""
    if not n:
        return (float("nan"), float("nan"))
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.is_file() else []


def report(arms: dict[str, list[dict]]) -> str:
    lines = ["| arm | calls | valid | planning_error | provider | failure rate | 95 % CI |",
             "|---|--:|--:|--:|--:|--:|---|"]
    for name, rows in arms.items():
        c = Counter(outcome(r) for r in rows)
        judged = len(rows) - c["provider"]
        lo, hi = wilson(c["planning_error"], judged)
        rate = c["planning_error"] / judged if judged else float("nan")
        lines.append(f"| {name} | {len(rows)} | {c['valid']} | {c['planning_error']} | {c['provider']} | "
                     f"{rate:.3f} | [{lo:.3f}, {hi:.3f}] |")
    prompts = sorted({r["prompt"] for rows in arms.values() for r in rows})
    if len(arms) > 1 and prompts:
        lines += ["", "| prompt | " + " | ".join(f"{a} valid/judged" for a in arms) + " |",
                  "|---|" + "--:|" * len(arms)]
        for prompt in prompts:
            cells = []
            for rows in arms.values():
                judged = [r for r in rows if r["prompt"] == prompt and outcome(r) != "provider"]
                cells.append(f"{sum(1 for r in judged if r['ok'])}/{len(judged)}")
            lines.append(f"| {prompt} | " + " | ".join(cells) + " |")
    failures = Counter()
    for name, rows in arms.items():
        for r in rows:
            if outcome(r) == "planning_error":
                failures[f"{name}: {r['error'].split('Value error, ')[-1][:90]}"] += 1
    if failures:
        lines += ["", "validation failures:"] + [f"  {n}x {msg}" for msg, n in failures.most_common()]
    fired = {name: [r for r in rows if r.get("restarts")] for name, rows in arms.items()}
    if any(fired.values()):
        lines += ["", "restarts:"] + [
            f"  {name}: fired on {len(rs)}/{len(arms[name])} calls, recovered {sum(1 for r in rs if r['ok'])}"
            for name, rs in fired.items() if rs]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rows", nargs="+", type=Path, help="one JSONL per arm (the file name is the arm name)")
    ns = ap.parse_args(argv)
    print(report({p.stem: load(p) for p in ns.rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
