"""Harness vs raw one-shot generation under ONE fixed judge (methodology bench).

    python bench/compare_backends.py --prompts bench/prompts/compare_v1.yaml \\
        --arms harness:gemini-cli:gemini-3.6-flash,harness:gemini-cli:gemini-3.7-flash,\\
oneshot:claude-code,oneshot:codex,oneshot:gemini:gemini-3.7-flash \\
        --judge gemini:gemini-3.1-pro-preview --out <dir> [--parallel 8] [--limit N]

Arms
* ``harness:<generator-id>`` — the full static_object track (plan → generate →
  build/repair → gates → render → judge → refine, rounds ≤ ``--rounds``).  Its
  in-loop judge is ``--loop-judge`` (default: the settings default, flash); the
  loop's own score is NOT the reported score.
* ``oneshot:<x>`` — ONE raw generation (prompt + minimal contract, no tools, no
  cookbook, no plan, no repair).  ``x`` ∈ ``claude-code[:m]`` · ``codex[:m]`` ·
  ``gemini|anthropic|openai:<m>``.
* ``oneshot+repair:<x>`` — same, plus ≤ ``--repair-attempts`` error-feedback
  retries on build failure (clearly labelled; never judge feedback).

Every arm ends with a ``src/model.py`` that is copied into a fresh eval workspace
and scored by the SAME fixed evaluator: BlenderRuntime lint+build → measure →
connectivity gate → 8-view ``render_glb`` → ``VlmJudge(static_object_v1, judge,
n_samples=2)`` whose acceptance checklist is the battery's ``must_have`` list.
A failed build (or unparseable answer) scores 0 with the error recorded.  Then a
pairwise arena (``PairwiseJudge``, same judge model, both orders) runs every
harness arm against every one-shot arm per prompt.

Layout of ``--out``: ``matrix.json`` · ``results.jsonl`` (cells; resume source) ·
``pairwise.jsonl`` · ``cells/<prompt>/<arm>/{run,gen,eval}`` · ``report.md`` ·
``report.html`` (+ ``report_assets/``).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# sys.path BEFORE any `codeverse` import: this file is also run as a script, and an
# editable install would otherwise resolve `codeverse` to the tree it was installed from
# rather than this one.  See the same note in `bench/ab_plan.py`.
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # `python bench/compare_backends.py` from anywhere
    sys.path.insert(0, str(REPO))

from datetime import UTC  # noqa: E402

from bench._compare_report import (  # noqa: E402
    CellResult,
    PairRow,
    build_compare_report,
    load_jsonl,
)
from bench._fixed_eval import RUBRIC, EvalOutcome, FixedEvaluator  # noqa: E402
from bench._infra import is_budget_exhaustion, is_infra_failure  # noqa: E402
from bench._jsonl import seal_for_append  # noqa: E402
from bench._oneshot import (  # noqa: E402
    OneShotBackend,
    OneShotResult,
    files_for,
    get_oneshot_backend,
    oneshot_prompt,
    repair_prompt,
    write_answer_files,
)
from bench.run_bench import (  # noqa: E402
    Battery,
    BenchPrompt,
    archive_attempt,
    build_spec,
    default_run_track,
    select_prompts,
)
from codeverse.config import get_settings  # noqa: E402
from codeverse.contracts.artifacts import RenderSet  # noqa: E402
from codeverse.contracts.common import ENTRY_FILE  # noqa: E402
from codeverse.contracts.run import RunRecord  # noqa: E402
from codeverse.contracts.spec import Spec  # noqa: E402
from codeverse.cost import run_ledger  # noqa: E402
from codeverse.proc import exclusive  # noqa: E402
from codeverse.tracks.generation import MultiFileParseError  # noqa: E402
from codeverse.tracks.planner import PlanningError  # noqa: E402
from codeverse.workspace import Workspace  # noqa: E402

log = logging.getLogger(__name__)

ArmKind = Literal["harness", "oneshot", "oneshot+repair"]


# ----------------------------------------------------------------------------- arms / options
class Arm(BaseModel):
    raw: str
    kind: ArmKind
    target: str = Field(description="generator id (harness) or one-shot target")

    @property
    def slug(self) -> str:
        return self.raw.replace("+", "_plus_").replace(":", "_").replace("/", "_")


def parse_arm(text: str) -> Arm:
    kind, sep, target = text.strip().partition(":")
    if not sep or not target or kind not in ("harness", "oneshot", "oneshot+repair"):
        raise ValueError(f"bad arm {text!r}: expected harness:<generator-id> | oneshot:<target> | oneshot+repair:<target>")
    if kind != "harness":
        get_oneshot_backend(target)  # validates the target early
    return Arm(raw=text.strip(), kind=kind, target=target)  # type: ignore[arg-type]


def parse_arms(text: str) -> list[Arm]:
    arms = [parse_arm(t) for t in text.split(",") if t.strip()]
    if not arms:
        raise ValueError("no arms given")
    if len({a.raw for a in arms}) != len(arms):
        raise ValueError("duplicate arms")
    return arms


class CompareOptions(BaseModel):
    judge: str = "gemini:gemini-3.1-pro-preview"
    loop_judge: str | None = Field(default=None, description="harness in-loop judge (None → settings default)")
    planner: str | None = None
    rounds: int = 3
    max_minutes: float = 45.0
    parallel: int = 8  # measured knee, see BenchOptions.parallel / docs/COST.md Part III
    limit: int | None = None
    ids: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    resume: bool = True
    gen_timeout_s: float = 900.0
    repair_attempts: int = 2
    n_samples: int = 2
    pairwise: bool = True
    degraded_min_wall_s: float = Field(default=40 * 60, description="flag_degraded: a harness run this long that never iterated waited, it did not work")
    degraded_max_rounds: int = Field(default=1, description="flag_degraded: completed rounds at or below this count")
    redo_status: list[str] = Field(default_factory=list, description="re-run cells whose status is one of these")


def spec_for(battery: Battery, item: BenchPrompt, arm: Arm, opts: CompareOptions) -> Spec:
    generator = arm.target if arm.kind == "harness" else f"single-shot:{arm.target}"
    backends = get_settings().backends(generator=generator, judge=opts.loop_judge, planner=opts.planner)
    return build_spec(battery, item, backends=backends, rounds=opts.rounds,
                      max_minutes=opts.max_minutes, tag0="compare", extra_tags=(arm.kind,))


# ----------------------------------------------------------------------------- deps (fakeable seams)
RunTrackFn = Callable[[Spec, Workspace, bool], RunRecord]


class CompareDeps:
    """Everything that touches a model / Blender / Chrome; tests swap these for fakes."""

    def __init__(self, evaluator: FixedEvaluator | Any, *, run_track: RunTrackFn | None = None,
                 oneshot_backend: Callable[[str], OneShotBackend] | None = None,
                 pairwise_judge: Callable[[str], Any] | None = None):
        self.evaluator = evaluator
        self.run_track = run_track or default_run_track
        self.oneshot_backend = oneshot_backend or get_oneshot_backend
        self.pairwise_judge = pairwise_judge or _default_pairwise


def _default_pairwise(model_id: str) -> Any:
    from codeverse.judges.pairwise import PairwiseJudge

    return PairwiseJudge(model_id)


# ----------------------------------------------------------------------------- one cell
def _fresh_ws(path: Path) -> Workspace:
    if path.exists():
        shutil.rmtree(path)
    return Workspace(path).create()


def _generate_oneshot(arm: Arm, spec: Spec, cell: Path, eval_ws: Workspace, opts: CompareOptions,
                      deps: CompareDeps, res: CellResult) -> None:
    backend = deps.oneshot_backend(arm.target)
    prompt = oneshot_prompt(spec)
    max_attempts = 1 + (opts.repair_attempts if arm.kind == "oneshot+repair" else 0)
    for attempt in range(max_attempts):
        gen_dir = cell / "gen" / f"attempt{attempt}"
        cached = gen_dir / "result.json"
        if opts.resume and cached.is_file():  # a redo re-uses the recorded answer: no second subscription call
            gen = OneShotResult.model_validate_json(cached.read_text())
        else:
            gen = backend.generate(prompt, out_dir=gen_dir, timeout_s=opts.gen_timeout_s, label=f"oneshot_{spec.id.split('/')[-1]}")
            if gen.ok:  # failures (timeouts, 5xx) are not cached so a redo regenerates
                gen_dir.mkdir(parents=True, exist_ok=True)
                cached.write_text(gen.model_dump_json(indent=1))
        res.attempts = attempt + 1
        res.gen_cost_usd += gen.usage.cost_usd
        res.tool_calls += gen.tool_calls
        res.gen_seconds += gen.duration_s
        if not gen.ok:
            res.error = gen.notes or "empty answer"
            res.error_is_infra = gen.infra_failed
            return
        try:
            write_answer_files(eval_ws, gen.text, spec.language)
        except MultiFileParseError as e:
            res.error = f"unparseable answer: {e}"
            return
        res.error = ""
        if attempt + 1 >= max_attempts:
            return
        build, lint = deps.evaluator.build(eval_ws, spec.language)  # repair arm: error feedback only
        if build.ok and not lint.errors:
            return
        previous = {rel: (eval_ws.root / rel).read_text() for rel in files_for(spec.language) if (eval_ws.root / rel).is_file()}
        prompt = repair_prompt(spec, previous, build, lint, attempt + 1)


def entry_of(spec: Spec) -> str:
    """The file THIS spec's language delivers its code in.

    ``_oneshot.MODEL_FILE`` is ``src/model.py`` because the one-shot arms are a
    blender-only comparison (their contract prompt is literally python).  The HARNESS arm
    is not: a glsl run delivers ``src/shader.frag``, three.js ``src/object.js``, a scene
    ``src/scene.js``, moderngl ``src/program.py``.  Gating the harness arm on the one-shot
    constant made every cell in those four languages ``no_code`` / **0.0** while the run
    itself came back ``passed`` — a rig failure wearing a capability result's clothes, and
    invisible in the summary.  ``ENTRY_FILE`` is the canonical table; consult it.
    """
    return ENTRY_FILE[spec.language]


def _run_harness(spec: Spec, cell: Path, eval_ws: Workspace, opts: CompareOptions,
                 deps: CompareDeps, res: CellResult) -> None:
    run_ws = Workspace(cell / "run")
    if run_ws.exists() and not opts.resume:
        # --no-resume regenerates every other arm (a recorded one-shot answer is not re-used
        # either), so a harness arm that resumed its FINISHED workspace handed back its old
        # score, generated in the old weather, against a partner generated in today's — the
        # cross-weather comparison the pairing exists to prevent (ab_plan.archive_cell has
        # the measured story).  The old tree is archived, never deleted: it holds that
        # attempt's cost ledger.
        archive_attempt(run_ws.root)
    resume = run_ws.exists()
    if not resume:
        run_ws.create()
        run_ws.write_json(run_ws.spec_path, spec)
    # the harness arm is a real run: one writer per run dir (--parallel is threads of ONE
    # process), and its own ledger nested inside the cell's (run_ledger restores the outer
    # one on the way out, so the fixed evaluation that follows lands in the cell ledger)
    with (exclusive(run_ws.root, what=f"compare {spec.id}:{run_ws.root.parent.name}"),
          run_ledger(run_ws.root, run=f"{spec.id}:{run_ws.root.parent.name}")):
        rec = deps.run_track(spec, run_ws, resume)
    res.gen_cost_usd = rec.total_usage.cost_usd
    res.tool_calls = rec.total_usage.tool_calls
    res.harness_status, res.harness_rounds, res.harness_loop_score = rec.status.value, len(rec.rounds), rec.final_score
    res.harness_stop_reason = str(rec.extra.get("stop_reason") or "")
    res.harness_aborted_rounds = len(rec.extra.get("aborted_rounds") or [])
    if not (run_ws.root / entry_of(spec)).is_file():
        res.error = f"harness run produced no {entry_of(spec)} (status {rec.status.value}: {rec.error})"
        return
    # the whole src/ tree: agents may split helpers into src/parts/*.py (the build wrapper puts src/ on sys.path)
    shutil.copytree(run_ws.src, eval_ws.src, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _new_cell(item: BenchPrompt, arm: Arm, out: Path, opts: CompareOptions) -> tuple[Path, CellResult]:
    """The cell's directory and its identity row.  ONE place knows what identifies a row,
    so run_matrix's synthesized last-resort row is shaped like every real one."""
    cell = out / "cells" / item.id / arm.slug
    return cell, CellResult(prompt_id=item.id, tier=item.tier, arm=arm.raw, kind=arm.kind,
                            target=arm.target, judge=opts.judge, workspace=str(cell))


