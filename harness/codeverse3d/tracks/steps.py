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
* **every round reports what it burned** — :func:`emit_round_cost` writes one
  ``cost.round`` event ({stage → $}, judge $, agent turns, wasted flag) at the
  end of the round *and* when the round raises half-way, so the ledger never has
  to reconstruct a round the budget cut.
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
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.judges.base import SLICE_TRACKS, JudgeInput
from codeverse3d.judges.rubrics import is_degraded
from codeverse3d.orchestrator import BudgetExceeded, usage_delta
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
    def plan_summary(self, ctx: RunContext) -> str: ...
    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        """Track-specific judge context.  Deliberately ``RunContext``-free so
        ``3dcode judge`` can rebuild the in-run context from stored artifacts."""
        ...


#: ``(ctx, round_index, build, measurement) → renders`` — ``RoundPipeline.render`` or a
#: cheaper stand-in (best-of-N candidates rank on ``candidates.quick_render``)
RenderFn = Callable[[RunContext, int, BuildResult, Measurement | None], RenderSet | None]


class RoundFailed(RuntimeError):
    """Every generation task of a round failed — nothing to build."""


#: error-text markers that mean a round DIED IN TRANSPORT (vendor CLI crash, 503
#: storm, dropped socket) rather than the agent declining the work.  They match
#: ``RoundFailed`` messages, which join ``GenerationResult.notes`` as composed by
#: ``agents/backends.py`` (``rc=N; response=<empty>; stderr tail: ...``) or by
#: ``_run_phase`` from a raised ``ModelError`` (``... timed out``, ``503``).
_TRANSPORT_MARKS = (
    "response=<empty>", "no result envelope", "503", "unavailable", "overloaded",
    "timed out", "etimedout", "econnreset", "socket hang up", "connection reset",
)


#: the vendor's own usage limit — the agent is gone until it resets, whatever we retry
_QUOTA_MARKS = (
    "hit your usage limit", "usage limit", "purchase more credits", "quota exceeded", "insufficient_quota",
    "insufficient credits", "billing hard limit",
)


def looks_quota(msg: str) -> bool:
    """True when a ``RoundFailed`` message says the VENDOR's usage limit is spent.

    Not a transport death (a retry meets the same wall) and not an agent verdict (the
    code did not stop improving): cmp8 (2026-09-09) filed three runs as ``plateau`` on
    "You've hit your usage limit … try again at Sep 14th"."""
    low = msg.lower()
    return any(m in low for m in _QUOTA_MARKS)


def looks_transport(msg: str) -> bool:
    """True when a ``RoundFailed`` message carries a transport-death signature.

    An agent that ran fine and chose to change nothing reports ``rc=0;
    response=ok`` or plain task notes — none of the markers.  A marker therefore
    separates "the session never really happened" (worth exactly one retry) from
    "the agent declined" (a plateau verdict to respect).
    """
    low = msg.lower()
    return any(m in low for m in _TRANSPORT_MARKS)


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


