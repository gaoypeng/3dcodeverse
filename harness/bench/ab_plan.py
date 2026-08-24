"""Controlled A/B of a plan / brief change: control vs variant, paired per prompt.

    python bench/ab_plan.py --prompts bench/prompts/static_objects_v2.yaml \\
        --variant-env CV3D_PLAN_BRIEF=on --out bench/out/ab_brief \\
        [--generator api-agent:gemini:gemini-3.7-flash] [--judge gemini:gemini-3.1-pro-preview] \\
        [--rounds 2] [--max-usd 2.5] [--ids a,b] [--wait-for-provider 60] [--redo-status infra_failed]

The only thing that differs between the arms is the ``--variant-env`` block, applied to
the VARIANT arm alone.  Everything else is held fixed on both sides: the generator, the
planner, the in-loop judge, the rounds and dollars, and the fixed judge that produces
the reported score (``bench/_fixed_eval.py``, ``n_samples`` 2).

Why each arm is a child PROCESS rather than a thread: the switches under test are read
from the environment (``codeverse.tracks.brief.brief_enabled`` reads ``os.environ`` at
call time; anything under ``Settings`` is read once through an ``lru_cache``), so two
threads in one interpreter cannot hold different values of them.  A child gets exactly
the env its arm needs and nothing leaks across.  Each child owns a key pool, so the
driver caps every child at ``CV3D_MAX_IN_FLIGHT`` (default 16) — two children at
16 stay inside the 64 knee measured for one process (``docs/COST.md`` §23).

Why the arms launch as simultaneous PAIRS: provider weather changes by the hour, and a
verdict that compares an arm run in a storm against one run in the clear measures the
storm.  Prompt N's control and variant start together and the next prompt waits for
both (``--parallel`` is fixed at 2 by construction).  A cell lost to an outage is
``infra_failed`` and removes its prompt from the pairing, never scores it 0
(``docs/EVAL.md`` §7); ``--redo-status infra_failed`` re-runs BOTH arms of that prompt so
the pair is re-measured together.

Layout of ``--out``: ``ab.json`` (the arms, options and variant env) ·
``results.jsonl`` (one ``CellResult`` per (prompt, arm); resume source) ·
``arms/<arm>/cells/<prompt>/<slug>/{run,eval,cell.json,worker.log}`` · ``pairs.json`` ·
``summary.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse._compat import UTC

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # `python bench/ab_plan.py` from anywhere
    sys.path.insert(0, str(REPO))

from bench._ab_report import ARMS, CONTROL, VARIANT, Verdict, write_report  # noqa: E402
from bench._compare_report import CellResult, load_jsonl  # noqa: E402
from bench._infra import is_infra_failure  # noqa: E402
from bench.compare_backends import (  # noqa: E402
    Arm,
    CompareDeps,
    CompareOptions,
    _preflight,
    run_cell,
)
from bench.run_bench import Battery, BenchPrompt, select_prompts  # noqa: E402

DEFAULT_GENERATOR = "api-agent:gemini:gemini-3.7-flash"
DEFAULT_JUDGE = "gemini:gemini-3.1-pro-preview"
#: the per-child cap.  The flat name is a first-class alias of ``CV3D_RATE__MAX_IN_FLIGHT``
#: since 2026-08-24 (``Settings._FLAT_ALIASES``; before that it was read by nothing) and
#: wins over the nested spelling when both are set, which is what makes ``setdefault``
#: below a real cap even when the launching shell exported the nested one at 64.
MAX_IN_FLIGHT_ENV = "CV3D_MAX_IN_FLIGHT"
DEFAULT_MAX_IN_FLIGHT = 16
#: only ever 2 — one control + one variant, launched together (see module docstring)
PARALLEL = 2


class AbOptions(BaseModel):
    generator: str = DEFAULT_GENERATOR
    judge: str = DEFAULT_JUDGE
    loop_judge: str | None = Field(default=None, description="in-loop judge for BOTH arms (None → settings default)")
    planner: str | None = Field(default=None, description="planner for BOTH arms (None → settings default)")
    rounds: int = 2
    max_usd: float = 2.5
    max_minutes: float = 45.0
    n_samples: int = 2
    ids: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    limit: int | None = None
    resume: bool = True
    redo_status: list[str] = Field(default_factory=list, description="re-run prompts where EITHER arm has one of these")
    variant_env: dict[str, str] = Field(default_factory=dict, description="applied to the variant arm only")
    max_in_flight: int = DEFAULT_MAX_IN_FLIGHT

    def compare_options(self) -> CompareOptions:
        """The per-cell options handed to ``compare_backends.run_cell`` (both arms identical)."""
        return CompareOptions(judge=self.judge, loop_judge=self.loop_judge, planner=self.planner, rounds=self.rounds,
                              max_usd=self.max_usd, max_minutes=self.max_minutes, n_samples=self.n_samples,
                              resume=self.resume, pairwise=False)


def parse_variant_env(items: Sequence[str]) -> dict[str, str]:
    """``["K=V", "K2=V2"]`` → ``{K: V, K2: V2}``; a bare ``K`` is rejected, not defaulted."""
    out: dict[str, str] = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k.strip():
            raise ValueError(f"--variant-env needs KEY=VALUE, got {it!r}")
        out[k.strip()] = v
    return out


def child_env(arm: str, opts: AbOptions, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of one arm's child process.

    The variant gets ``variant_env`` on top of the driver's env; the control gets the
    driver's env with those same keys REMOVED, so a switch that happens to be set in
    the launching shell cannot silently turn the control into a second variant.  Both
    get the in-flight cap unless the driver already set one.
    """
    env = dict(os.environ if base is None else base)
    for k in opts.variant_env:
        env.pop(k, None)
    if arm == VARIANT:
        env.update(opts.variant_env)
    env.setdefault(MAX_IN_FLIGHT_ENV, str(opts.max_in_flight))
    env["PYTHONUNBUFFERED"] = "1"
    return env