def run_cell(battery: Battery, item: BenchPrompt, arm: Arm, out: Path, opts: CompareOptions, deps: CompareDeps) -> CellResult:
    cell, res = _new_cell(item, arm, out, opts)
    cell.mkdir(parents=True, exist_ok=True)
    spec = spec_for(battery, item, arm, opts)
    t0 = time.time()
    eval_ws = _fresh_ws(cell / "eval")
    eval_ws.write_json(eval_ws.spec_path, spec)
    try:
        # one ledger per cell: generation (one-shot arms) AND the fixed evaluation's
        # judge calls are priced into cells/<prompt>/<arm>/telemetry/cost.jsonl instead
        # of the per-process fallback log.  Context-local, so --parallel keeps cells apart.
        with run_ledger(cell, run=f"{item.id}:{arm.slug}"):
            if arm.kind == "harness":
                _run_harness(spec, cell, eval_ws, opts, deps, res)
            else:
                _generate_oneshot(arm, spec, cell, eval_ws, opts, deps, res)
            # A one-shot arm whose LAST attempt was lost to the provider has not finished
            # its protocol: `oneshot+repair` writes attempt 0's file before the repair call,
            # so when that call dies in a 503 storm the broken pre-repair code was being
            # evaluated and scored 0 — a hard zero for someone else's downtime, the exact
            # asymmetry tests/compare_bench/test_infra_failures.py exists to end.  Drop the
            # cell instead (infra_failed); --redo-status re-runs the lost attempt only.
            truncated = arm.kind != "harness" and res.error_is_infra
            if (eval_ws.root / entry_of(spec)).is_file() and not truncated:
                outcome = deps.evaluator.evaluate(eval_ws, spec)
                eval_ws.write_json(eval_ws.root / "eval.json", outcome)
                _fill_from_outcome(res, outcome)
            elif is_budget_exhaustion(res.error, rounds=res.harness_rounds):
                # nothing was ever built, so there is no score to average — but the arm
                # DID fail to deliver, so this still counts against its build rate
                res.status, res.score, res.passed, res.build_ok = "budget_exhausted", None, False, False
            elif res.error_is_infra or is_infra_failure(res.error):
                # a provider outage is not a capability result: drop the cell (score
                # None) instead of scoring the model 0 for someone else's downtime
                res.status, res.score, res.passed, res.build_ok = "infra_failed", None, None, False
            else:
                res.status, res.score, res.passed, res.build_ok = "no_code", 0.0, False, False
    except PlanningError as e:
        # the harness's OWN planner gave up (its plan failed validation twice): no
        # artifact, and nobody else's fault — a capability failure of the arm, scored 0
        # like a one-shot answer in the wrong format (compare_art_v2, 2026-08-25: 5 of 14
        # articulated prompts; dropping them as `error` hid a third of the harness's losses)
        res.status, res.score, res.passed, res.build_ok = "no_code", 0.0, False, False
        res.error = f"PlanningError: {e}"
    except Exception as e:  # noqa: BLE001 — one cell must never kill the matrix
        # is_infra_failure is total (bench/_infra.py): a classifier bug answers False and
        # logs, it does not raise out of this handler and take the matrix loop with it.
        res.status = "infra_failed" if is_infra_failure(e) else "error"  # same rule as the no-code path
        res.note(f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1200:]}")
    res.wall_s = round(time.time() - t0, 1)
    try:
        flag_degraded(res, opts)
        eval_ws.write_json(cell / "cell.json", res)
    except Exception as e:  # noqa: BLE001 — the row is the record of last resort
        log.exception("cell.json not written for %s", res.workspace)
        res.note(f"[cell.json not written: {type(e).__name__}: {e}]")
    return res