def run_generation_tasks(ctx: RunContext, tasks: Sequence[GenerationTask]) -> list[GenerationResult]:
    """Run tasks (parallel when > 1).  Raises ``RoundFailed`` when none succeeded.

    ``GenerationTask.phase`` sequences the round: tasks run in parallel WITHIN a
    phase, phases in ascending order.  Every task is phase 0 unless a track says
    otherwise, so this is a no-op for every existing caller; per-part scoped
    generation uses it so the assembly session sees the part files first.
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
                got = _run_phase(ctx, [t for _, t in picked])
            except RoundFailed:
                # a phase in which nothing succeeded is not automatically a dead round:
                # the earlier phases may have written the parts.  Record the failures
                # and let the final check below decide.
                got = [GenerationResult(ok=False, notes="phase produced no change", label=t.label)
                       for _, t in picked]
            for (i, _), r in zip(picked, got, strict=True):
                out[i] = r
            ok_any = ok_any or any(r.ok for r in got)
        results_seq = [out[i] for i in range(len(tasks))]
        if not ok_any:
            raise RoundFailed("; ".join(f"{r.label}: {r.notes}" for r in results_seq) or "no generation task succeeded")
        return results_seq
    return _run_phase(ctx, list(tasks))


def _run_phase(ctx: RunContext, tasks: Sequence[GenerationTask]) -> list[GenerationResult]:
    """One parallel batch of generation tasks (the pre-phase behaviour, unchanged)."""

    def _one(task: GenerationTask) -> GenerationResult:
        return generate_for(ctx, task)

    # fan_out also for ONE task: a crashing generator (503 storm, parse error) becomes a failed
    # result → RoundFailed (refine rounds treat that as a plateau) instead of killing the run.
    results = fan_out(list(tasks), _one, max_workers=ctx.settings.limits.max_parallel_agents,
                      label="generate", item_name=lambda t: t.label)
    out: list[GenerationResult] = []
    budget_stop: Exception | None = None
    for task, r in zip(tasks, results, strict=True):
        if isinstance(r, Exception):
            if isinstance(r, BudgetExceeded) and budget_stop is None:
                budget_stop = r  # a failed item; the siblings' paid results still land in ``out``
            ctx.events.emit("generate.failed", label=task.label, error=f"{type(r).__name__}: {r}")
            out.append(GenerationResult(ok=False, notes=f"{type(r).__name__}: {r}", label=task.label))
        else:
            out.append(r)
    if not any(r.ok for r in out):
        if budget_stop is not None:
            raise budget_stop  # nothing usable survived the phase
        raise RoundFailed("; ".join(f"{r.label}: {r.notes}" for r in out) or "no generation task succeeded")
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
    mark = ctx.budget.mark()
    try:
        return _run_round(ctx, index=index, kind=kind, tasks=tasks, pipeline=pipeline, instructions=instructions,
                          previous=previous, files_hint=files_hint, extra_usage=extra_usage,
                          extra_notes=extra_notes)
    except BaseException as e:
        # the round died half-way (budget stop, 503 storm, RoundFailed).  Whatever it
        # burned is already in the guard: report it so the round is not invisible.
        burned = usage_delta(ctx.budget.spent, mark)
        record_aborted_round(ctx, index=index, kind=kind, usage=burned, error=f"{type(e).__name__}: {e}")
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
) -> RoundRecord:
    t0 = time.time()
    ctx.events.emit("round.start", round=index, kind=kind, n_tasks=len(tasks))
    rec = RoundRecord(index=index, kind=kind, agent_backend=ctx.agent_id, instructions=list(instructions),
                      started_at=datetime.now(UTC))
    usage = extra_usage or Usage()
    notes: list[str] = list(extra_notes)
    cost: dict[str, float] = {}
    if extra_usage is not None and extra_usage.cost_usd:
        cost["candidates"] = round(extra_usage.cost_usd, 6)

    # a candidate IS a baseline written in a sub-workspace: route its skills as baseline work,
    # or every kind-gated bundle (the *-forms / *-joints routes) drops out and the winner is
    # generated with a strictly smaller library than a --candidates 1 baseline (review 2026-08-29)
    skill_kind = "baseline" if kind == "candidate" else kind
    skills_hook.attach_for_round(ctx, index=index, kind=skill_kind)
    gens = run_generation_tasks(ctx, skills_hook.with_inlined_skill(ctx, tasks))
    turns = 0
    for g in gens:
        usage = usage + g.usage
        turns += g.turns
        if not g.ok:
            notes.append(f"{g.label}: {g.notes}")
    if gens:
        cost["generate"] = round(sum(g.usage.cost_usd for g in gens), 6)
    ctx.ws.commit(f"r{index:02d} {kind}: generated")

    outcome: RepairOutcome = build_with_repair(ctx, round_index=index, label=f"r{index:02d}_{kind}",
                                               files_hint=list(files_hint))
    usage = usage + outcome.usage
    if outcome.usage.cost_usd:
        cost["repair"] = round(outcome.usage.cost_usd, 6)
    rec.build = outcome.build
    gates: list[GateReport] = [outcome.lint]
    if outcome.attempts:
        notes.append(
            f"repair attempts: {len(outcome.attempts)}/{outcome.max_attempts} "
            f"({'fixed' if outcome.ok else 'still failing'})"
        )

    if outcome.build.ok:
        rec.measurement = pipeline.measure(ctx, outcome.build)
        gates.extend(pipeline.gates(ctx, index, outcome.build, rec.measurement))
        try:
            rec.renders = (render or pipeline.render)(ctx, index, outcome.build, rec.measurement)
        except RenderError as e:
            # A render that times out (measured 2026-08-26, art_verify camera_tripod: the
            # refine round built in 1.2 s, then render_glb.mjs hit its 330 s timeout with
            # three articulated runs and two readouts sharing the browser) used to raise
            # out of the round and record the whole run `failed` -- with a judged round 0
            # already on disk.  The round is kept: build, measurement and gates stand, the
            # judge is skipped (no renders), and the loop delivers the best round so far.
            rec.renders = None
            notes.append(f"render failed: {str(e)[:200]}")
            ctx.events.emit("render.failed", round=index, error=str(e)[:400])
        post = getattr(pipeline, "post_render_gates", None)
        if callable(post) and rec.renders is not None:
            gates.extend(post(ctx, index, rec.renders))
        n_err = sum(len(g.errors) for g in gates)
        ctx.events.emit("gates.done", round=index, n_gates=len(gates), n_errors=n_err,
                        tri_count=rec.measurement.tri_count if rec.measurement else None)
        # a candidate's verdict is the ONLY thing that picks the code r00 starts from, so it is
        # bought even past the ceiling — unlike a refine verdict, which could promote nothing
        skip = skip_judge_reason(ctx, renders=rec.renders, ignore_budget=kind == "candidate")
        if skip:
            notes.append(f"judge skipped ({skip})")
            ctx.events.emit("judge.skipped", round=index, reason=skip)
        else:
            rec.judgment, judge_usage = _judge(ctx, pipeline, index, outcome.build, gates, rec, previous, notes,
                                               geometry_views=geometry_views)
            if rec.judgment is not None or judge_usage.cost_usd:
                # a degraded/crashed verdict was still PAID: fold its usage into the
                # round's usage and cost["judge"] so rec.usage and the cost.round event
                # see the money even when judged=False.
                usage = usage + judge_usage
                cost["judge"] = round(judge_usage.cost_usd, 6)
            if rec.judgment is not None:
                # add, never charge: the verdict exists and is paid for — raising here
                # would drop a fully judged round before it is committed/recorded
                # (the loop stops at its next budget_ok check instead, AFTER best promotion).
                ctx.budget.add(rec.judgment.usage, stage="judge")
    else:
        notes.append(f"build failed: {outcome.build.error_type}: {outcome.build.error_message[:200]}")
    rec.gates = gates
    rec.skills = skills_hook.record_usage(ctx, index=index, kind=skill_kind)
    _write_gate_reports(ctx, index, gates)

    rec.commit = ctx.ws.commit(f"r{index:02d} {kind}")
    if outcome.build.ok:
        # every round keeps what a hand-over of it needs: the next build replaces artifacts/
        keep_round_artifacts(ctx.ws, index)
    rec.usage = usage
    rec.duration_s = round(time.time() - t0, 2)
    rec.notes = "; ".join(notes)
    ctx.ws.write_json(round_record_path(ctx, index), rec)
    ctx.events.emit("round.done", round=index, kind=kind, commit=rec.commit[:10], score=rec.score,
                    build_ok=outcome.build.ok, duration_s=rec.duration_s, cost_usd=round(usage.cost_usd, 4))
    emit_round_cost(ctx, index=index, kind=kind, cost=cost, usage=usage, turns=turns, score=rec.score,
                    build_ok=outcome.build.ok, judged=rec.judgment is not None)
    return rec


# ----------------------------------------------------------------------------- cost of a round
def skip_judge_reason(ctx: RunContext, *, renders: RenderSet | None, ignore_budget: bool = False) -> str:
    """Why this round must NOT be judged (``""`` = judge it).

    Only states in which the verdict is never bought at all — a skip that the
    loop buys back a moment later (``rejudge_round``) is not a saving, it is a
    ``judge.retry`` plus a lost score:

    * ``no judge configured`` / ``no renders`` — there is nothing to buy.
    * ``budget already exceeded`` — the loop's next ``budget_ok`` check ends the
      run, so this verdict could not promote anything.  Measured: 2 verdicts /
      $0.09 in the audit were bought past the run's wall clock (docs/COST.md §5).
      ``ignore_budget`` exempts the best-of-N candidates: their verdict picks the
      code r00 starts from, so skipping it wastes the N generations already paid for.

    A broken build never gets here (``run_round`` judges only when the build is
    ok) and a round in which nothing changed never gets here either
    (``run_generation_tasks`` raises :class:`RoundFailed` first)."""
    if ctx.judge is None:
        return "no judge configured"
    if renders is None or not renders.views:
        return "no renders"
    if not ctx.budget.ok() and not ignore_budget:
        return "budget already exceeded"
    return ""


def emit_round_cost(
    ctx: RunContext,
    *,
    index: int,
    kind: str,
    cost: dict[str, float],
    usage: Usage,
    turns: int,
    score: float | None,
    build_ok: bool,
    judged: bool,
    aborted: str = "",
    corrected: bool = False,
) -> None:
    """One ``cost.round`` event per round: ``{stage → $}``, judge $, agent turns and
    whether the money bought a usable round.  The ledger/audit reads this instead of
    reconstructing the round from trajectories and events.  ``corrected=True``
    marks a re-emission after ``rejudge_round`` recovered a verdict — the LAST
    event per round is the truth."""
    wasted, why = _waste_flag(build_ok=build_ok, judged=judged and score is not None, aborted=aborted)
    ctx.events.emit(
        "cost.round", round=index, kind=kind,
        stages={k: round(v, 6) for k, v in cost.items()},
        judge_usd=round(cost.get("judge", 0.0), 6),
        total_usd=round(usage.cost_usd, 6),
        agent_turns=turns,
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, cached_tokens=usage.cached_tokens,
        score=score, wasted=wasted, waste_reason=why, aborted=aborted,
        corrected=corrected,
        run_usd=round(ctx.budget.spent.cost_usd, 6),
    )


def _waste_flag(*, build_ok: bool, judged: bool, aborted: str) -> tuple[bool, str]:
    """Did this round buy a usable, judged round?  (docs/COST.md §5 vocabulary.)  A round that
    scored below an earlier one is NOT waste any more (2026-09-22): every round is kept, and
    any of them can be the one handed over (``codeverse3d.addons.select``)."""
    if aborted:
        return True, "aborted"
    if not build_ok:
        return True, "build_failed"
    if not judged:
        return True, "unjudged"
    return False, ""


def record_aborted_round(ctx: RunContext, *, index: int, kind: str, usage: Usage, error: str) -> None:
    """A round that raised half-way still reports what it burned.

    Its record is written next to the round records as ``aborted_rNN.json`` (never
    ``rNN.json``: the round did not happen, and ``load_round_journal`` must not
    resume from it), a ``cost.round`` event is emitted, and the usage is remembered
    in ``ctx.extra`` so the run record can carry it.  Accounting a stop must never
    raise on top of the stop that is already happening."""
    try:
        rec = RoundRecord(index=index, kind=kind, agent_backend=ctx.agent_id, usage=usage,
                          notes=f"aborted: {error}")
        path = ctx.ws.root / "rounds" / f"aborted_r{index:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        ctx.ws.write_json(path, rec)
        aborted = ctx.extra.setdefault("aborted_rounds", [])
        aborted.append({"index": index, "kind": kind, "cost_usd": round(usage.cost_usd, 6), "error": error[:300]})
        emit_round_cost(ctx, index=index, kind=kind, cost={"aborted": round(usage.cost_usd, 6)}, usage=usage,
                        turns=0, score=None, build_ok=False, judged=False, aborted=error[:300])
    except Exception as e:  # noqa: BLE001 — never mask the exception that stopped the round
        log.warning("could not record aborted round r%02d: %s", index, e)


def _judge(ctx: RunContext, pipeline: RoundPipeline, index: int, build: BuildResult, gates: list[GateReport],
           rec: RoundRecord, previous: Judgment | None, notes: list[str], *,
           geometry_views: bool = True) -> tuple[Judgment | None, Usage]:
    """Judge one round → ``(judgment, paid_usage)``.

    ``judgment`` is ``None`` when the verdict is unusable (the judge crashed or
    returned a degraded non-score); ``paid_usage`` is what the attempt cost
    EITHER WAY, so a failed verdict's dollars still reach the round record and
    its ``cost.round`` event.  Failure notes are appended to the CALLER's
    ``notes`` list — ``_run_round`` joins that list into ``rec.notes`` at the
    end of the round, so writing to ``rec.notes`` here was silently lost."""
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
    inp = JudgeInput(
        spec=ctx.spec, renders=renders, measurement=rec.measurement, gates=gates,
        acceptance=list(getattr(ctx.plan, "acceptance", []) or []), plan_summary=pipeline.plan_summary(ctx),
        round_index=index, previous=previous, extra_context=pipeline.judge_context(ctx.ws, ctx.plan, index, build, gates),
        geometry_views=geometry,
        # D48: the round's canonical GLB feeds the conditional slice channel (object tracks only)
        glb_path=build.glb_path if ctx.spec.track.value in SLICE_TRACKS else None,
    )
    t0 = time.time()
    try:
        judgment: Judgment = ctx.judge.judge(inp)
    except Exception as e:  # noqa: BLE001 — a judge outage must not destroy the run's code/commits
        log.exception("judge failed in round %d", index)
        paid = getattr(e, "usage", None)
        paid = paid if isinstance(paid, Usage) else Usage()
        if paid.cost_usd:
            # the provider billed the failed attempt (ModelError.usage): book it
            ctx.budget.add(paid, stage="judge")
        ctx.events.emit("judge.failed", round=index, error=f"{type(e).__name__}: {e}",
                        cost_usd=round(paid.cost_usd, 4))
        notes.append(f"judge failed: {type(e).__name__}: {e}")
        return None, paid
    ctx.ws.write_json(ctx.ws.judge_path(index), judgment)
    if is_degraded(judgment):
        # a glitch, never a score: keep the raw verdict on disk, pay for it, but do not
        # let 0.0 poison plateau/best/refine (judges/rubrics.degraded_judgment contract)
        ctx.budget.add(judgment.usage, stage="judge")
        ctx.events.emit("judge.degraded", round=index, error=judgment.summary[:300],
                        cost_usd=round(judgment.usage.cost_usd, 4))
        notes.append(f"judge degraded: {judgment.summary[:200]}")
        return None, judgment.usage
    ctx.events.emit("judge.done", round=index, overall=round(judgment.overall, 3), passed=judgment.passed,
                    n_issues=len(judgment.issues), n_plan=len(judgment.improvement_plan),
                    duration_s=round(time.time() - t0, 1), cost_usd=round(judgment.usage.cost_usd, 4))
    return judgment, judgment.usage


def rejudge_round(ctx: RunContext, pipeline: RoundPipeline, rec: RoundRecord, previous: Judgment | None = None) -> bool:
    """Re-judge an already built+rendered round whose judgment FAILED or was degraded.

    No regeneration, no rebuild: the same commit is judged again.  On success the
    round record is updated in place and re-persisted.  Returns True when the
    round now has a usable judgment."""
    if ctx.judge is None or rec.build is None or not rec.build.ok or rec.renders is None or not rec.renders.views:
        return False
    ctx.events.emit("judge.retry", round=rec.index)
    notes: list[str] = [rec.notes] if rec.notes else []
    judgment, judge_usage = _judge(ctx, pipeline, rec.index, rec.build, list(rec.gates), rec, previous, notes)
    if judgment is None:
        # the retry failed AGAIN — but it was still paid for: keep the money and the
        # failure note on the persisted record so totals and resume can see them.
        rec.usage = rec.usage + judge_usage
        rec.notes = "; ".join(notes)
        ctx.ws.write_json(round_record_path(ctx, rec.index), rec)
        return False
    rec.judgment = judgment
    rec.usage = rec.usage + judgment.usage
    rec.notes = "; ".join(notes)
    ctx.budget.add(judgment.usage, stage="judge")
    ctx.ws.write_json(round_record_path(ctx, rec.index), rec)
    # the round's original cost.round went out as wasted=unjudged; emit the corrected
    # one so the audit stream stops counting a now-scored round as wasted.
    emit_round_cost(ctx, index=rec.index, kind=rec.kind, cost={"judge": round(judgment.usage.cost_usd, 6)},
                    usage=rec.usage, turns=0, score=rec.score, build_ok=True, judged=True, corrected=True)
    return True


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


def sum_usage(rounds: Sequence[RoundRecord]) -> Usage:
    return sum((r.usage for r in rounds), Usage())