def arm_dir(out: Path, arm: str) -> Path:
    return out / "arms" / arm


def cell_dir(out: Path, arm: str, item_id: str, generator: str) -> Path:
    """Where ``compare_backends.run_cell`` puts this arm's cell (its layout, our root)."""
    return arm_dir(out, arm) / "cells" / item_id / _harness_arm(generator).slug


def _harness_arm(generator: str) -> Arm:
    return Arm(raw=f"harness:{generator}", kind="harness", target=generator)


def worker_argv(battery_path: Path, out: Path, item_id: str, arm: str, opts: AbOptions) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "cell", "--prompts", str(battery_path), "--out", str(out),
            "--id", item_id, "--arm", arm, "--options-json", opts.model_dump_json()]


# ----------------------------------------------------------------------------- one cell, in a child
CellRunner = Callable[[Path, Path, BenchPrompt, str, AbOptions], CellResult]


def spawn_cell(battery_path: Path, out: Path, item: BenchPrompt, arm: str, opts: AbOptions) -> CellResult:
    """Run one (prompt, arm) cell in a child process and read back its ``cell.json``.

    A child that dies without writing a cell (killed, import error, unhandled outage)
    is classified by the tail of its log with the same rule every other failure path
    uses, so an outage stays ``infra_failed`` and never becomes a scored zero.
    """
    cell = cell_dir(out, arm, item.id, opts.generator)
    cell.mkdir(parents=True, exist_ok=True)
    log, marker = cell / "worker.log", cell / "cell.json"
    marker.unlink(missing_ok=True)  # a stale verdict from an earlier attempt must not stand in for this one
    t0 = time.time()
    with log.open("a") as fh:
        fh.write(f"--- {datetime.now(UTC).isoformat()} {arm} {item.id}\n")
        fh.flush()
        rc = subprocess.run(worker_argv(battery_path, out, item.id, arm, opts), cwd=REPO, env=child_env(arm, opts),
                            stdout=fh, stderr=subprocess.STDOUT, check=False).returncode
    if marker.is_file():
        return CellResult.model_validate_json(marker.read_text())
    tail = log.read_text()[-1500:] if log.is_file() else ""
    return CellResult(prompt_id=item.id, tier=item.tier, arm=arm, kind="harness", target=opts.generator,
                      judge=opts.judge, workspace=str(cell), wall_s=round(time.time() - t0, 1),
                      status="infra_failed" if is_infra_failure(tail) else "error",
                      error=f"worker exited {rc} without a cell.json; log tail: {tail[-400:]!r}")


