"""Run the PLAN STAGE alone, many times, and count what it loses.

The judge channel cannot resolve a single-switch change on a 14-prompt battery: the A/A
puts the noise floor at 2 SE = 0.130 and asks for ~506 pairs to resolve ±0.02
(``docs/EVAL.md``).  The plan stage costs ~$0.03 and ~85 s, so the same question asked as
a LOSS RATE — how often does planning end in a ``PlanningError`` instead of a plan — is
affordable at n in the hundreds.  That is how ``CV3D_PLAN_RESTART`` was measured
(4.7 % → 0.7 %, Fisher exact p = 0.0067 over 560 calls; ``docs/DECISIONS.md`` D52).

    python bench/plan_stage_bench.py --tree . --label restart_on --reps 20 \
        --out bench/data/plan_stage/restart_on.jsonl --env CV3D_PLAN_RESTART=1

``--tree`` is the harness tree to import from, so the two arms can differ by a switch
(``--env``) or by a worktree.  Run both arms in the SAME window: provider weather moves
the failure rate more than most changes do.  One row per call; resumable (a row already
recorded for (prompt, rep) is not re-run).  ``bench/plan_stage_report.py`` turns the rows
into the rate table.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_BATTERY = "bench/prompts/articulated_v2.yaml"
DEFAULT_PLANNER = "gemini:gemini-3.7-flash"
DEFAULT_GENERATOR = "gemini-cli:gemini-3.7-flash"
DEFAULT_JUDGE = "gemini:gemini-3.1-pro-preview"


def _done(out: Path) -> set[tuple[str, int]]:
    """(prompt, rep) pairs already recorded, so a killed run resumes instead of repeating."""
    if not out.is_file():
        return set()
    seen: set[tuple[str, int]] = set()
    for line in out.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        seen.add((row["prompt"], row["rep"]))
    return seen


def _stats(ws_root: Path) -> dict:
    """What the plan stage's event log says about the call that just ran."""
    out: dict = {"invalid_reasks": 0, "geometry_reasks": 0, "restarts": 0, "missing": [], "cost_usd": 0.0}
    events = ws_root / "events.jsonl"
    if not events.is_file():
        return out
    for line in events.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = e.get("event")
        if kind == "plan.invalid":
            out["invalid_reasks"] += 1
        elif kind == "plan.geometry":
            out["geometry_reasks"] += 1
        elif kind == "plan.restart":
            out["restarts"] += 1
            out["missing"] = e.get("missing") or []
        elif kind == "plan.done":
            out["n_parts"] = e.get("n_parts")
            out["attempt"] = e.get("attempt")
            out["cost_usd"] = float(e.get("cost_usd") or 0.0)
    return out


def run_one(battery, item, backends, label: str, rep: int) -> dict:
    """One plan-stage call in a throwaway workspace; never raises."""
    from bench.pin_plan import plan_once
    from bench.run_bench import build_spec

    spec = build_spec(battery, item, backends=backends, rounds=1, max_minutes=15, tag0="plan_stage")
    root = Path(tempfile.mkdtemp(prefix=f"plan_stage_{label}_"))
    started = time.time()
    ok, error = True, ""
    try:
        plan_once(spec, root / "ws")
    except Exception as e:  # noqa: BLE001 — the failure IS the measurement
        ok, error = False, f"{type(e).__name__}: {e}"[:300].replace("\n", " ")
    row = {"tree": label, "prompt": item.id, "rep": rep, "ok": ok, "error": error,
           "seconds": round(time.time() - started, 1), **_stats(root / "ws")}
    shutil.rmtree(root, ignore_errors=True)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tree", type=Path, default=Path.cwd(), help="harness tree to import from")
    ap.add_argument("--label", required=True, help="arm name, recorded on every row")
    ap.add_argument("--reps", type=int, default=20, help="calls per prompt")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--battery", default=DEFAULT_BATTERY)
    ap.add_argument("--ids", default="", help="comma list; default every prompt in the battery")
    ap.add_argument("--planner", default=DEFAULT_PLANNER)
    ap.add_argument("--env", default="", help="comma list of KEY=VALUE applied to this arm")
    ns = ap.parse_args(argv)

    for kv in (x for x in ns.env.split(",") if x.strip()):
        key, _, value = kv.partition("=")
        os.environ[key.strip()] = value.strip()
        print(f"env {key.strip()}={value.strip()}")
    sys.path.insert(0, str(ns.tree.resolve()))

    from bench.run_bench import Battery
    from codeverse.config import get_settings

    battery = Battery.load(ns.tree / ns.battery)
    backends = get_settings().backends(generator=DEFAULT_GENERATOR, planner=ns.planner, judge=DEFAULT_JUDGE)
    wanted = [p for p in battery.prompts if not ns.ids or p.id in {i for i in ns.ids.split(",") if i}]
    done = _done(ns.out)
    jobs = [(item, rep) for item in wanted for rep in range(ns.reps) if (item.id, rep) not in done]
    print(f"{ns.label}: {len(jobs)} plan call(s) — {len(wanted)} prompt(s) x {ns.reps} rep(s), {len(done)} already recorded")

    ns.out.parent.mkdir(parents=True, exist_ok=True)
    with ns.out.open("a") as fh, cf.ThreadPoolExecutor(ns.workers) as pool:
        for row in pool.map(lambda job: run_one(battery, job[0], backends, ns.label, job[1]), jobs):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"  {row['prompt']:28} r{row['rep']:<3} ok={row['ok']} restart={row['restarts']} {row['error'][:60]}")
    rows = [json.loads(x) for x in ns.out.read_text().splitlines() if x.strip()]
    print(f"{ns.label}: {sum(1 for r in rows if r['ok'])}/{len(rows)} plans valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
