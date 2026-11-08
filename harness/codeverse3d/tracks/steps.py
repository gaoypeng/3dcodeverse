"""One round = generate → build(+repair) → gates → measure → render → judge → commit."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
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
from c3d_flywheel.cost.tally import tally, timed
from codeverse3d.judges.base import round_input
from codeverse3d.orchestrator import BudgetExceeded
from codeverse3d.proc import fan_out
from c3d_flywheel.record.deliverable import keep_round_artifacts
from codeverse3d.spatial.render import RenderError
from codeverse3d.tracks import skills_hook
from codeverse3d.tracks.common import RunContext, generate_for
from codeverse3d.tracks.generation import GenerationResult, GenerationTask
from codeverse3d.tracks.project_checks import run_project_checks
from codeverse3d.tracks.repair import RepairOutcome, build_with_repair
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


class RoundPipeline(Protocol):
    """Track-specific steps after a successful build."""

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None: ...
    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]: ...
    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet: ...
    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        """Track-specific judge context.  Deliberately ``RunContext``-free so"""
        ...


class RoundFailed(RuntimeError):
    """Every generation task of a round failed — nothing to build."""

    def __init__(self, message: str, *, transient: bool = False, quota: bool = False, stalled: bool = False) -> None:
        super().__init__(message)
        self.transient = transient
        self.quota = quota
        self.stalled = stalled

    @classmethod
    def of(cls, results: Sequence[GenerationResult], default: str) -> RoundFailed:
        """The round's failure: every task's notes, and the typed flags of any of them — except"""
        return cls("; ".join(f"{r.label}: {r.notes}" for r in results) or default,
                   transient=any(r.transient for r in results), quota=any(r.quota for r in results),
                   stalled=any(r.stalled for r in results) and all(r.stalled or r.transient for r in results))


def round_record_path(ctx: RunContext, index: int) -> Path:
    return ctx.ws.root / "rounds" / f"r{index:02d}.json"


def load_round_journal(ws: Workspace) -> list[RoundRecord]:
    """The on-disk round journal: ``rounds/r*.json`` sorted, contiguous prefix only."""
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
                         steps: list[StepTime]) -> list[GenerationResult]:
    """Run tasks side by side — one ``generate`` step: its sessions share one clock.  Raises"""
    if not tasks:
        return []

    def _one(task: GenerationTask) -> GenerationResult:
        return generate_for(ctx, task)

    # fan_out also for ONE task: a crashing generator (503 storm, parse error) becomes a failed
    # result → RoundFailed (a refine round then stops the run as no_change) instead of killing it.
    with timed("generate", steps, round_index=tasks[0].round):
        results = fan_out(list(tasks), _one, max_workers=ctx.settings.limits.max_parallel_agents,
                          label="generate", item_name=lambda t: t.label)
    out: list[GenerationResult] = []
    budget_stop: Exception | None = None
    for task, r in zip(tasks, results, strict=True):
        if isinstance(r, Exception):
            if isinstance(r, BudgetExceeded) and budget_stop is None:
                budget_stop = r
            ctx.events.emit("generate.failed", label=task.label, error=f"{type(r).__name__}: {r}")
            out.append(GenerationResult.from_error(task.label, r))
        else:
            out.append(r)
    if not any(r.ok for r in out):
        if budget_stop is not None:
            raise budget_stop
        raise RoundFailed.of(out, "no generation task succeeded")
    ctx.budget.check()
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
    findings: Sequence[GateReport] = (),
    files_hint: Sequence[str] = (),
    extra_usage: Usage | None = None,
    extra_notes: Sequence[str] = (),
) -> RoundRecord:
    """Execute one round and persist its record.  Budget is charged as it goes."""
    steps: list[StepTime] = []
    with tally() as spent:
        try:
            t0 = time.time()
            ctx.events.emit("round.start", round=index, kind=kind, n_tasks=len(tasks))
            rec = RoundRecord(index=index, kind=kind, agent_backend=ctx.agent_id, instructions=list(instructions),
                              started_at=datetime.now(UTC))
            notes: list[str] = list(extra_notes)

            skills_hook.attach_for_round(ctx, index=index, kind=kind, findings=findings)
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
                    if ctx.spec.checks and outcome.build.glb_path:  # the project's own checks (Spec.checks)
                        gates.extend(run_project_checks(ctx.spec, ctx.ws.root, Path(outcome.build.glb_path),
                                                        rec.measurement, index))
                try:
                    with timed("render", steps, round_index=index):
                        rec.renders = pipeline.render(ctx, index, outcome.build, rec.measurement)
                except RenderError as e:
                    rec.renders = None
                    notes.append(f"render failed: {str(e)[:200]}")
                    ctx.events.emit("render.failed", round=index, error=str(e)[:400])
                post = getattr(pipeline, "post_render_gates", None)
                if callable(post) and rec.renders is not None:
                    with timed("gates", steps, round_index=index):
                        gates.extend(post(ctx, index, rec.renders))
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
                        rec.judgment = _judge(ctx, pipeline, index, outcome.build, gates, rec, previous, notes)
            else:
                gates.extend(outcome.build.gates)
                notes.append(f"build failed: {outcome.build.error_type}: {outcome.build.error_message[:200]}")
            rec.gates = gates
            rec.skills = skills_hook.record_usage(ctx, index=index, kind=kind)
            _write_gate_reports(ctx, index, gates)

            rec.commit = ctx.ws.commit(f"r{index:02d} {kind}")
            if outcome.build.ok:
                keep_round_artifacts(ctx.ws, index)
            rec.usage = spent.usage + (extra_usage or Usage())
            rec.duration_s = round(time.time() - t0, 2)
            rec.steps = steps
            rec.notes = "; ".join(notes)
            ctx.ws.write_json(round_record_path(ctx, index), rec)
            ctx.events.emit("round.done", round=index, kind=kind, commit=rec.commit[:10], score=rec.score,
                            build_ok=outcome.build.ok, duration_s=rec.duration_s, cost_usd=round(rec.usage.cost_usd, 4))
            return rec
        except BaseException as e:
            record_aborted_round(ctx, index=index, kind=kind, usage=spent.usage,
                                 error=f"{type(e).__name__}: {e}", steps=steps)
            raise


# ----------------------------------------------------------------------------- cost of a round
def skip_judge_reason(ctx: RunContext, *, renders: RenderSet | None) -> str:
    """Why this round must NOT be judged (``""`` = judge it)."""
    if ctx.judge is None:
        return "no judge configured"
    if renders is None or not renders.views:
        return "no renders"
    return ""


def record_aborted_round(ctx: RunContext, *, index: int, kind: str, usage: Usage, error: str,
                         steps: Sequence[StepTime] = ()) -> None:
    """A round that raised half-way still reports what it burned."""
    try:
        ctx.state.steps.extend(steps)
        aborted = ctx.extra.setdefault("aborted_rounds", [])
        aborted.append({"index": index, "kind": kind, "cost_usd": round(usage.cost_usd, 6), "error": error[:300]})
    except Exception as e:  # noqa: BLE001 — never mask the exception that stopped the round
        log.warning("could not record aborted round r%02d: %s", index, e)


def _judge(ctx: RunContext, pipeline: RoundPipeline, index: int, build: BuildResult, gates: list[GateReport],
           rec: RoundRecord, previous: Judgment | None, notes: list[str]) -> Judgment | None:
    """Judge one round; ``None`` when the verdict is unusable."""
    renders = rec.renders
    jv = getattr(pipeline, "judge_views", None)
    if callable(jv) and renders is not None:
        renders = jv(ctx, renders) or renders
    geometry = None
    gv = getattr(pipeline, "geometry_views", None)
    if callable(gv):
        geometry = gv(ctx, index, build)
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
    if judgment.degraded:
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
    if ctx.judge is None or rec.build is None or not rec.build.ok or rec.renders is None or not rec.renders.views:
        return False
    ctx.events.emit("judge.retry", round=rec.index)
    notes: list[str] = [rec.notes] if rec.notes else []
    with timed("judge", rec.steps, round_index=rec.index) as spent:
        judgment = _judge(ctx, pipeline, rec.index, rec.build, list(rec.gates), rec, previous, notes)
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