def flag_degraded(res: CellResult, opts: CompareOptions) -> None:
    """Mark a harness cell whose run WAITED instead of iterating (compare_v4, 2026-08-25).

    Under a day-long gemini-3.7-flash 503 storm 37 of 40 harness runs stopped on the
    45-minute wall-clock ceiling with 0–2 completed rounds while the 3 runs that met a
    calm window finished 2–4 rounds in 27–40 min and scored 0.92–0.95.  Those cells are
    real artifacts and stay scored, but a paired mean over them measures the weather, not
    the loop — so the cell says so.  Rule: the harness stopped for *budget* while money
    was left (i.e. the clock, not the dollars), completed at most ``degraded_max_rounds``
    rounds, and the cell ran at least ``degraded_min_wall_s``.  ``harness_aborted_rounds``
    separately counts runs the ceiling interrupted mid-round.
    """
    if res.kind != "harness" or res.status not in ("scored", "build_failed"):
        return
    if (res.harness_stop_reason == "budget" and res.harness_rounds <= opts.degraded_max_rounds
            and res.wall_s >= opts.degraded_min_wall_s):
        res.degraded = True
        res.degraded_reason = (f"ceiling stop after {res.harness_rounds} completed round(s) in {res.wall_s / 60:.0f} min "
                               f"having spent ${res.gen_cost_usd:.2f}")


