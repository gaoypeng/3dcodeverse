"""Paired, per-prompt DETERMINISTIC readouts of an ab_plan run: what the gates measured.

The judged score carries the planner's variance (docs/EVAL.md 8.1); these do not carry the
judge's.  They are what c3d-part-contact and c3d-bbox-contract are FOR, so they are the
primary readout and the score is the second.  Both are read off the round
``codeverse3d.addons.select`` picks (the last round when nothing was judged).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench._records import prompt_of, records  # noqa: E402
from codeverse3d.addons import select  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit("usage: python bench/ab_gate_rates.py bench/out/<ab-run>")
OUT = Path(sys.argv[1])
DEPTH = re.compile(r"interpenetrate by ≈([\d.]+) mm")

def cell_stats(record: Path, rec: dict) -> dict | None:
    rounds = rec.get("rounds") or []
    if not rounds:
        return None
    summary = select.summarise(record.parent)
    rnd = next((r for r in rounds if r.get("index") == summary.round), rounds[-1])
    pairs = floats = islands = contract_n = 0
    worst = 0.0
    for g in rnd.get("gates") or []:
        for f in g.get("findings") or []:
            sev, msg = str(f.get("severity", "")), str(f.get("message", ""))
            if sev == "info":
                continue
            if g.get("gate") == "connectivity":
                m = DEPTH.search(msg)
                if m:
                    pairs += 1
                    worst = max(worst, float(m.group(1)))
                elif "is floating" in msg:
                    floats += 1
                elif "tiny disconnected island" in msg:
                    islands += 1
            elif g.get("gate") == "contract":
                contract_n += 1
    return {"pairs": pairs, "worst_depth_mm": worst, "floating": floats,
            "islands": islands, "contract_findings": contract_n,
            "score": summary.picked_score, "status": summary.stop_reason}

rows: dict[str, dict[str, dict]] = {}
for arm in ("control", "variant"):
    cells = OUT / "arms" / arm / "cells"
    for rec, data in records(cells):  # symlinked cells once, _cand/_assets sub-runs never
        s = cell_stats(rec, data)
        if s:
            rows.setdefault(prompt_of(rec, cells), {})[arm] = s

keys = ("pairs", "worst_depth_mm", "floating", "islands", "contract_findings")
paired = {k: [] for k in keys}
print(f"{'prompt':<28} {'pairs c/v':>10} {'worst mm c/v':>14} {'float c/v':>10} {'islands c/v':>12} {'contract c/v':>13}")
for prompt, arms in sorted(rows.items()):
    if len(arms) != 2:
        print(f"{prompt:<28} unpaired ({sorted(arms)})")
        continue
    c, v = arms["control"], arms["variant"]
    for k in keys:
        paired[k].append(v[k] - c[k])
    print(f"{prompt:<28} {c['pairs']:>4}/{v['pairs']:<5} {c['worst_depth_mm']:>6.1f}/{v['worst_depth_mm']:<7.1f}"
          f" {c['floating']:>4}/{v['floating']:<5} {c['islands']:>5}/{v['islands']:<6} {c['contract_findings']:>6}/{v['contract_findings']:<6}")
print()
n = len(paired["pairs"])
print(f"paired prompts with a gate report on both sides: {n}")
for k in keys:
    d = paired[k]
    if not d:
        continue
    mean = sum(d) / len(d)
    down = sum(1 for x in d if x < 0)
    up = sum(1 for x in d if x > 0)
    print(f"  {k:<20} mean delta (variant - control) {mean:+.2f}   better on {down}, worse on {up}, tied {len(d)-down-up}")
