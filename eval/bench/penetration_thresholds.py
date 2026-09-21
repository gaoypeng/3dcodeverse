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
import inspect
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse (harness/) + the `bench` package (eval/)

from codeverse.languages.urdf import REST_PENETRATION_MAX_M  # noqa: E402
from codeverse.record.record import unique_files  # noqa: E402
from codeverse.spatial.connectivity import (  # noqa: E402
    PENETRATION_ERROR_M,
    PENETRATION_WARN_M,
)
from codeverse.spatial.joints_sweep import sweep_collisions, sweep_findings  # noqa: E402

#: candidate ERROR thresholds in metres, including the one that ships
CANDIDATES = (0.001, 0.002, 0.005, PENETRATION_ERROR_M)
#: The sweep's own two numbers, IMPORTED rather than restated — this file used to carry a
#: third copy of a constant the two probes already disagree on, and it had it wrong.
#: `sweep_collisions(tol_m=)` is the depth at which a moved-pose overlap is recorded at
#: all; `sweep_findings(rest_max_m=)` is the depth above which a REST overlap is an ERROR
#: rather than a WARN.  Every caller takes both defaults.
SWEEP_TOLERANCE_M = float(inspect.signature(sweep_collisions).parameters["tol_m"].default)
SWEEP_REST_ERROR_M = float(inspect.signature(sweep_findings).parameters["rest_max_m"].default)
assert SWEEP_REST_ERROR_M == REST_PENETRATION_MAX_M, "the urdf runtime disagrees with the sweep default"




def records(root: Path) -> list[dict]:
    """Every articulated run record under ``root``, once per run.

    The walk is ``flywheel.record.unique_files``: it follows symlinked cells and the
    ``run/telemetry/trajectories`` link and counts each file once, and skips the
    sub-workspaces a run owns — the trap that inflated COST §30, handled in one place.
    """
    out = []
    for rec in unique_files(root, "record.json"):
        try:
            data = json.loads(rec.read_text())
        except (OSError, ValueError):
            continue
        if (data.get("spec") or {}).get("track") == "articulated_object":
            out.append(data)
    return out


def sweep_pairs(sweep: dict | None, *, rest_only: bool) -> dict[tuple[str, ...], float]:
    """``{link pair: worst depth}`` from a ``joint_sweep`` gate's ERROR findings.

    Read off the structured finding, never the message: ``target`` IS the pair key and
    ``data["max_depth_m"]`` the worst depth (``depth_m`` when the pair was not aggregated).
    A wording change in ``aggregate_findings`` used to zero this whole report.

    ``rest_only`` keeps the findings whose WORST pose is the rest pose — ``data["pose"]``
    falsy, which is what ``aggregate_findings`` writes for rest.  Without it this compares
    the sweep's worst-over-all-poses depth against connectivity's rest-pose depth, and
    "2-3x deeper on the same pairs" is then not a statement about one pose.
    """
    out: dict[tuple[str, ...], float] = {}
    for f in (sweep or {}).get("findings") or []:
        if f.get("severity") != "error":
            continue
        d = f.get("data") or {}
        if d.get("kind") != "penetration":
            continue
        if rest_only and d.get("pose"):
            continue
        target = str(f.get("target") or "")
        if "|" not in target:
            continue
        key = tuple(sorted(target.split("|", 1)))
        depth = float(d.get("max_depth_m") or d.get("depth_m") or 0.0)
        out[key] = max(out.get(key, 0.0), depth)
    return out


def survey(recs: list[dict]) -> dict:
    """Per-round worst connectivity depth, and how the two probes compare where both ran."""
    worst_per_round: list[float] = []
    fired: Counter[str] = Counter()
    both: list[tuple[str, str, float, float | None]] = []
    all_sweep_pairs: list[tuple[str, float]] = []
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
            # every ERROR pair, for the corpus counts the docs cite
            for key, depth in sweep_pairs(sweep, rest_only=False).items():
                all_sweep_pairs.append(("|".join(key), depth))
            # and only the pairs whose worst pose IS rest, for the depth comparison
            for key, depth in sweep_pairs(sweep, rest_only=True).items():
                both.append((data.get("spec", {}).get("id", "?"), "|".join(key), depth, depths.get(key)))
    n_rest = sum(1 for _, _, _, c in both if c is not None)
    return {"rounds": len(worst_per_round), "worst": worst_per_round, "fired": fired, "both": both,
            "sweep_findings": len(all_sweep_pairs),
            "sweep_distinct": len({p for p, _ in all_sweep_pairs}),
            "sweep_depths": [d for _, d in all_sweep_pairs],
            "rest_comparable": n_rest}


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
            flagged = set(sweep_pairs(sweep, rest_only=False))
            both += len(deep & flagged)
            conn_only += len(deep - flagged)
            sweep_only += len(flagged - deep)
            for pair in sorted(deep - flagged)[:1]:
                examples.append(f"{data.get('spec', {}).get('id', '?')}: {'|'.join(pair)}")
    return {"both": both, "conn_only": conn_only, "sweep_only": sweep_only, "examples": examples}


def report(root: Path) -> str:
    recs = records(root)          # read once: this used to walk every record.json twice
    s = survey(recs)
    rounds, worst = s["rounds"], s["worst"]
    nonzero = [w for w in worst if w > 0]
    lines = [f"# penetration thresholds over {rounds} articulated round(s) under {root}", "",
             f"connectivity findings: {s['fired']['error']} ERROR, {s['fired']['warn']} WARN "
             f"(ships at WARN {PENETRATION_WARN_M * 1000:.0f} mm / ERROR {PENETRATION_ERROR_M * 1000:.0f} mm)",
             f"rounds with any recorded penetration: {len(nonzero)}"]
    if nonzero:
        lines.append(f"depth recorded by connectivity: median {statistics.median(nonzero) * 1000:.1f} mm, "
                     f"max {max(nonzero) * 1000:.1f} mm")
    sd = s["sweep_depths"]
    lines += ["",
              f"joint_sweep ERROR findings: {s['sweep_findings']} across {s['sweep_distinct']} distinct link "
              f"pair(s) (records an overlap at {SWEEP_TOLERANCE_M * 1000:.0f} mm; a REST overlap is an ERROR "
              f"only above {SWEEP_REST_ERROR_M * 1000:.0f} mm)"]
    if sd:
        lines.append(f"depth recorded by the sweep: median {statistics.median(sd) * 1000:.1f} mm, "
                     f"max {max(sd) * 1000:.1f} mm")
    lines.append(f"pairs whose WORST sweep pose is rest (the only ones comparable with connectivity "
                 f"pose-for-pose): {len(s['both'])}, of which {s['rest_comparable']} were also recorded "
                 f"by connectivity in the same round")
    lines += ["", "| ERROR threshold | rounds at or over it | share |", "|---|--:|--:|"]
    for th in sorted(CANDIDATES):
        n = sum(1 for w in worst if w >= th)
        lines.append(f"| {th * 1000:.0f} mm | {n} | {n / max(1, rounds):.0%} |")
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
        lines += ["", "the two probes on the same link pair AT REST — the sweep rows below are all "
                      "findings whose worst pose is the rest pose, so both columns describe one pose:",
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