def _fill_from_outcome(res: CellResult, o: EvalOutcome) -> None:
    res.build_ok = o.build.ok
    res.gate_errors = o.gate_errors
    res.tris = o.measurement.tri_count if o.measurement else None
    res.sheet = (o.renders.contact_sheet or "") if o.renders else ""
    res.glb = o.build.glb_path or ""
    if not o.build.ok:
        res.status, res.score, res.passed = "build_failed", 0.0, False
        res.note(f"{o.build.error_type}: {o.build.error_message[:400]}")
        return
    j = o.judgment
    if j is None or j.n_samples == 0:
        res.status, res.score, res.passed = "judge_error", None, None
        res.note((o.error or (j.summary if j else "no judgment"))[:400])
        return
    res.judge_cost_usd = j.usage.cost_usd
    res.score, res.passed, res.score_std, res.status = j.overall, j.passed, j.score_std, "scored"
    res.criteria = {k: round(v, 3) for k, v in j.scores.items()}


# ----------------------------------------------------------------------------- pairwise arena
def _renders_of(out: Path, r: CellResult) -> RenderSet | None:
    p = Path(r.workspace) / "eval" / "eval.json"
    if not p.is_file():
        return None
    o = EvalOutcome.model_validate(json.loads(p.read_text()))
    return o.renders if o.renders and o.renders.views else None


