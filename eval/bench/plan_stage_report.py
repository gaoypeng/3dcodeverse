"""The rate table behind a :mod:`bench.plan_stage_bench` run.

Per arm: valid / ``PlanningError`` / provider counts, the failure rate with a Wilson
interval, the per-prompt split, and the distinct validation failures.  Provider failures
are excluded from the denominator — they say nothing about the code under test.

    python bench/plan_stage_report.py bench/data/plan_stage/*.jsonl
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench._jsonl import read_jsonl  # noqa: E402

#: what a validation failure was ABOUT, read off the error text.  The mechanism under test
#: (``C3D_PLAN_RESTART``) only ever addresses ``dangling_link``; every other class is a
#: bystander, and a total that mixes them hides both the effect and its residue.
FAILURE_CLASSES: dict[str, str] = {
    "dangling_link": "references unknown link",
    "parent_eq_child": "parent == child",
    "disconnected": "not connected to root",
}


def outcome(row: dict) -> str:
    """``valid`` | ``planning_error`` (the code under test lost the call) | ``provider``.

    ``provider`` is dropped from the denominator, so it is deliberately narrow: only the
    model-side failures the harness cannot help.  A harness-side death — a budget ceiling,
    an unexpected exception — is a loss of the run and must NOT be hidden here, so anything
    that is neither a ``PlanningError`` nor a known provider failure counts as a loss.
    """
    if row["ok"]:
        return "valid"
    err = row.get("error", "")
    if "PlanningError" in err:
        return "planning_error"
    if any(w in err for w in ("ModelError", "BlockedReason", "429", "503", "Deadline", "RESOURCE_EXHAUSTED")):
        return "provider"
    return "planning_error"


def failure_class(row: dict) -> str:
    """Which validation rule the plan broke (``other`` when none of the known ones)."""
    err = row.get("error", "")
    for name, needle in FAILURE_CLASSES.items():
        if needle in err:
            return name
    return "other"


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — usable at the counts a rate this small produces."""
    if not n:
        return (float("nan"), float("nan"))
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for the 2x2 table ((a, b), (c, d)), no scipy.

    The failure counts here are single digits out of a few hundred, where the normal
    approximation is not usable and the exact sum is cheap: every table with the same
    margins, keeping those no more likely than the observed one.  The paper cites this
    number, so it is computed in the repo from the rows in the repo.
    """
    n = a + b + c + d
    row1, col1 = a + b, a + c
    lo, hi = max(0, col1 - (n - row1)), min(row1, col1)

    def prob(x: int) -> float:
        return (math.comb(row1, x) * math.comb(n - row1, col1 - x)) / math.comb(n, col1)

    observed = prob(a)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed * (1 + 1e-9)))


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
    classes = {name: Counter(failure_class(r) for r in rows if outcome(r) == "planning_error")
               for name, rows in arms.items()}
    if any(classes.values()):
        seen = [k for k in (*FAILURE_CLASSES, "other") if any(c[k] for c in classes.values())]
        lines += ["", "| arm | " + " | ".join(seen) + " |", "|---|" + "--:|" * len(seen)]
        for name, c in classes.items():
            lines.append(f"| {name} | " + " | ".join(str(c[k]) for k in seen) + " |")
        names = list(arms)
        judged_n = {n: len(arms[n]) - Counter(outcome(r) for r in arms[n])["provider"] for n in names}
        for k in seen:
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    x, y = classes[a][k], classes[b][k]
                    if not (x or y):
                        continue
                    p = fisher_exact(x, judged_n[a] - x, y, judged_n[b] - y)
                    lines.append(f"  {k}: {a} {x}/{judged_n[a]} vs {b} {y}/{judged_n[b]}  "
                                 f"Fisher p = {p:.4f}")
    failures = Counter()
    for name, rows in arms.items():
        for r in rows:
            if outcome(r) == "planning_error":
                failures[f"{name}: {r['error'].split('Value error, ')[-1][:90]}"] += 1
    if failures:
        lines += ["", "validation failures:"] + [f"  {n}x {msg}" for msg, n in failures.most_common()]
    if len(arms) > 1:
        # every pair, because a three-arm run (off / old trigger / new trigger) asks two
        # questions at once: did the mechanism still work, and did narrowing it cost anything
        counts = {name: Counter(outcome(r) for r in rows) for name, rows in arms.items()}
        judged = {name: len(arms[name]) - c["provider"] for name, c in counts.items()}
        names = list(arms)
        lines.append("")
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ea, eb = counts[a]["planning_error"], counts[b]["planning_error"]
                p = fisher_exact(ea, judged[a] - ea, eb, judged[b] - eb)
                lines.append(f"Fisher exact two-sided p = {p:.4f}  ({a} {ea}/{judged[a]} vs "
                             f"{b} {eb}/{judged[b]})")
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
    print(report({p.stem: read_jsonl(p) for p in ns.rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
