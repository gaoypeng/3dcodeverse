"""Controlled A/B of a plan / brief change: control vs variant, paired per prompt.

    python bench/ab_plan.py --prompts bench/prompts/static_objects_v2.yaml \\
        --variant-env CV3D_PLAN_BRIEF=on --out bench/out/ab_brief \\
        [--generator <backend-id>] [--judge gemini:gemini-3.1-pro-preview] \\
        [--rounds 2] [--ids a,b] [--wait-for-provider 60] [--redo-status infra_failed]

The only thing that differs between the arms is the ``--variant-env`` block, applied to
the VARIANT arm alone.  Everything else is held fixed on both sides: the generator, the
planner, the in-loop judge, the rounds, and the fixed judge that produces
the reported score (``bench/_fixed_eval.py``, ``n_samples`` 2).

Why each arm is a child PROCESS rather than a thread: the switches under test are read
from the environment (``codeverse.tracks.planner.brief_enabled`` reads ``os.environ`` at
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
the pair is re-measured together, archiving the previous attempt first so the surviving
arm regenerates instead of resuming its old answer (:func:`archive_cell`).

What the verdict does NOT tell you: the generator is stochastic, so a paired delta carries
the spread of two independent generations, not the judge's ±0.02 sampling noise.  Run
``--aa`` (both arms identical) on the same battery to measure that floor; every summary
prints the paired sd, the 2 SE band and how many pairs this spread would need before
±0.02 is resolvable (``bench/_ab_report.NOISE_SIGMAS``).  Measured 2026-08-24: two A/A
runs of one prompt came back +0.344 and -0.100 — "keep" and "revert" from identical code.

Layout of ``--out``: ``ab.json`` (the arms, options and variant env) ·
``results.jsonl`` (one ``CellResult`` per (prompt, arm); resume source) ·
``arms/<arm>/cells/<prompt>/<slug>/{run,eval,cell.json,worker.log}`` · ``pairs.json`` ·
``summary.md``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------------------
# sys.path FIRST, and before any `codeverse` import.  `spawn_cell` starts each child as a
# FILE path, so `sys.path[0]` is `bench/` and the cwd is NOT on the path; an editable
# install (`__editable__.3dcodeverse-...pth`) then resolves `import codeverse` to whatever
# tree it was installed from.  Importing `codeverse._compat` above this line made every
# child of a worktree run the MAIN tree's harness, both arms identically, and the A/B
# measured nothing while looking completely healthy — it happened twice on 2026-08-25
# (`bench/out/plan_loop/C0/invalid_attempt1_maintree_import`, and again to the skills wave).
REPO = Path(__file__).resolve().parents[2] / "harness"   # the harness tree these scripts evaluate
for _p in (REPO, Path(__file__).resolve().parents[1]):      # its codeverse + the `bench` package (eval/)
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from datetime import UTC  # noqa: E402


def _assert_local_codeverse() -> None:
    """Refuse to run against a `codeverse` from a different tree.

    A workaround in a launch script (`export PYTHONPATH=...`) protects the person who
    remembers it.  This protects the run.
    """
    import codeverse

    got = Path(codeverse.__file__).resolve().parent
    want = REPO / "codeverse"
    if got != want:
        raise SystemExit(
            f"refusing to run: `import codeverse` resolved to {got}, not {want}.\n"
            f"  An editable install is shadowing this tree, so both arms would run the same\n"
            f"  code and the A/B would measure nothing.  Launch with:\n"
            f"      PYTHONPATH={REPO} python bench/ab_plan.py ..."
        )


_assert_local_codeverse()

from bench._ab_report import ARMS, CONTROL, VARIANT, Verdict, write_report  # noqa: E402
from bench._compare_report import CellResult, load_jsonl  # noqa: E402
from bench._infra import is_infra_failure  # noqa: E402
from bench._jsonl import seal_for_append  # noqa: E402
from bench.compare_backends import (  # noqa: E402
    Arm,
    CompareDeps,
    CompareOptions,
    _preflight,
    run_cell,
    spec_for,  # noqa: E402
)
from bench.pin_plan import PLAN_JSON, PinError, plan_once, seed_plan  # noqa: E402
from bench.run_bench import Battery, BenchPrompt, select_prompts  # noqa: E402
from codeverse.contracts.common import Backends  # noqa: E402
from codeverse.proc import exclusive  # noqa: E402
from codeverse.tracks.plan_features import pin_plan_blockers  # noqa: E402
from codeverse.workspace import Workspace  # noqa: E402

log = logging.getLogger(__name__)

#: derived from the canonical default (codeverse/contracts/common.py Backends.generator),
#: never a literal: a frozen baseline arm must be named at its use site, not hidden here.
DEFAULT_GENERATOR = Backends().generator
DEFAULT_JUDGE = "gemini:gemini-3.1-pro-preview"
#: the per-child cap.  The flat name is a first-class alias of ``CV3D_RATE__MAX_IN_FLIGHT``
#: since 2026-08-24 (``Settings._FLAT_ALIASES``; before that it was read by nothing) and
#: wins over the nested spelling when both are set.
MAX_IN_FLIGHT_ENV = "CV3D_MAX_IN_FLIGHT"
#: the nested spelling: popped from every child's env so it cannot fight the flat one
NESTED_MAX_IN_FLIGHT_ENV = "CV3D_RATE__MAX_IN_FLIGHT"
DEFAULT_MAX_IN_FLIGHT = 16
#: only ever 2 — one control + one variant, launched together (see module docstring)
PARALLEL = 2


class AbOptions(BaseModel):
    generator: str = DEFAULT_GENERATOR
    judge: str = DEFAULT_JUDGE
    loop_judge: str | None = Field(
        default=None, description="in-loop judge for BOTH arms (None → settings default)"
    )
    planner: str | None = Field(
        default=None, description="planner for BOTH arms (None → settings default)"
    )
    rounds: int = 2
    max_minutes: float = 45.0
    n_samples: int = 2
    ids: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    limit: int | None = None
    resume: bool = True
    redo_status: list[str] = Field(
        default_factory=list, description="re-run prompts where EITHER arm has one of these"
    )
    variant_env: dict[str, str] = Field(
        default_factory=dict, description="applied to the variant arm only"
    )
    max_in_flight: int = DEFAULT_MAX_IN_FLIGHT
    aa: bool = Field(
        default=False,
        description="calibration: run BOTH arms as the control, so the delta IS the noise",
    )
    redo_fresh: bool = Field(
        default=True, description="a redo regenerates both arms instead of resuming them"
    )
    pin_plan: bool = Field(
        default=False,
        description="plan ONCE per prompt and seed both arms with it "
        "(generation-side switches only: see pin_plan_blockers)",
    )

    def compare_options(self) -> CompareOptions:
        """The per-cell options handed to ``compare_backends.run_cell`` (both arms identical)."""
        return CompareOptions(
            judge=self.judge,
            loop_judge=self.loop_judge,
            planner=self.planner,
            rounds=self.rounds,
            max_minutes=self.max_minutes,
            n_samples=self.n_samples,
            resume=self.resume,
            pairwise=False,
        )


def inherited_max_in_flight() -> int:
    """The cap the launching shell asks for, used only when ``--max-in-flight`` is absent.

    Whatever wins here is BOTH what the children run at and what the budget preflight
    reserves; the two must never disagree, which is why the answer is resolved once, into
    ``AbOptions``, instead of once per child out of the ambient environment.
    """
    for name in (MAX_IN_FLIGHT_ENV, NESTED_MAX_IN_FLIGHT_ENV):
        raw = os.environ.get(name, "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    return DEFAULT_MAX_IN_FLIGHT


def parse_variant_env(items: Sequence[str]) -> dict[str, str]:
    """``["K=V", "K2=V2"]`` → ``{K: V, K2: V2}``; a bare ``K`` is rejected, not defaulted."""
    out: dict[str, str] = {}
    for it in items:
        k, sep, v = it.partition("=")
        if not sep or not k.strip():
            raise ValueError(f"--variant-env needs KEY=VALUE, got {it!r}")
        if k.strip() in (MAX_IN_FLIGHT_ENV, NESTED_MAX_IN_FLIGHT_ENV):
            # child_env sets the cap authoritatively so the §23 admission check and the
            # children agree; accepting it here too would silently discard one of them.
            raise ValueError(
                f"--variant-env {k.strip()} is not allowed: the in-flight cap is "
                f"the driver's (--max-in-flight), and the admission check budgets on it"
            )
        out[k.strip()] = v
    return out


def child_env(arm: str, opts: AbOptions, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of one arm's child process.

    The variant gets ``variant_env`` on top of the driver's env; the control gets the
    driver's env with those same keys REMOVED, so a switch that happens to be set in
    the launching shell cannot silently turn the control into a second variant.  Both
    are PINNED to ``opts.max_in_flight`` (both spellings), never merely defaulted to it:
    ``main`` refuses to start unless ``pool_budget().fits(2 * opts.max_in_flight)``, so a
    child that inherited a different cap from the launching shell would make that
    reservation a fiction — with ``--max-in-flight 8`` under a shell exporting 32 the A/B
    reserved 16 of the 64-call knee and consumed 64 (docs/COST.md §23).  ``opts`` already
    carries the inherited value when no flag was passed (:func:`inherited_max_in_flight`).

    Under ``--aa`` the variant arm gets the CONTROL environment: the two arms then run
    byte-identical code and the measured delta is the rig's own noise floor, which is the
    number every A/B verdict has to be read against (``bench/_ab_report.NOISE_SIGMAS``).
    """
    env = dict(os.environ if base is None else base)
    for k in opts.variant_env:
        env.pop(k, None)
    if arm == VARIANT and not opts.aa:
        env.update(opts.variant_env)
    env[MAX_IN_FLIGHT_ENV] = str(opts.max_in_flight)
    env.pop(NESTED_MAX_IN_FLIGHT_ENV, None)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def arm_dir(out: Path, arm: str) -> Path:
    return out / "arms" / arm