def run_pairwise(battery: Battery, cells: dict[tuple[str, str], CellResult], arms: Sequence[Arm], out: Path,
                 opts: CompareOptions, deps: CompareDeps) -> list[PairRow]:
    path = out / "pairwise.jsonl"
    done = {(p.prompt_id, p.arm_a, p.arm_b): p for p in load_jsonl(path, PairRow)} if opts.resume else {}
    harness = [a for a in arms if a.kind == "harness"]
    oneshot = [a for a in arms if a.kind != "harness"]
    judge = None
    seal_for_append(path)  # a kill left the last row unterminated; do not glue onto it
    with path.open("a") as fh:
        for item in battery.prompts:
            for ha in harness:
                for oa in oneshot:
                    key = (item.id, ha.raw, oa.raw)
                    ca, cb = cells.get((item.id, ha.raw)), cells.get((item.id, oa.raw))
                    if key in done or ca is None or cb is None:
                        continue
                    spec = spec_for(battery, item, ha, opts)
                    ra, rb = _renders_of(out, ca), _renders_of(out, cb)
                    if ra is None or rb is None:
                        winner = "tie" if ra is None and rb is None else ("a" if rb is None else "b")
                        row = PairRow(prompt_id=item.id, arm_a=ha.raw, arm_b=oa.raw, winner=winner, confidence=1.0,
                                      reasons=["decided without the judge: a side has no renders (build failed)"], judged=False)
                    else:
                        judge = judge or deps.pairwise_judge(opts.judge)
                        pr = judge.compare(spec, ra, rb, rubric=RUBRIC)
                        row = PairRow(prompt_id=item.id, arm_a=ha.raw, arm_b=oa.raw, winner=pr.winner, confidence=pr.confidence,
                                      reasons=list(pr.reasons), orderings=list(pr.orderings), cost_usd=pr.usage.cost_usd,
                                      error=pr.error, judged=True)
                    done[key] = row
                    fh.write(row.model_dump_json() + "\n")
                    fh.flush()
    return list(done.values())


