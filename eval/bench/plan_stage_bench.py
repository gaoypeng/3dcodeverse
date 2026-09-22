"""Run the PLAN STAGE alone, many times, and count what it loses.

The judge channel cannot resolve a single-switch change on a 14-prompt battery: the A/A
puts the noise floor at 2 SE = 0.130 and asks for ~506 pairs to resolve ±0.02
(``docs/EVAL.md``).  The plan stage costs ~$0.03 and ~85 s, so the same question asked as
a LOSS RATE — how often does planning end in a ``PlanningError`` instead of a plan — is
affordable at n in the hundreds.  That is how ``C3D_PLAN_RESTART`` was measured
(4.7 % → 0.7 %, Fisher exact p = 0.0067 over 560 calls; ``docs/DECISIONS.md`` D52).

    python bench/plan_stage_bench.py --tree . --label restart_on --reps 20 \
        --out bench/data/plan_stage/restart_on.jsonl --env C3D_PLAN_RESTART=1

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
import subprocess
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
    from bench._jsonl import read_jsonl  # after main's path insert, like every bench import here

    return {(row["prompt"], row["rep"]) for row in read_jsonl(out)}


def _plan_shape(ws_root: Path) -> dict:
    """What the plan the call produced actually contains.

    The workspace is deleted after the call, so anything the row does not carry is gone.
    ``n_mimic`` is why the coupled battery exists: a mechanism with one input is only
    planned as one if the plan SAYS the followers follow (``joints[].mimic``)."""
    try:
        plan = json.loads((ws_root / "plan.json").read_text())
    except (OSError, ValueError):
        return {}
    joints = plan.get("joints") or []
    movable = [j for j in joints if isinstance(j, dict) and j.get("type") != "fixed"]
    return {"n_parts": len(plan.get("parts") or []), "n_joints": len(movable),
            "n_mimic": sum(1 for j in movable if j.get("mimic"))}


def _stats(ws_root: Path) -> dict:
    """What the plan stage's event log says about the call that just ran."""
    from bench._jsonl import read_jsonl  # after main's path insert, like every bench import here

    out: dict = {"invalid_reasks": 0, "geometry_reasks": 0, "restarts": 0, "missing": [], "cost_usd": 0.0}
    for e in read_jsonl(ws_root / "events.jsonl"):
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


def tree_provenance(tree: Path, codeverse_file: str) -> dict[str, str]:
    """What was actually running, on every row: the imported package and the commit.

    Two arms that differ by a worktree are only comparable if the rows say which tree
    they came from — ``--label`` is a name the caller chose, and ``sys.path`` order is
    not visible after the fact (a stale editable install would silently make both arms
    the same code).  ``codeverse_file`` is passed in, never imported here: this module is
    run as a file, so every ``import codeverse3d`` must sit below the sys.path bootstrap
    (tests/compare_bench/test_worktree_import.py)."""
    out = {"codeverse_file": codeverse_file}
    try:
        proc = subprocess.run(["git", "-C", str(tree), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=False, timeout=30)
        if proc.returncode == 0:
            out["tree_commit"] = proc.stdout.strip()
        # an arm made of "the same commit plus two uncommitted edits" would otherwise carry
        # the other arm's commit and read as identical code
        dirty = subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=False, timeout=30)
        if dirty.returncode == 0 and dirty.stdout.strip():
            out["tree_dirty"] = f"{len(dirty.stdout.splitlines())} modified file(s)"
    except (OSError, subprocess.SubprocessError):  # a tree that is not a checkout is fine
        pass
    return out