def cell_main(battery_path: Path, out: Path, item_id: str, arm: str, opts: AbOptions) -> int:
    """Child entry: the harness run + fixed evaluation of one arm, via ``run_cell``."""
    from bench._fixed_eval import FixedEvaluator

    battery = Battery.load(battery_path)
    item = next((p for p in battery.prompts if p.id == item_id), None)
    if item is None:
        print(f"no prompt {item_id!r} in {battery_path}", file=sys.stderr)
        return 2
    deps = CompareDeps(FixedEvaluator(opts.judge, n_samples=opts.n_samples))
    res = run_cell(battery, item, _harness_arm(opts.generator), arm_dir(out, arm), opts.compare_options(), deps)
    res.arm, res.target = arm, opts.generator  # the row is keyed by A/B arm, not by generator
    (Path(res.workspace) / "cell.json").write_text(res.model_dump_json(indent=1))
    print(f"cell {arm} {item_id}: status={res.status} score={res.score} ${res.gen_cost_usd + res.judge_cost_usd:.2f}")
    return 0


# ----------------------------------------------------------------------------- the driver
def _plan_todo(battery: Battery, done: dict[tuple[str, str], CellResult], opts: AbOptions) -> list[tuple[BenchPrompt, list[str]]]:
    """Which (prompt, arms) still need running.

    A prompt with EITHER arm in ``redo_status`` is re-run on BOTH arms — a redo exists
    to re-measure the pair together, not to patch one side in different weather.  A
    prompt with only one arm recorded (the driver was interrupted mid-pair) runs only
    the missing arm; its partner already finished and re-running it would pay twice
    for the same generation without making the weather match.
    """
    todo: list[tuple[BenchPrompt, list[str]]] = []
    redo = set(opts.redo_status)
    for p in select_prompts(battery, ids=opts.ids, tiers=opts.tiers, limit=opts.limit):
        rows = {a: done.get((p.id, a)) for a in ARMS}
        if redo and any(r is not None and r.status in redo for r in rows.values()):
            for a in ARMS:
                done.pop((p.id, a), None)
            todo.append((p, list(ARMS)))
            continue
        missing = [a for a in ARMS if rows[a] is None]
        if missing:
            todo.append((p, missing))
    return todo


def run_ab(battery_path: Path | str, out_dir: Path | str, opts: AbOptions, *, run_cell_fn: CellRunner = spawn_cell,
           on_result: Callable[[CellResult], None] | None = None) -> Verdict:
    """Run (or resume) the A/B, one prompt-pair at a time; write pairs.json + summary.md."""
    battery_path, out = Path(battery_path), Path(out_dir)
    battery = Battery.load(battery_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ab.json").write_text(json.dumps({"battery": battery.name, "prompts": str(battery_path),
                                             "arms": {CONTROL: {}, VARIANT: opts.variant_env},
                                             "options": opts.model_dump(mode="json"),
                                             "started_at": datetime.now(UTC).isoformat()}, indent=2))
    results = out / "results.jsonl"
    done = {(r.prompt_id, r.arm): r for r in load_jsonl(results, CellResult)} if opts.resume else {}
    todo = _plan_todo(battery, done, opts)
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool, results.open("a") as fh:
        for item, arms in todo:  # one pair at a time: both arms of a prompt see the same weather
            futs = [pool.submit(run_cell_fn, battery_path, out, item, a, opts) for a in arms]
            for fut in futs:
                r = fut.result()
                done[(r.prompt_id, r.arm)] = r
                fh.write(r.model_dump_json() + "\n")
                fh.flush()
                if on_result:
                    on_result(r)
    order = [(p.id, p.tier) for p in battery.prompts]
    return write_report(out, list(done.values()), order, title=f"{battery.name} / {out.name}",
                        variant_env=opts.variant_env, generator=opts.generator, judge=opts.judge, rounds=opts.rounds)


def preflight(opts: AbOptions, *, wait_minutes: float) -> bool:
    """Probe every model either arm needs (planner, generator, loop judge, fixed judge)."""
    return _preflight(opts.judge, [_harness_arm(opts.generator)], opts.compare_options(), wait_minutes=wait_minutes)


