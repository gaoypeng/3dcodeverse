"""What the two penetration probes say about the same artefacts, and what a threshold costs.

The harness measures interpenetration twice.  ``spatial/connectivity`` samples 600 points
per surface, requires a minimum share of them to lie inside the other part, and calls
2 mm a WARN and 10 mm an ERROR.  ``spatial/joints_collide`` (the ``joint_sweep`` gate)
probes the posed meshes densely and reports at a 1 mm tolerance.  Same physical quantity,
different sensitivity, and the LAXER one is the gate that fails a round.

    python bench/penetration_thresholds.py bench/out            # every recorded run below it

Prints, over the articulated rounds it finds: how often connectivity's ERROR threshold
fires at all, the depth distribution it records, what each candidate threshold would cost
in newly failing rounds, and — where both gates ran — the two probes' numbers on the same
link pair.  A threshold change is a behaviour change; this is the blast radius for one.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codeverse.spatial.connectivity import (  # noqa: E402
    PENETRATION_ERROR_M,
    PENETRATION_WARN_M,
)

#: candidate ERROR thresholds in metres, including the one that ships
CANDIDATES = (0.001, 0.002, 0.005, PENETRATION_ERROR_M)
#: `joint_sweep` reports a rest-pose overlap at this depth (joints_collide's tolerance)
SWEEP_TOLERANCE_M = 0.001
_PAIR = re.compile(r"links '([^|]+)\|([^']+)'")
_WORST = re.compile(r"worst ([\d.]+) mm")


def records(root: Path) -> list[dict]:
    """Every articulated run record under ``root``, once per run."""
    out, seen = [], set()
    for rec in root.rglob("record.json"):
        if rec.resolve() in seen or "_cand" in rec.parts:
            continue
        seen.add(rec.resolve())
        try:
            data = json.loads(rec.read_text())
        except (OSError, ValueError):
            continue
        if (data.get("spec") or {}).get("track") == "articulated_object":
            out.append(data)
    return out


def survey(recs: list[dict]) -> dict:
    """Per-round worst connectivity depth, and how the two probes compare where both ran."""
    worst_per_round: list[float] = []
    fired: Counter[str] = Counter()
    both: list[tuple[str, str, float, float | None]] = []
    for data in recs:
        for rnd in data.get("rounds") or []:
            gates = {g["gate"]: g for g in (rnd.get("gates") or [])}
            conn = gates.get("connectivity")
            if conn is None:
                continue
            depths = {}
            worst = 0.0
            for f in conn.get("findings") or []:
                d = f.get("data") or {}
                if d.get("kind") != "penetration":
                    continue
                depth = float(d.get("depth_m") or 0.0)
                worst = max(worst, depth)
                depths[tuple(sorted([f.get("target", ""), str(d.get("other") or "")]))] = depth
                fired[f.get("severity", "")] += 1
            worst_per_round.append(worst)
            sweep = gates.get("joint_sweep")
            for f in (sweep or {}).get("findings") or []:
                if f.get("severity") != "error":
                    continue
                pair, mm = _PAIR.search(f.get("message", "")), _WORST.search(f.get("message", ""))
                if not (pair and mm):
                    continue
                key = tuple(sorted([pair.group(1), pair.group(2)]))
                both.append((data.get("spec", {}).get("id", "?"), "|".join(key),
                             float(mm.group(1)) / 1000, depths.get(key)))
    return {"rounds": len(worst_per_round), "worst": worst_per_round, "fired": fired, "both": both}


def corroborate(recs: list[dict], threshold_m: float) -> dict:
    """Would an ERROR at ``threshold_m`` fire on real defects?

    The dense probe is the reference: for every link pair connectivity records at or over
    the threshold, ask whether the SAME round's ``joint_sweep`` also reported that pair.
    ``both`` = a new failure the dense probe corroborates, ``conn_only`` = one it does not,
    ``sweep_only`` = an overlap the sparse probe never saw (today's silent misses).
    """
    both = conn_only = sweep_only = 0
    examples: list[str] = []
    for data in recs:
        for rnd in data.get("rounds") or []:
            gates = {g["gate"]: g for g in (rnd.get("gates") or [])}
            conn, sweep = gates.get("connectivity"), gates.get("joint_sweep")
            if conn is None or sweep is None:
                continue
            deep = set()
            for f in conn.get("findings") or []:
                d = f.get("data") or {}
                if d.get("kind") == "penetration" and float(d.get("depth_m") or 0.0) >= threshold_m:
                    deep.add(tuple(sorted([f.get("target", ""), str(d.get("other") or "")])))
            flagged = set()
            for f in sweep.get("findings") or []:
                if f.get("severity") != "error":
                    continue
                m = _PAIR.search(f.get("message", ""))
                if m:
                    flagged.add(tuple(sorted([m.group(1), m.group(2)])))
            both += len(deep & flagged)
            conn_only += len(deep - flagged)
            sweep_only += len(flagged - deep)
            for pair in sorted(deep - flagged)[:1]:
                examples.append(f"{data.get('spec', {}).get('id', '?')}: {'|'.join(pair)}")
    return {"both": both, "conn_only": conn_only, "sweep_only": sweep_only, "examples": examples}


def report(root: Path) -> str:
    s = survey(records(root))
    rounds, worst = s["rounds"], s["worst"]
    nonzero = [w for w in worst if w > 0]
    lines = [f"# penetration thresholds over {rounds} articulated round(s) under {root}", "",
             f"connectivity findings: {s['fired']['error']} ERROR, {s['fired']['warn']} WARN "
             f"(ships at WARN {PENETRATION_WARN_M * 1000:.0f} mm / ERROR {PENETRATION_ERROR_M * 1000:.0f} mm)",
             f"rounds with any recorded penetration: {len(nonzero)}"]
    if nonzero:
        lines.append(f"depth recorded by connectivity: median {statistics.median(nonzero) * 1000:.1f} mm, "
                     f"max {max(nonzero) * 1000:.1f} mm")
    lines += ["", "| ERROR threshold | rounds at or over it | share |", "|---|--:|--:|"]
    for th in sorted(CANDIDATES):
        n = sum(1 for w in worst if w >= th)
        lines.append(f"| {th * 1000:.0f} mm | {n} | {n / max(1, rounds):.0%} |")
    recs = records(root)
    for th in (0.002, 0.005):
        c = corroborate(recs, th)
        total = c["both"] + c["conn_only"]
        lines += ["", f"an ERROR at {th * 1000:.0f} mm would fire on {total} link pair(s): "
                      f"**{c['both']} corroborated** by the dense probe in the same round, "
                      f"{c['conn_only']} not; the dense probe additionally flags {c['sweep_only']} "
                      f"pair(s) this threshold still misses."]
        if c["examples"]:
            lines.append(f"  uncorroborated examples: {', '.join(c['examples'][:4])}")
    if s["both"]:
        lines += ["", f"the two probes on the same link pair ({len(s['both'])} sweep ERROR findings; "
                      f"sweep tolerance {SWEEP_TOLERANCE_M * 1000:.0f} mm):",
                  "", "| run | pair | sweep | connectivity |", "|---|---|--:|--:|"]
        for run, pair, sw, cn in s["both"][:20]:
            lines.append(f"| {run} | `{pair}` | {sw * 1000:.1f} mm | "
                         f"{'—' if cn is None else f'{cn * 1000:.1f} mm'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", type=Path, help="a directory holding recorded runs (bench/out, a battery, a run)")
    print(report(ap.parse_args(argv).root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