def run_one(battery, item, backends, label: str, rep: int, provenance: dict[str, str] | None = None,
            keep_failed: Path | None = None, keep_plans: Path | None = None) -> dict:
    """One plan-stage call in a throwaway workspace; never raises.

    ``plan_once`` deliberately bypasses ``Track.run``: there is no build, no judge and no
    run ledger, so the JSONL row IS the record (it carries the call's own ``cost_usd``
    from the plan events, and the totals are printed at the end).  ``keep_failed`` moves a
    workspace that produced no plan out of the temp dir instead of deleting it, which is
    the only way to read what the model actually wrote.  ``keep_plans`` copies the plan
    JSON of a call that SUCCEEDED beside its row: the rows carry `n_parts` / `n_joints` /
    `n_mimic` only, so any joint-level reading of a committed arm ("eight of ten leave
    exactly one free joint and it IS the input") is otherwise a hand reading nobody can
    redo — review round 3 asked for this.
    """
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
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
           "seconds": round(time.time() - started, 1), **(provenance or {}),
           **_plan_shape(root / "ws"), **_stats(root / "ws")}
    if ok and keep_plans is not None:
        src = root / "ws" / "plan.json"
        if src.is_file():
            keep_plans.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, keep_plans / f"{item.id}_r{rep}.json")
    if not ok and keep_failed is not None:
        # name what it was, by the same rule the report classifies the row: a provider block
        # says nothing about the code under test, everything else (a validation failure, a
        # budget ceiling) is a loss worth reading
        from bench.plan_stage_report import outcome

        kind = "provider" if outcome(row) == "provider" else "planning"
        keep_failed.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(keep_failed / f"{kind}_{item.id}_r{rep}"))
    else:
        shutil.rmtree(root, ignore_errors=True)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tree", type=Path, default=Path(__file__).resolve().parents[2] / "harness",
                    help="harness tree to import from")
    ap.add_argument("--label", required=True, help="arm name, recorded on every row")
    ap.add_argument("--reps", type=int, default=20, help="calls per prompt")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--battery", default=DEFAULT_BATTERY)
    ap.add_argument("--ids", default="", help="comma list; default every prompt in the battery")
    ap.add_argument("--planner", default=DEFAULT_PLANNER)
    ap.add_argument("--env", default="", help="comma list of KEY=VALUE applied to this arm")
    ap.add_argument("--keep-failed", type=Path, default=None,
                    help="move the workspace of any call that produced no plan here (plan stage "
                         "only, so they are small) instead of deleting it")
    ap.add_argument("--keep-plans", type=Path, default=None,
                    help="copy each SUCCESSFUL call's plan.json here, so a joint-level reading of "
                         "the arm can be redone from the tree (the rows carry shape counts only)")
    ns = ap.parse_args(argv)

    for kv in (x for x in ns.env.split(",") if x.strip()):
        key, _, value = kv.partition("=")
        os.environ[key.strip()] = value.strip()
        print(f"env {key.strip()}={value.strip()}")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # the `bench` package (eval/)
    sys.path.insert(0, str(ns.tree.resolve()))   # the harness tree under test wins for `codeverse3d`

    import codeverse3d
    from bench._jsonl import read_jsonl, seal_for_append  # after the path insert (script run)
    from bench.run_bench import Battery
    from codeverse3d.config import get_settings
    if not Path(codeverse3d.__file__).resolve().is_relative_to(ns.tree.resolve()):
        raise SystemExit(f"--tree {ns.tree} but `codeverse3d` imported from {codeverse3d.__file__}: "
                         "an editable install won the path race, so both arms would run the "
                         "same code (ab_plan.py's header documents this failure)")

    battery = Battery.load(ns.tree / ns.battery)
    backends = get_settings().backends(generator=DEFAULT_GENERATOR, planner=ns.planner, judge=DEFAULT_JUDGE)
    wanted = [p for p in battery.prompts if not ns.ids or p.id in {i for i in ns.ids.split(",") if i}]
    done = _done(ns.out)
    jobs = [(item, rep) for item in wanted for rep in range(ns.reps) if (item.id, rep) not in done]
    print(f"{ns.label}: {len(jobs)} plan call(s) — {len(wanted)} prompt(s) x {ns.reps} rep(s), {len(done)} already recorded")

    ns.out.parent.mkdir(parents=True, exist_ok=True)
    seal_for_append(ns.out)  # a killed run leaves a partial last line; do not glue onto it
    prov = tree_provenance(ns.tree, codeverse3d.__file__)
    print(f"{ns.label}: {prov}")
    with ns.out.open("a") as fh, cf.ThreadPoolExecutor(ns.workers) as pool:
        # imap-style ordering: pool.map yields in submission order, so one slow call holds
        # back every finished row behind it.  Rows are the journal, so they go out as they
        # land instead (resume reads what is on disk, order does not matter).
        futures = [pool.submit(run_one, battery, item, backends, ns.label, rep, prov, ns.keep_failed, ns.keep_plans)
                   for item, rep in jobs]
        for fut in cf.as_completed(futures):
            row = fut.result()
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"  {row['prompt']:28} r{row['rep']:<3} ok={row['ok']} restart={row['restarts']} {row['error'][:60]}")
    rows = read_jsonl(ns.out)
    spent = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    print(f"{ns.label}: {sum(1 for r in rows if r['ok'])}/{len(rows)} plans valid, "
          f"${spent:.2f} over {len(rows)} calls (the rows are the ledger: plan_once does not "
          f"open one)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