# ----------------------------------------------------------------------------- CLI
def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("cell", help="(internal) run one arm of one prompt in this process")
    w.add_argument("--prompts", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--id", required=True)
    w.add_argument("--arm", required=True, choices=ARMS)
    w.add_argument("--options-json", required=True)
    ap.add_argument("--prompts")
    ap.add_argument("--out")
    ap.add_argument("--variant-env", action="append", default=[], metavar="KEY=VALUE",
                    help="env applied to the VARIANT arm only (repeatable)")
    ap.add_argument("--generator", default=DEFAULT_GENERATOR)
    ap.add_argument("--judge", default=DEFAULT_JUDGE, help="FIXED judge model id (both arms)")
    ap.add_argument("--loop-judge", default=None)
    ap.add_argument("--planner", default=None)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--max-usd", type=float, default=2.5)
    ap.add_argument("--max-minutes", type=float, default=45.0)
    ap.add_argument("--n-samples", type=int, default=2)
    ap.add_argument("--ids", default="")
    ap.add_argument("--tiers", default="")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-in-flight", type=int, default=DEFAULT_MAX_IN_FLIGHT, help=f"per child ({MAX_IN_FLIGHT_ENV})")
    ap.add_argument("--parallel", type=int, default=PARALLEL, help="accepted for symmetry; must be 2 (one pair)")
    ap.add_argument("--redo-status", default="", help="comma list, e.g. infra_failed (re-runs both arms of the prompt)")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--no-preflight", action="store_true", help="skip the provider health check (do not)")
    ap.add_argument("--wait-for-provider", type=float, default=0.0, metavar="MIN")
    ap.add_argument("--allow-siblings", action="store_true",
                    help="launch even when other harness processes are running (they share the quota)")
    ap.add_argument("--report-only", action="store_true", help="only rebuild pairs.json / summary.md")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    if ns.cmd == "cell":
        return cell_main(Path(ns.prompts), Path(ns.out), ns.id, ns.arm, AbOptions.model_validate_json(ns.options_json))
    if not ns.prompts or not ns.out:
        _parser().error("--prompts and --out are required")
    if ns.parallel != PARALLEL:
        _parser().error(f"--parallel must be {PARALLEL}: the arms of a prompt run together and nothing else does")
    opts = AbOptions(generator=ns.generator, judge=ns.judge, loop_judge=ns.loop_judge, planner=ns.planner,
                     rounds=ns.rounds, max_usd=ns.max_usd, max_minutes=ns.max_minutes, n_samples=ns.n_samples,
                     ids=[i for i in ns.ids.split(",") if i], tiers=[t for t in ns.tiers.split(",") if t],
                     limit=ns.limit, resume=not ns.no_resume, redo_status=[s for s in ns.redo_status.split(",") if s],
                     variant_env=parse_variant_env(ns.variant_env), max_in_flight=ns.max_in_flight)
    out = Path(ns.out)
    if ns.report_only:
        battery = Battery.load(ns.prompts)
        v = write_report(out, load_jsonl(out / "results.jsonl", CellResult), [(p.id, p.tier) for p in battery.prompts],
                         title=f"{battery.name} / {out.name}", variant_env=opts.variant_env, generator=opts.generator,
                         judge=opts.judge, rounds=opts.rounds)
        print(f"verdict: {v.decision} — {v.reason}\n{out / 'summary.md'}")
        return 0
    if not opts.variant_env:
        _parser().error("--variant-env KEY=VALUE is required: an A/B with identical arms measures only the judge")
    from codeverse.models.health import pool_budget

    # the rule is a BUDGET, not a head-count: the provider sees one machine, so the sum of
    # every process's in-flight cap must stay at the knee.  Two children run at once here.
    need = 2 * opts.max_in_flight
    if not (pb := pool_budget()).fits(need) and not ns.allow_siblings:
        print(f"refusing to start: {pb}; this A/B needs {need} (2 children x {opts.max_in_flight}) "
              f"(docs/COST.md §23).  Wait, lower --max-in-flight, or pass --allow-siblings.", flush=True)
        return 3
    if not ns.no_preflight and not preflight(opts, wait_minutes=ns.wait_for_provider):
        return 2

    def _log(r: CellResult) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {r.prompt_id:28s} {r.arm:8s} score={r.score} "
              f"${r.gen_cost_usd + r.judge_cost_usd:.2f} {r.wall_s / 60:.1f}min {r.status} {r.error[:80]!r}", flush=True)

    v = run_ab(ns.prompts, out, opts, on_result=_log)
    print(f"\nverdict: {v.decision} — {v.reason}\n{out / 'summary.md'}")
    lost = [r for r in load_jsonl(out / "results.jsonl", CellResult) if r.status == "infra_failed"]
    if lost:
        print(f"{len(lost)} cell(s) lost to provider outages and EXCLUDED from the pairing; re-run both arms of "
              f"those prompts with:  --redo-status infra_failed")
    return 0


__all__ = ["DEFAULT_GENERATOR", "DEFAULT_JUDGE", "MAX_IN_FLIGHT_ENV", "PARALLEL", "AbOptions", "CellRunner",
           "cell_dir", "cell_main", "child_env", "main", "parse_variant_env", "preflight", "run_ab", "spawn_cell",
           "worker_argv"]

if __name__ == "__main__":
    raise SystemExit(main())
