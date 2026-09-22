"""One round = generate → build(+repair) → gates → measure → render → judge → commit.

``run_round`` is track-agnostic; a ``RoundPipeline`` supplies the track-specific
gates / measurement / rendering.  Every step writes its artifact under the
workspace (gates/rNN, renders/rNN, judge/rNN.json, rounds/rNN.json, and the build
outputs a hand-over needs under artifacts/rNN/) so a run can be resumed, the flywheel
can replay it, and any round can be packaged later without a rebuild.

Two cost rules live here (docs/COST.md §5, §6):

* **never pay for a verdict you will not use** — :func:`skip_judge_reason` drops
  the judge only where the verdict is provably never bought at all, and
  :func:`rejudge_round` re-buys one only after the judge was TRIED and failed, so
  a skip is a saving and not a deferral.  Two wave-2 branches were removed for
  failing exactly that test (docs/COST.md §17).
* **a round's cost is its ledger rows** — the round runs inside a ``cost.tally``, so
  ``RoundRecord.usage`` is exactly what ``telemetry/cost.jsonl`` booked while it ran, and a
  round that raises half-way still reports it (``rounds/aborted_rNN.json``).  Each step it
  runs is timed into ``RoundRecord.steps`` (docs/COST.md §31).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateReport,
    Judgment,
    Measurement,
    RenderSet,
)
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.plan import AcceptanceItem, Plan
from codeverse3d.contracts.run import RoundRecord, StepTime
from codeverse3d.cost.tally import Tally, tally, timed
from codeverse3d.judges.base import round_input
from codeverse3d.judges.rubrics import is_degraded
from codeverse3d.orchestrator import BudgetExceeded
from codeverse3d.proc import fan_out
from codeverse3d.record.deliverable import keep_round_artifacts
from codeverse3d.spatial.render import RenderError
from codeverse3d.tracks import skills_hook
from codeverse3d.tracks.common import RunContext, generate_for
from codeverse3d.tracks.generation import GenerationResult, GenerationTask
from codeverse3d.tracks.repair import RepairOutcome, build_with_repair
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


class RoundPipeline(Protocol):
    """Track-specific steps after a successful build."""

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None: ...
    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]: ...
    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet: ...
    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        """Track-specific judge context.  Deliberately ``RunContext``-free so
        ``3dcode judge`` can rebuild the in-run context from stored artifacts."""
        ...


#: ``(ctx, round_index, build, measurement) → renders`` — ``RoundPipeline.render`` or a
#: cheaper stand-in (best-of-N candidates rank on ``candidates.quick_render``)
RenderFn = Callable[[RunContext, int, BuildResult, Measurement | None], RenderSet | None]


class RoundFailed(RuntimeError):
    """Every generation task of a round failed — nothing to build.

    Typed, so the round loop never reads the message: ``transient`` — a task died of a
    provider failure (a vendor-CLI 503 storm, a dropped socket, a ``ModelError`` outage), so
    the round is re-run once before the failure may mean anything; ``quota`` — the vendor's
    usage limit is spent, so the run stops as ``agent_quota``.  Neither — the agents ran
    and changed nothing: a ``no_change`` stop.  The flags come from each backend's own
    classification (``AgentResult``) through ``GenerationResult``; until 2026-09-22 the loop
    substring-matched the joined notes, with a vocabulary that had drifted from the backends'
    (a codex usage limit reached the loop only because the loop's list said "usage limit")."""

    def __init__(self, message: str, *, transient: bool = False, quota: bool = False) -> None:
        super().__init__(message)
        self.transient = transient
        self.quota = quota

    @classmethod
    def of(cls, results: Sequence[GenerationResult], default: str) -> RoundFailed:
        """The round's failure: every task's notes, and the typed flags of any of them."""
        return cls("; ".join(f"{r.label}: {r.notes}" for r in results) or default,
                   transient=any(r.transient for r in results), quota=any(r.quota for r in results))


def round_record_path(ctx: RunContext, index: int) -> Path:
    return ctx.ws.root / "rounds" / f"r{index:02d}.json"


def load_round_journal(ws: Workspace) -> list[RoundRecord]:
    """The on-disk round journal: ``rounds/r*.json`` sorted, contiguous prefix only.

    Workspace-level (no ``RunContext``) because ``lifecycle.reconcile_resume``
    reads it before the context exists."""
    d = ws.root / "rounds"
    if not d.is_dir():
        return []
    out: list[RoundRecord] = []
    for i, p in enumerate(sorted(d.glob("r*.json"))):
        rec = RoundRecord.model_validate(ws.read_json(p))
        if rec.index != i:
            break
        out.append(rec)
    return out


def run_generation_tasks(ctx: RunContext, tasks: Sequence[GenerationTask], *,
                         steps: list[StepTime] | None = None) -> list[GenerationResult]:
    """Run tasks (parallel when > 1).  Raises ``RoundFailed`` when none succeeded.

    ``GenerationTask.phase`` sequences the round: tasks run in parallel WITHIN a
    phase, phases in ascending order.  Every task is phase 0 unless a track says
    otherwise, so this is a no-op for every existing caller; per-part scoped
    generation uses it so the assembly session sees the part files first.  Each
    phase is one ``generate`` step in ``steps``.
    """
    if not tasks:
        return []
    phases = sorted({t.phase for t in tasks})
    if len(phases) > 1:
        out: dict[int, GenerationResult] = {}
        ok_any = False
        for ph in phases:
            picked = [(i, t) for i, t in enumerate(tasks) if t.phase == ph]
            try:
                got = _run_phase(ctx, [t for _, t in picked], steps)
            except RoundFailed as e:
                # a phase in which nothing succeeded is not automatically a dead round:
                # the earlier phases may have written the parts.  Record the failures
                # (and why they failed) and let the final check below decide.
                got = [GenerationResult(ok=False, notes="phase produced no change", label=t.label,
                                        transient=e.transient, quota=e.quota) for _, t in picked]
            for (i, _), r in zip(picked, got, strict=True):
                out[i] = r
            ok_any = ok_any or any(r.ok for r in got)
        results_seq = [out[i] for i in range(len(tasks))]
        if not ok_any:
            raise RoundFailed.of(results_seq, "no generation task succeeded")
        return results_seq
    return _run_phase(ctx, list(tasks), steps)


def _run_phase(ctx: RunContext, tasks: Sequence[GenerationTask],
               steps: list[StepTime] | None = None) -> list[GenerationResult]:
    """One parallel batch of generation tasks — one ``generate`` step: its sessions run side
    by side, so they share one clock."""

    def _one(task: GenerationTask) -> GenerationResult:
        return generate_for(ctx, task)

    # fan_out also for ONE task: a crashing generator (503 storm, parse error) becomes a failed
    # result → RoundFailed (a refine round then stops the run as no_change) instead of killing it.
    with timed("generate", [] if steps is None else steps, round_index=tasks[0].round if tasks else None):
        results = fan_out(list(tasks), _one, max_workers=ctx.settings.limits.max_parallel_agents,
                          label="generate", item_name=lambda t: t.label)
    out: list[GenerationResult] = []
    budget_stop: Exception | None = None
    for task, r in zip(tasks, results, strict=True):
        if isinstance(r, Exception):
            if isinstance(r, BudgetExceeded) and budget_stop is None:
                budget_stop = r  # a failed item; the siblings' paid results still land in ``out``
            ctx.events.emit("generate.failed", label=task.label, error=f"{type(r).__name__}: {r}")
            out.append(GenerationResult.from_error(task.label, r))
        else:
            out.append(r)
    if not any(r.ok for r in out):
        if budget_stop is not None:
            raise budget_stop  # nothing usable survived the phase
        raise RoundFailed.of(out, "no generation task succeeded")
    ctx.budget.check()  # phase boundary: single-shot money is booked non-enforcing upstream
    return out


def run_round(
    ctx: RunContext,
    *,
    index: int,
    kind: str,
    tasks: Sequence[GenerationTask],
    pipeline: RoundPipeline,
    instructions: Sequence[str] = (),
    previous: Judgment | None = None,
    files_hint: Sequence[str] = (),
    extra_usage: Usage | None = None,
    extra_notes: Sequence[str] = (),
) -> RoundRecord:
    """Execute one round and persist its record.  Budget is charged as it goes.

    ``tasks`` may be empty when the code is already in place (scene stages,
    best-of-N winner copied in): the round is then build → gates → render →
    judge only.  ``extra_usage`` / ``extra_notes`` fold pre-round work
    (candidate generation) into the record.
    ``_run_round`` (the same round without the aborted-round record) also takes
    ``render`` (replaces ``pipeline.render``) and ``geometry_views=False`` (skips the
    clay views): the two knobs a best-of-N candidate turns (``candidates.run_best_of_n``
    calls it directly for each candidate in its sub-workspace)."""
    steps: list[StepTime] = []
    with tally() as spent:
        try:
            return _run_round(ctx, index=index, kind=kind, tasks=tasks, pipeline=pipeline, instructions=instructions,
                              previous=previous, files_hint=files_hint, extra_usage=extra_usage,
                              extra_notes=extra_notes, steps=steps, spent=spent)
        except BaseException as e:
            # the round died half-way (budget stop, 503 storm, RoundFailed): what it burned
            # is in its tally — the ledger rows booked while it ran — so it is not invisible
            record_aborted_round(ctx, index=index, kind=kind, usage=spent.usage,
                                 error=f"{type(e).__name__}: {e}", steps=steps)
            raise


def _run_round(
    ctx: RunContext,
    *,
    index: int,
    kind: str,
    tasks: Sequence[GenerationTask],
    pipeline: RoundPipeline,
    instructions: Sequence[str] = (),
    previous: Judgment | None = None,
    files_hint: Sequence[str] = (),
    extra_usage: Usage | None = None,
    extra_notes: Sequence[str] = (),
    render: RenderFn | None = None,
    geometry_views: bool = True,
    steps: list[StepTime] | None = None,
    spent: Tally,
) -> RoundRecord:
    """The round's body.  ``spent`` is the tally the caller opened around it: the round's
    cost is what it booked (+ ``extra_usage``, work paid before the round began)."""
    t0 = time.time()
    steps = [] if steps is None else steps
    ctx.events.emit("round.start", round=index, kind=kind, n_tasks=len(tasks))
    rec = RoundRecord(index=index, kind=kind, agent_backend=ctx.agent_id, instructions=list(instructions),
                      started_at=datetime.now(UTC))
    notes: list[str] = list(extra_notes)

    # a candidate IS a baseline written in a sub-workspace: route its skills as baseline work,
    # or every kind-gated bundle (the *-forms / *-joints routes) drops out and the winner is
    # generated with a strictly smaller library than a --candidates 1 baseline (review 2026-08-29)
    skill_kind = "baseline" if kind == "candidate" else kind
    skills_hook.attach_for_round(ctx, index=index, kind=skill_kind)
    gens = run_generation_tasks(ctx, skills_hook.with_inlined_skill(ctx, tasks), steps=steps)
    notes += [f"{g.label}: {g.notes}" for g in gens if not g.ok]
    ctx.ws.commit(f"r{index:02d} {kind}: generated")

    with timed("build", steps, round_index=index):
        outcome: RepairOutcome = build_with_repair(ctx, round_index=index, label=f"r{index:02d}_{kind}",
                                                   files_hint=list(files_hint))
    rec.build = outcome.build
    gates: list[GateReport] = [outcome.lint]
    if outcome.attempts:
        notes.append(
            f"repair attempts: {len(outcome.attempts)}/{outcome.max_attempts} "
            f"({'fixed' if outcome.ok else 'still failing'})"
        )

    if outcome.build.ok:
        with timed("gates", steps, round_index=index):
            rec.measurement = pipeline.measure(ctx, outcome.build)
            gates.extend(pipeline.gates(ctx, index, outcome.build, rec.measurement))
        try:
            with timed("render", steps, round_index=index):
                rec.renders = (render or pipeline.render)(ctx, index, outcome.build, rec.measurement)
        except RenderError as e:
            # A render that times out (measured 2026-08-26, art_verify camera_tripod: the
            # refine round built in 1.2 s, then render_glb.mjs hit its 330 s timeout with
            # three articulated runs and two readouts sharing the browser) used to raise
            # out of the round and record the whole run `failed` -- with a judged round 0
            # already on disk.  The round is kept: build, measurement and gates stand and
            # the judge is skipped (no renders), so a pick passes over it.
            rec.renders = None
            notes.append(f"render failed: {str(e)[:200]}")
            ctx.events.emit("render.failed", round=index, error=str(e)[:400])
        post = getattr(pipeline, "post_render_gates", None)
        if callable(post) and rec.renders is not None:
            with timed("gates", steps, round_index=index):
                gates.extend(post(ctx, index, rec.renders))
        # the build's own reports (scene_probe + shader_preflight, gl_frames) after the
        # track's: the judge's warning list is capped and reads them in this order
        gates.extend(outcome.build.gates)
        n_err = sum(len(g.errors) for g in gates)
        ctx.events.emit("gates.done", round=index, n_gates=len(gates), n_errors=n_err,
                        tri_count=rec.measurement.tri_count if rec.measurement else None)
        skip = skip_judge_reason(ctx, renders=rec.renders)
        if skip:
            notes.append(f"judge skipped ({skip})")
            ctx.events.emit("judge.skipped", round=index, reason=skip)
        else:
            with timed("judge", steps, round_index=index):
                rec.judgment = _judge(ctx, pipeline, index, outcome.build, gates, rec, previous, notes,
                                      geometry_views=geometry_views)
    else:
        # a failed build's own reports say why (a shader error lives in shader_preflight)
        gates.extend(outcome.build.gates)
        notes.append(f"build failed: {outcome.build.error_type}: {outcome.build.error_message[:200]}")
    rec.gates = gates
    rec.skills = skills_hook.record_usage(ctx, index=index, kind=skill_kind)
    _write_gate_reports(ctx, index, gates)

    rec.commit = ctx.ws.commit(f"r{index:02d} {kind}")
    if outcome.build.ok:
        # every round keeps what a hand-over of it needs: the next build replaces artifacts/
        keep_round_artifacts(ctx.ws, index)
    rec.usage = spent.usage + (extra_usage or Usage())
    rec.duration_s = round(time.time() - t0, 2)
    rec.steps = steps
    rec.notes = "; ".join(notes)
    ctx.ws.write_json(round_record_path(ctx, index), rec)
    ctx.events.emit("round.done", round=index, kind=kind, commit=rec.commit[:10], score=rec.score,
                    build_ok=outcome.build.ok, duration_s=rec.duration_s, cost_usd=round(rec.usage.cost_usd, 4))
    return rec


# ----------------------------------------------------------------------------- cost of a round
def skip_judge_reason(ctx: RunContext, *, renders: RenderSet | None) -> str:
    """Why this round must NOT be judged (``""`` = judge it).

    Only states in which the verdict is never bought at all — a skip that the
    loop buys back a moment later (``rejudge_round``) is not a saving, it is a
    ``judge.retry`` plus a lost score:

    * ``no judge configured`` / ``no renders`` — there is nothing to buy.

    A round that finished past the wall clock is still judged (2026-09-22): its verdict is
    what lets ``addons.select`` pick it, and the generation it scores is already paid for.
    A broken build never gets here (``run_round`` judges only when the build is
    ok) and a round in which nothing changed never gets here either
    (``run_generation_tasks`` raises :class:`RoundFailed` first)."""
    if ctx.judge is None:
        return "no judge configured"
    if renders is None or not renders.views:
        return "no renders"
    return ""


def record_aborted_round(ctx: RunContext, *, index: int, kind: str, usage: Usage, error: str,
                         steps: Sequence[StepTime] = ()) -> None:
    """A round that raised half-way still reports what it burned.

    Its record is written next to the round records as ``aborted_rNN.json`` (never
    ``rNN.json``: the round did not happen, and ``load_round_journal`` must not
    resume from it), its cost is remembered in ``ctx.extra`` so the run record can carry
    it, and the steps it did run join the run-level steps (``RunState.steps``): their
    minutes were spent.  Accounting a stop must never raise on top of the stop that is
    already happening."""
    try:
        ctx.state.steps.extend(steps)
        rec = RoundRecord(index=index, kind=kind, agent_backend=ctx.agent_id, usage=usage, steps=list(steps),
                          notes=f"aborted: {error}")
        path = ctx.ws.root / "rounds" / f"aborted_r{index:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        ctx.ws.write_json(path, rec)
        aborted = ctx.extra.setdefault("aborted_rounds", [])
        aborted.append({"index": index, "kind": kind, "cost_usd": round(usage.cost_usd, 6), "error": error[:300]})
    except Exception as e:  # noqa: BLE001 — never mask the exception that stopped the round
        log.warning("could not record aborted round r%02d: %s", index, e)


def _judge(ctx: RunContext, pipeline: RoundPipeline, index: int, build: BuildResult, gates: list[GateReport],
           rec: RoundRecord, previous: Judgment | None, notes: list[str], *,
           geometry_views: bool = True) -> Judgment | None:
    """Judge one round; ``None`` when the verdict is unusable (the judge crashed or returned
    a degraded non-score).  What the attempt cost reaches the round either way: its calls
    are ledger rows, booked into the round's tally.  Failure notes are appended to the
    CALLER's ``notes`` list — ``_run_round`` joins that list into ``rec.notes`` at the end
    of the round, so writing to ``rec.notes`` here was silently lost."""
    renders = rec.renders
    jv = getattr(pipeline, "judge_views", None)
    if callable(jv) and renders is not None:
        try:
            renders = jv(ctx, renders) or renders
        except Exception as e:  # noqa: BLE001 — view selection is an optimisation, never a blocker
            log.warning("judge_views selection failed: %s", e)
            renders = rec.renders
    geometry = None
    gv = getattr(pipeline, "geometry_views", None) if geometry_views else None
    if callable(gv):
        try:
            geometry = gv(ctx, index, build)
        except Exception as e:  # noqa: BLE001 — clay views are optional judge context
            log.warning("geometry views failed in round %d: %s", index, e)
            ctx.events.emit("judge.geometry_views_failed", round=index, error=f"{type(e).__name__}: {e}")
    inp = round_input(ctx.spec, ctx.plan, rec, renders=renders, gates=gates, previous=previous,
                      extra_context=pipeline.judge_context(ctx.ws, ctx.plan, index, build, gates),
                      geometry_views=geometry, glb_path=build.glb_path)
    t0 = time.time()
    try:
        judgment: Judgment = ctx.judge.judge(inp)
    except Exception as e:  # noqa: BLE001 — a judge outage must not destroy the run's code/commits
        log.exception("judge failed in round %d", index)
        paid = getattr(e, "usage", None)
        ctx.events.emit("judge.failed", round=index, error=f"{type(e).__name__}: {e}",
                        cost_usd=round(paid.cost_usd, 4) if isinstance(paid, Usage) else 0.0)
        notes.append(f"judge failed: {type(e).__name__}: {e}")
        return None
    ctx.ws.write_json(ctx.ws.judge_path(index), judgment)
    if is_degraded(judgment):
        # a glitch, never a score: keep the raw verdict on disk, but do not let 0.0
        # poison a pick or a refine plan (judges/rubrics.degraded_judgment contract)
        ctx.events.emit("judge.degraded", round=index, error=judgment.summary[:300],
                        cost_usd=round(judgment.usage.cost_usd, 4))
        notes.append(f"judge degraded: {judgment.summary[:200]}")
        return None
    ctx.events.emit("judge.done", round=index, overall=round(judgment.overall, 3), passed=judgment.passed,
                    n_issues=len(judgment.issues), n_plan=len(judgment.improvement_plan),
                    duration_s=round(time.time() - t0, 1), cost_usd=round(judgment.usage.cost_usd, 4))
    return judgment


def rejudge_round(ctx: RunContext, pipeline: RoundPipeline, rec: RoundRecord, previous: Judgment | None = None) -> bool:
    """Re-judge an already built+rendered round whose judgment FAILED or was degraded.

    No regeneration, no rebuild: the same commit is judged again.  On success the
    round record is updated in place and re-persisted.  Returns True when the
    round now has a usable judgment."""
    if ctx.judge is None or rec.build is None or not rec.build.ok or rec.renders is None or not rec.renders.views:
        return False
    ctx.events.emit("judge.retry", round=rec.index)
    notes: list[str] = [rec.notes] if rec.notes else []
    # a round recorded before step timing keeps counting its own clock (RoundRecord.minutes)
    with timed("judge", rec.steps if rec.steps is not None else [], round_index=rec.index) as spent:
        judgment = _judge(ctx, pipeline, rec.index, rec.build, list(rec.gates), rec, previous, notes)
    # paid either way: a retry that failed AGAIN keeps its money and its note on the record
    rec.usage = rec.usage + spent.usage
    if judgment is not None:
        rec.judgment = judgment
    rec.notes = "; ".join(notes)
    ctx.ws.write_json(round_record_path(ctx, rec.index), rec)
    return judgment is not None


def _write_gate_reports(ctx: RunContext, index: int, gates: list[GateReport]) -> None:
    d = ctx.ws.gates_dir(index)
    d.mkdir(parents=True, exist_ok=True)
    for g in gates:
        name = g.gate.replace(":", "_").replace("/", "_")
        ctx.ws.write_json(d / f"{name}.json", g)


def failed_acceptance(ctx: RunContext, judgment: Judgment | None) -> list[AcceptanceItem]:
    """Acceptance items the judge marked as not verified."""
    if judgment is None:
        return []
    items = {a.id: a for a in (getattr(ctx.plan, "acceptance", []) or [])}
    return [items[k] for k, ok in judgment.acceptance_results.items() if not ok and k in items]
