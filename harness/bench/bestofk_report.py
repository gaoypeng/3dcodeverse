"""Is the harness better than sampling k times for the same money?

The reviewer question a harness paper has to answer: a loop that plans, builds, gates and
refines costs ~28x one raw generation on `compare_v4` (median generation cost $0.9221
against $0.0327).  Spend that money on 28 independent one-shot samples and keep the best —
does the harness still win?

    python bench/bestofk_report.py bench/out/bestofk --against bench/out/compare_v4

`bestofk/rep*/results.jsonl` are K independent one-shot batteries over the same prompts
and the same fixed judge; the best-of-k curve is computed here rather than by a new arm
kind, so no code path the comparison depends on is new.  The harness column is READ from
the recorded battery — its generator (`api-agent`) was deleted from the tree on 2026-08-28,
so those rows are the arm the paper reports.

**The winner's curse is reported, not hidden.**  max() over k noisy scores overestimates
the true best by roughly `sigma * sqrt(2 ln k)`; with the pro judge's sigma = 0.030
(PAPER_WRITING §2) that is +0.078 at k = 28.  The bias runs AGAINST the harness, so a
harness win that survives it is the conservative reading; a harness loss inside that band
is not a loss.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: judge repeatability on the calibration set (docs/PAPER_WRITING.md §2), used only to
#: state how much of a best-of-k maximum is selection noise
JUDGE_SIGMA = 0.030


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def one_shot_samples(root: Path) -> dict[str, list[float]]:
    """``{prompt: [score per rep]}`` — one entry per rep that scored that prompt."""
    out: dict[str, list[float]] = defaultdict(list)
    for rep in sorted(root.glob("rep*/results.jsonl")):
        seen: dict[str, float] = {}
        for r in _rows(rep):
            if r.get("score") is None:
                continue
            seen[r["prompt_id"]] = float(r["score"])   # last row wins, as paired_compare does
        for pid, sc in seen.items():
            out[pid].append(sc)
    return dict(out)


def harness_scores(root: Path, *, arm_prefix: str = "harness:") -> dict[str, float]:
    """``{prompt: score}`` for the recorded harness arm, last row per prompt."""
    out: dict[str, float] = {}
    for r in _rows(root / "results.jsonl"):
        if not str(r.get("arm", "")).startswith(arm_prefix) or r.get("score") is None:
            continue
        out[r["prompt_id"]] = float(r["score"])
    return out


def best_of(samples: list[float], k: int) -> float | None:
    """Best of the FIRST k samples — the reps are independent and unordered, so this is
    one draw of best-of-k rather than the best over everything available."""
    return max(samples[:k]) if len(samples) >= k else None


def curve(one: dict[str, list[float]], harness: dict[str, float], ks: list[int]) -> list[dict]:
    out = []
    for k in ks:
        pairs = [(p, best_of(s, k), harness[p]) for p, s in one.items() if p in harness]
        pairs = [(p, b, h) for p, b, h in pairs if b is not None]
        if not pairs:
            continue
        deltas = [h - b for _, b, h in pairs]
        wins = sum(1 for d in deltas if d > 0)
        losses = sum(1 for d in deltas if d < 0)
        mean = statistics.mean(deltas)
        se = statistics.stdev(deltas) / math.sqrt(len(deltas)) if len(deltas) > 1 else 0.0
        out.append({"k": k, "n": len(pairs), "best_of_k": statistics.mean(b for _, b, _ in pairs),
                    "harness": statistics.mean(h for _, _, h in pairs), "delta": mean,
                    "ci": 1.96 * se, "wins": wins, "losses": losses,
                    "curse": JUDGE_SIGMA * math.sqrt(2 * math.log(k)) if k > 1 else 0.0})
    return out


def report(root: Path, against: Path) -> str:
    one = one_shot_samples(root)
    harness = harness_scores(against)
    if not one or not harness:
        return f"nothing to compare: {len(one)} prompts sampled, {len(harness)} harness rows"
    reps = max(len(v) for v in one.values())
    ks = [k for k in (1, 2, 4, 8, 16, 28) if k <= reps]
    lines = [f"# harness vs best-of-k one-shot — {len(one)} prompts, up to {reps} reps", "",
             f"Winner's-curse bound: judge sigma {JUDGE_SIGMA:.3f}, so a best-of-k maximum carries",
             "about `sigma*sqrt(2 ln k)` of selection noise. It inflates the BASELINE, never the harness.",
             "", "| k | n | best-of-k | harness | harness − best-of-k | 95 % CI | W/L | curse bound |",
             "|--:|--:|--:|--:|--:|---|---|--:|"]
    for row in curve(one, harness, ks):
        lines.append(f"| {row['k']} | {row['n']} | {row['best_of_k']:.3f} | {row['harness']:.3f} | "
                     f"{row['delta']:+.3f} | [{row['delta'] - row['ci']:+.3f}, {row['delta'] + row['ci']:+.3f}] | "
                     f"{row['wins']}/{row['losses']} | {row['curse']:+.3f} |")
    counts = sorted({len(v) for v in one.values()})
    lines += ["", f"reps per prompt: {counts[0]}–{counts[-1]}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", type=Path, help="directory holding rep*/results.jsonl")
    ap.add_argument("--against", type=Path, required=True, help="recorded battery with the harness arm")
    ap.add_argument("--out", type=Path)
    ns = ap.parse_args(argv)
    text = report(ns.root, ns.against)
    print(text)
    if ns.out:
        ns.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