# ----------------------------------------------------------------------------- matrix
def _drop_pairs(path: Path, key: tuple[str, str]) -> None:
    """Forget pairwise rows touching a cell that is about to be re-run."""
    rows = [p for p in load_jsonl(path, PairRow) if not (p.prompt_id == key[0] and key[1] in (p.arm_a, p.arm_b))]
    if path.is_file():
        path.write_text("".join(p.model_dump_json() + "\n" for p in rows))


def run_matrix(battery_path: Path | str, out_dir: Path | str, arms: Sequence[Arm], opts: CompareOptions,
               deps: CompareDeps, *, on_result: Callable[[CellResult], None] | None = None) -> list[CellResult]:
    battery = Battery.load(battery_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "matrix.json").write_text(json.dumps({"battery": battery.model_dump(mode="json"), "arms": [a.raw for a in arms],
                                                 "options": opts.model_dump(mode="json"),
                                                 "started_at": datetime.now(UTC).isoformat()}, indent=2))
    results = out / "results.jsonl"
    done = {(r.prompt_id, r.arm): r for r in load_jsonl(results, CellResult)} if opts.resume else {}
    for key in [k for k, r in done.items() if r.status in set(opts.redo_status)]:
        _drop_pairs(out / "pairwise.jsonl", key)
        del done[key]
    selected = select_prompts(battery, ids=opts.ids, tiers=opts.tiers, limit=opts.limit)
    todo = [(p, a) for a in sorted(arms, key=lambda a: a.kind == "harness")  # cheap one-shots first
            for p in selected if (p.id, a.raw) not in done]
    seal_for_append(results)  # a kill left the last row unterminated; do not glue onto it
    with ThreadPoolExecutor(max_workers=max(1, opts.parallel)) as pool, results.open("a") as fh:
        futs = {pool.submit(run_cell, battery, p, a, out, opts, deps): (p, a) for p, a in todo}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001 — run_cell must not raise; if it does, record the cell, keep the matrix
                p, a = futs[fut]
                log.exception("run_cell raised for %s / %s", p.id, a.raw)
                _, r = _new_cell(p, a, out, opts)
                r.status = "error"
                r.note(f"run_cell raised {type(e).__name__}: {e}"[:800])
            done[(r.prompt_id, r.arm)] = r
            fh.write(r.model_dump_json() + "\n")
            fh.flush()
            if on_result:
                on_result(r)
    if opts.pairwise:
        # the arena is battery-level spend: its own ledger at <out>/telemetry/cost.jsonl
        with run_ledger(out, run=f"{battery.name}:pairwise"):
            run_pairwise(battery, done, arms, out, opts, deps)
    build_compare_report(out)
    return list(done.values())