def cell_dir(out: Path, arm: str, item_id: str, generator: str) -> Path:
    """Where ``compare_backends.run_cell`` puts this arm's cell (its layout, our root)."""
    return arm_dir(out, arm) / "cells" / item_id / _harness_arm(generator).slug


def _harness_arm(generator: str) -> Arm:
    return Arm(raw=f"harness:{generator}", kind="harness", target=generator)


def worker_argv(
    battery_path: Path, out: Path, item_id: str, arm: str, opts: AbOptions
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "cell",
        "--prompts",
        str(battery_path),
        "--out",
        str(out),
        "--id",
        item_id,
        "--arm",
        arm,
        "--options-json",
        opts.model_dump_json(),
    ]


# ----------------------------------------------------------------------------- one cell, in a child
CellRunner = Callable[[Path, Path, BenchPrompt, str, AbOptions], CellResult]


def spawn_cell(
    battery_path: Path, out: Path, item: BenchPrompt, arm: str, opts: AbOptions
) -> CellResult:
    """Run one (prompt, arm) cell in a child process and read back its ``cell.json``.

    A child that dies without writing a cell (killed, import error, unhandled outage)
    is classified by the tail of its log with the same rule every other failure path
    uses, so an outage stays ``infra_failed`` and never becomes a scored zero.
    """
    cell = cell_dir(out, arm, item.id, opts.generator)
    cell.mkdir(parents=True, exist_ok=True)
    log, marker = cell / "worker.log", cell / "cell.json"
    marker.unlink(
        missing_ok=True
    )  # a stale verdict from an earlier attempt must not stand in for this one
    t0 = time.time()
    with log.open("a") as fh:
        fh.write(f"--- {datetime.now(UTC).isoformat()} {arm} {item.id}\n")
        fh.flush()
        rc = subprocess.run(
            worker_argv(battery_path, out, item.id, arm, opts),
            cwd=REPO,
            env=child_env(arm, opts),
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode
    if marker.is_file():
        return CellResult.model_validate_json(marker.read_text())
    # errors="replace": the child's raw stdout lands in this log, and one non-UTF-8 byte
    # from an agent CLI used to raise UnicodeDecodeError *here* — on the very path whose
    # job is to classify an outage — killing the whole driver through fut.result().
    tail = log.read_text(errors="replace")[-1500:] if log.is_file() else ""
    return CellResult(
        prompt_id=item.id,
        tier=item.tier,
        arm=arm,
        kind="harness",
        target=opts.generator,
        judge=opts.judge,
        workspace=str(cell),
        wall_s=round(time.time() - t0, 1),
        status="infra_failed" if is_infra_failure(tail) else "error",
        error=f"worker exited {rc} without a cell.json; log tail: {tail[-400:]!r}",
    )


def cell_main(battery_path: Path, out: Path, item_id: str, arm: str, opts: AbOptions) -> int:
    """Child entry: the harness run + fixed evaluation of one arm, via ``run_cell``."""
    from bench._fixed_eval import FixedEvaluator

    battery = Battery.load(battery_path)
    item = next((p for p in battery.prompts if p.id == item_id), None)
    if item is None:
        print(f"no prompt {item_id!r} in {battery_path}", file=sys.stderr)
        return 2
    deps = CompareDeps(FixedEvaluator(opts.judge, n_samples=opts.n_samples))
    res = run_cell(
        battery, item, _harness_arm(opts.generator), arm_dir(out, arm), opts.compare_options(), deps
    )
    res.arm, res.target = arm, opts.generator  # the row is keyed by A/B arm, not by generator
    (Path(res.workspace) / "cell.json").write_text(res.model_dump_json(indent=1))
    print(
        f"cell {arm} {item_id}: status={res.status} score={res.score} ${res.gen_cost_usd + res.judge_cost_usd:.2f}"
    )
    return 0


# ----------------------------------------------------------------------------- the driver
class Todo(NamedTuple):
    """One unit of work: a prompt, the arms of it still owed, and whether to start over."""

    item: BenchPrompt
    arms: list[str]
    fresh: bool = False


def _plan_todo(
    battery: Battery, done: dict[tuple[str, str], CellResult], opts: AbOptions
) -> list[Todo]:
    """Which (prompt, arms) still need running.

    A prompt with EITHER arm in ``redo_status`` is re-run on BOTH arms — a redo exists
    to re-measure the pair together, not to patch one side in different weather.  A
    prompt with only one arm recorded (the driver was interrupted mid-pair) runs only
    the missing arm; its partner already finished and re-running it would pay twice
    for the same generation without making the weather match.
    """
    todo: list[Todo] = []
    redo = set(opts.redo_status)
    for p in select_prompts(battery, ids=opts.ids, tiers=opts.tiers, limit=opts.limit):
        rows = {a: done.get((p.id, a)) for a in ARMS}
        if redo and any(r is not None and r.status in redo for r in rows.values()):
            for a in ARMS:
                done.pop((p.id, a), None)
            todo.append(Todo(p, list(ARMS), fresh=opts.redo_fresh))
            continue
        missing = [a for a in ARMS if rows[a] is None]
        if missing:
            todo.append(Todo(p, missing))
    return todo


def archive_cell(out: Path, arm: str, item_id: str, opts: AbOptions) -> Path | None:
    """Move a finished attempt aside so the next one REGENERATES; ``None`` if there was none.

    ``compare_backends._run_harness`` resumes whenever ``<cell>/run`` exists — it never
    consults ``resume`` — which is right for a harness run recovering from a crash and
    wrong for this rig.  Without this, ``--redo-status infra_failed`` re-ran the pair on
    paper only: the arm that had SUCCEEDED resumed its finished workspace and handed back
    its old score, generated in the old weather, while its partner generated afresh in
    today's.  That is precisely the cross-weather comparison the pairing exists to
    prevent, and it was invisible in the report.

    The old attempt is renamed, never deleted: it is the evidence for the outage that
    caused the redo, and it holds that attempt's cost ledger.
    """
    cell = cell_dir(out, arm, item_id, opts.generator)
    if not cell.exists():
        return None
    for n in count(1):
        dest = cell.with_name(f"{cell.name}.attempt{n}")
        if not dest.exists():
            cell.rename(dest)
            return dest
    raise AssertionError("unreachable")  # pragma: no cover — count() is infinite


def pin_pair(
    battery: Battery, item: BenchPrompt, out: Path, arms: Sequence[str], opts: AbOptions
) -> str:
    """Plan ``item`` ONCE and seed that plan into every arm about to run; return its hash.

    The A/A of this rig measured paired sd 0.202 on the judged score and traced it to the
    PLANNER — its worst pair planned 1 part against 10 on identical settings (docs/EVAL.md
    §8.1).  A generation-side switch cannot change planning, so both arms may share one plan
    and the paired difference stops carrying that spread.  ``pin_plan_blockers`` decides
    whether that is true of THIS variant_env; ``main`` refuses the run when it is not.

    Both arms are seeded from the same third workspace rather than the variant from the
    control's run, so the pair still launches together and sees the same provider weather.
    The returned ``inputs_hash`` is asserted equal for every arm: it is derived from the
    spec, so a mismatch means the arms were not planning the same thing and the seed would
    be a cache MISS — a silently UNpinned pair, which is the one failure this must not have.
    """
    spec = spec_for(battery, item, _harness_arm(opts.generator), opts.compare_options())
    src = Workspace(out / "plans" / item.id / "run")
    if not (src.root / PLAN_JSON).is_file():
        with exclusive(src.root, what=f"ab pin {item.id}"):  # one writer per run dir
            plan_once(spec, src.root)
    want = json.loads((src.root / "run_state.json").read_text())["stages"]["plan"]["inputs_hash"]
    for arm in arms:
        dst = Workspace(cell_dir(out, arm, item.id, opts.generator) / "run")
        dst.create()
        dst.write_json(dst.spec_path, spec)  # _run_harness only writes it when it creates the ws
        got = seed_plan(src.root, dst.root)
        if got != want:  # pragma: no cover — same spec both sides; the assert is the point
            raise PinError(
                f"{item.id}/{arm}: seeded plan hash {got} != {want}; the pair would be unpinned"
            )
    return want


def run_ab(
    battery_path: Path | str,
    out_dir: Path | str,
    opts: AbOptions,
    *,
    run_cell_fn: CellRunner = spawn_cell,
    on_result: Callable[[CellResult], None] | None = None,
) -> Verdict:
    """Run (or resume) the A/B, one prompt-pair at a time; write pairs.json + summary.md."""
    battery_path, out = Path(battery_path), Path(out_dir)
    battery = Battery.load(battery_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ab.json").write_text(
        json.dumps(
            {
                "battery": battery.name,
                "prompts": str(battery_path),
                "aa": opts.aa,
                "arms": {CONTROL: {}, VARIANT: {} if opts.aa else opts.variant_env},
                "options": opts.model_dump(mode="json"),
                "started_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )
    results = out / "results.jsonl"
    done = {(r.prompt_id, r.arm): r for r in load_jsonl(results, CellResult)} if opts.resume else {}
    todo = _plan_todo(battery, done, opts)
    seal_for_append(results)  # a kill left the last row unterminated; do not glue onto it
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool, results.open("a") as fh:
        for (
            item,
            arms,
            fresh,
        ) in todo:  # one pair at a time: both arms of a prompt see the same weather
            if fresh:
                for a in arms:
                    archive_cell(out, a, item.id, opts)
            if opts.pin_plan:
                try:
                    pin_pair(battery, item, out, arms, opts)
                except Exception as e:  # noqa: BLE001 — one prompt's planner outage must not end the battery
                    # The pinned plan is ONE model call with a 15-minute retry budget
                    # (models/retry.py RETRY_DEADLINE_S).  When a 503 storm outlasts it the call
                    # raises, and this used to propagate straight out of the loop: measured
                    # 2026-08-26, a driver of 3 prompts died on its first pin with 2 prompts
                    # never attempted, while the drivers beside it waited the storm out.  The
                    # pair is recorded the way a dead cell is (docs/EVAL.md §7 — an outage is
                    # not a score) so `--redo-status infra_failed` picks it up, and the loop
                    # goes on to the next prompt.
                    status = "infra_failed" if is_infra_failure(str(e)) else "error"
                    log.warning(
                        "%s: pinned plan failed (%s) — pair recorded %s, continuing",
                        item.id,
                        e,
                        status,
                    )
                    for a in arms:
                        r = CellResult(
                            prompt_id=item.id,
                            tier=item.tier,
                            arm=a,
                            kind="harness",
                            target=opts.generator,
                            status=status,
                            error=f"pinned plan: {type(e).__name__}: {e}"[:600],
                        )
                        done[(r.prompt_id, r.arm)] = r
                        fh.write(r.model_dump_json() + "\n")
                        fh.flush()
                        if on_result:
                            on_result(r)
                    continue
            futs = [pool.submit(run_cell_fn, battery_path, out, item, a, opts) for a in arms]
            for fut in futs:
                r = fut.result()
                done[(r.prompt_id, r.arm)] = r
                fh.write(r.model_dump_json() + "\n")
                fh.flush()
                if on_result:
                    on_result(r)
    order = [(p.id, p.tier) for p in battery.prompts]
    return write_report(
        out,
        list(done.values()),
        order,
        title=f"{battery.name} / {out.name}",
        variant_env={} if opts.aa else opts.variant_env,
        generator=opts.generator,
        judge=opts.judge,
        rounds=opts.rounds,
        aa=opts.aa,
    )


def preflight(opts: AbOptions, *, wait_minutes: float) -> bool:
    """Probe every model either arm needs (planner, generator, loop judge, fixed judge)."""
    return _preflight(
        opts.judge,
        [_harness_arm(opts.generator)],
        opts.compare_options(),
        wait_minutes=wait_minutes,
    )


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
    ap.add_argument(
        "--variant-env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="env applied to the VARIANT arm only (repeatable)",
    )
    ap.add_argument("--generator", default=DEFAULT_GENERATOR)
    ap.add_argument("--judge", default=DEFAULT_JUDGE, help="FIXED judge model id (both arms)")
    ap.add_argument("--loop-judge", default=None)
    ap.add_argument("--planner", default=None)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--max-minutes", type=float, default=45.0)
    ap.add_argument("--n-samples", type=int, default=2)
    ap.add_argument("--ids", default="")
    ap.add_argument("--tiers", default="")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--max-in-flight",
        type=int,
        default=None,
        help=f"per child; wins over an inherited {MAX_IN_FLIGHT_ENV} "
        f"(default: that variable, else {DEFAULT_MAX_IN_FLIGHT}).  The preflight reserves 2x it",
    )
    ap.add_argument(
        "--parallel", type=int, default=PARALLEL, help="accepted for symmetry; must be 2 (one pair)"
    )
    ap.add_argument(
        "--redo-status",
        default="",
        help="comma list, e.g. infra_failed (re-runs both arms of the prompt)",
    )
    ap.add_argument(
        "--redo-resume",
        action="store_true",
        help="let a redo RESUME the old cells instead of regenerating them (cheap, but the pair then "
        "spans two weathers — see archive_cell)",
    )
    ap.add_argument(
        "--pin-plan",
        action="store_true",
        help="plan once per prompt and seed BOTH arms with it, removing the planner's "
        "spread from the pairing; refused for a plan-side --variant-env",
    )
    ap.add_argument(
        "--aa",
        action="store_true",
        help="calibration run: both arms identical, so the measured delta IS this rig's noise floor",
    )
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument(
        "--no-preflight", action="store_true", help="skip the provider health check (do not)"
    )
    ap.add_argument("--wait-for-provider", type=float, default=0.0, metavar="MIN")
    ap.add_argument(
        "--allow-siblings",
        action="store_true",
        help="launch even when other harness processes are running (they share the quota)",
    )
    ap.add_argument(
        "--report-only", action="store_true", help="only rebuild pairs.json / summary.md"
    )
    return ap


def _stored_options(out: Path) -> AbOptions | None:
    """The options this run was launched with, from its own ``ab.json``."""
    p = out / "ab.json"
    if not p.is_file():
        return None
    try:
        return AbOptions.model_validate(json.loads(p.read_text())["options"])
    except (OSError, KeyError, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    if ns.cmd == "cell":
        return cell_main(
            Path(ns.prompts),
            Path(ns.out),
            ns.id,
            ns.arm,
            AbOptions.model_validate_json(ns.options_json),
        )
    if not ns.prompts or not ns.out:
        _parser().error("--prompts and --out are required")
    if ns.parallel != PARALLEL:
        _parser().error(
            f"--parallel must be {PARALLEL}: the arms of a prompt run together and nothing else does"
        )
    opts = AbOptions(
        generator=ns.generator,
        judge=ns.judge,
        loop_judge=ns.loop_judge,
        planner=ns.planner,
        rounds=ns.rounds,
        max_minutes=ns.max_minutes,
        n_samples=ns.n_samples,
        ids=[i for i in ns.ids.split(",") if i],
        tiers=[t for t in ns.tiers.split(",") if t],
        limit=ns.limit,
        resume=not ns.no_resume,
        redo_status=[s for s in ns.redo_status.split(",") if s],
        variant_env=parse_variant_env(ns.variant_env),
        max_in_flight=ns.max_in_flight
        if ns.max_in_flight is not None
        else inherited_max_in_flight(),
        aa=ns.aa,
        redo_fresh=not ns.redo_resume,
        pin_plan=ns.pin_plan,
    )
    out = Path(ns.out)
    if ns.report_only:
        battery = Battery.load(ns.prompts)
        # the RUN's own options, not this invocation's flags.  `--report-only` is normally
        # typed with no --variant-env, and reporting the arms as identical would relabel a
        # real A/B as an A/A in the file everyone reads afterwards.
        opts = _stored_options(out) or opts
        v = write_report(
            out,
            load_jsonl(out / "results.jsonl", CellResult),
            [(p.id, p.tier) for p in battery.prompts],
            title=f"{battery.name} / {out.name}",
            variant_env={} if opts.aa else opts.variant_env,
            generator=opts.generator,
            judge=opts.judge,
            rounds=opts.rounds,
            aa=opts.aa,
        )
        print(
            f"verdict: {v.decision} — {v.reason}"
            + (f"\nCAUTION: {v.caution}" if v.caution else "")
            + f"\n{out / 'summary.md'}"
        )
        return 0
    if not opts.variant_env and not opts.aa:
        _parser().error(
            "--variant-env KEY=VALUE is required: an A/B with identical arms measures only the noise "
            "(pass --aa if measuring the noise is the point)"
        )
    if not opts.aa:
        # A KEY no code path reads makes the variant arm byte-identical to the control, so
        # the battery costs a full run and yields a verdict about nothing.  One such A/B is
        # on record printing "keep, mean delta +0.344" (CQ-5).
        from codeverse.tracks.plan_features import DEAD_SWITCHES, dead_env_keys

        dead = dead_env_keys(opts.variant_env)
        if dead and len(dead) == len(opts.variant_env):
            _parser().error(
                "--variant-env only sets switches nothing reads, so both arms would be identical: "
                + "; ".join(f"{k} ({DEAD_SWITCHES[k]})" for k in dead)
                + " (pass --aa if an identical-arms calibration run is the point)"
            )
    if opts.pin_plan and (blockers := pin_plan_blockers(opts.variant_env)):
        _parser().error(
            "--pin-plan is refused for a PLAN-side switch: sharing one plan across the arms would delete the very "
            "thing under test and the rig would report 'no effect' with confidence — "
            + "; ".join(blockers)
        )
    from codeverse.models.health import pool_budget

    # the rule is a BUDGET, not a head-count: the provider sees one machine, so the sum of
    # every process's in-flight cap must stay at the knee.  Two children run at once here.
    need = 2 * opts.max_in_flight
    if not (pb := pool_budget()).fits(need) and not ns.allow_siblings:
        print(
            f"refusing to start: {pb}; this A/B needs {need} (2 children x {opts.max_in_flight}) "
            f"(docs/COST.md §23).  Wait, lower --max-in-flight, or pass --allow-siblings.",
            flush=True,
        )
        return 3
    if not ns.no_preflight and not preflight(opts, wait_minutes=ns.wait_for_provider):
        return 2

    def _log(r: CellResult) -> None:
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] {r.prompt_id:28s} {r.arm:8s} score={r.score} "
            f"${r.gen_cost_usd + r.judge_cost_usd:.2f} {r.wall_s / 60:.1f}min {r.status} {r.error[:80]!r}",
            flush=True,
        )

    v = run_ab(ns.prompts, out, opts, on_result=_log)
    print(
        f"\nverdict: {v.decision} — {v.reason}"
        + (f"\nCAUTION: {v.caution}" if v.caution else "")
        + f"\n{out / 'summary.md'}"
    )
    lost = [r for r in load_jsonl(out / "results.jsonl", CellResult) if r.status == "infra_failed"]
    if lost:
        print(
            f"{len(lost)} cell(s) lost to provider outages and EXCLUDED from the pairing; re-run both arms of "
            f"those prompts with:  --redo-status infra_failed"
        )
    return 0


__all__ = [
    "DEFAULT_GENERATOR",
    "DEFAULT_JUDGE",
    "MAX_IN_FLIGHT_ENV",
    "NESTED_MAX_IN_FLIGHT_ENV",
    "PARALLEL",
    "AbOptions",
    "CellRunner",
    "Todo",
    "archive_cell",
    "cell_dir",
    "cell_main",
    "child_env",
    "inherited_max_in_flight",
    "main",
    "parse_variant_env",
    "pin_pair",
    "preflight",
    "run_ab",
    "spawn_cell",
    "worker_argv",
]

if __name__ == "__main__":
    raise SystemExit(main())