# ----------------------------------------------------------------------------- CLI
def _preflight(judge: str, arms: Sequence[Arm], opts: CompareOptions, *, wait_minutes: float = 0.0) -> bool:
    """Refuse to start a battery against a model that is not serving.

    A dead provider does not fail fast on its own: every cell burns its full retry
    budget first.  On 2026-08-24 that cost ~9 hours of wall clock and a contaminated
    battery.  One 20-second probe per model is the whole cure.

    Every model a HARNESS arm needs counts, not just the generator: the same outage
    took down `harness:codex:gpt-5.6-sol` — whose generator is a local subscription
    CLI and whose judge was healthy — because the default PLANNER is flash.  A
    harness arm is only as available as the weakest model in its loop.
    """
    from codeverse.models.health import probe

    def api_model(target: str) -> str:
        """An API-billed oneshot target ('gemini:x') as-is; a subscription CLI target -> ''."""
        return target if target.startswith(("gemini:", "anthropic:", "openai:")) else ""

    models = {judge, *(api_model(a.target) for a in arms)}
    if any(a.kind.startswith("harness") for a in arms):
        # the harness loop runs a planner and its own in-loop judge on EVERY arm,
        # whatever the generator is
        backends = get_settings().backends(judge=opts.loop_judge, planner=opts.planner)
        models |= {backends.planner, backends.judge}
    models.discard("")
    deadline = time.time() + wait_minutes * 60
    while True:
        sick = [h for h in (probe(m) for m in sorted(models)) if not h.ok]
        if not sick:
            return True
        for h in sick:
            print(f"preflight: {h}", flush=True)
        if time.time() >= deadline:
            print("\nrefusing to start: the cells would spend their whole retry budget losing to this.\n"
                  "  wait for it:   --wait-for-provider 60\n"
                  "  start anyway:  --no-preflight", flush=True)
            return False
        print(f"preflight: parking 120 s (up to {wait_minutes:.0f} min) for the provider…", flush=True)
        time.sleep(120)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--arms", required=True, help="comma-separated arm ids")
    ap.add_argument("--judge", default="gemini:gemini-3.1-pro-preview", help="FIXED judge model id")
    ap.add_argument("--out", required=True)
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ids", default="", help="comma-separated prompt ids")
    ap.add_argument("--tiers", default="", help="comma-separated tiers (easy,medium,hard)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--max-minutes", type=float, default=45.0, help="wall-clock ceiling for ONE harness run")
    ap.add_argument("--loop-judge", default=None, help="harness in-loop judge (default: settings default)")
    ap.add_argument("--repair-attempts", type=int, default=2)
    ap.add_argument("--gen-timeout", type=float, default=900.0)
    ap.add_argument("--judge-samples", type=int, default=2,
                    help="FIXED judge samples per cell (default 2 — kept for resumable batteries; use an odd "
                         "n for a true majority on the defect checklist, see docs/DECISIONS.md D36)")
    ap.add_argument("--no-pairwise", action="store_true")
    ap.add_argument("--no-preflight", action="store_true",
                    help="skip the provider health check (see --wait-for-provider)")
    ap.add_argument("--wait-for-provider", type=float, default=0.0, metavar="MIN",
                    help="if the preflight fails, park up to MIN minutes for the provider to recover "
                         "instead of refusing to start")
    ap.add_argument("--redo-status", default="", help="comma list of statuses to re-run "
                    "(error,no_code,build_failed,judge_error,infra_failed); "
                    "recorded one-shot answers are re-used, not re-generated")
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--report-only", action="store_true", help="only rebuild report.md/html from results")
    ns = ap.parse_args(argv)
    if ns.report_only:
        build_compare_report(Path(ns.out))
        return 0
    opts = CompareOptions(judge=ns.judge, loop_judge=ns.loop_judge, rounds=ns.rounds,
                          max_minutes=ns.max_minutes, parallel=ns.parallel,
                          limit=ns.limit, ids=[i for i in ns.ids.split(",") if i],
                          tiers=[t for t in ns.tiers.split(",") if t], resume=not ns.no_resume,
                          gen_timeout_s=ns.gen_timeout, repair_attempts=ns.repair_attempts, pairwise=not ns.no_pairwise,
                          n_samples=max(1, ns.judge_samples),
                          redo_status=[x for x in ns.redo_status.split(",") if x])
    arms = parse_arms(ns.arms)
    if not ns.no_preflight and not _preflight(opts.judge, arms, opts, wait_minutes=ns.wait_for_provider):
        return 2
    battery = Battery.load(ns.prompts)  # the fixed evaluator follows the battery's track / language
    deps = CompareDeps(FixedEvaluator(opts.judge, n_samples=opts.n_samples, track=battery.track, language=battery.language))

    def _log(r: CellResult) -> None:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {r.prompt_id:28s} {r.arm:44s} score={r.score} build_ok={r.build_ok} "
              f"${r.gen_cost_usd:.2f} {r.wall_s / 60:.1f}min {r.status} {r.error[:80]!r}", flush=True)

    run_matrix(ns.prompts, ns.out, arms, opts, deps, on_result=_log)
    print(f"report: {Path(ns.out) / 'report.md'}")
    dropped = [r for r in load_jsonl(Path(ns.out) / "results.jsonl", CellResult) if r.status == "infra_failed"]
    if dropped:
        # never let downtime pass as a result: say what was lost and how to get it back
        print(f"\n{len(dropped)} cell(s) lost to provider outages and EXCLUDED from every rate: "
              + ", ".join(sorted({f"{r.prompt_id}/{r.arm}" for r in dropped})[:6])
              + (" ..." if len(dropped) > 6 else "")
              + "\nre-run them once the provider recovers with:  --redo-status infra_failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
