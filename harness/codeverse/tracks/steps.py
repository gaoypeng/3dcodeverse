"""One round = generate → build(+repair) → gates → measure → render → judge → commit.

``run_round`` is track-agnostic; a ``RoundPipeline`` supplies the track-specific
gates / measurement / rendering.  Every step writes its artifact under the
workspace (gates/rNN, renders/rNN, judge/rNN.json, rounds/rNN.json) so a run
can be resumed and the flywheel can replay it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, RenderSet
from codeverse.contracts.common import Usage
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem, Plan
from codeverse.contracts.run import RoundRecord
from codeverse.orchestrator.fanout import fan_out
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import GenerationResult, GenerationTask, generate
from codeverse.tracks.repair import RepairOutcome, build_with_repair
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)


class RoundPipeline(Protocol):
    """Track-specific steps after a successful build."""

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None: ...
    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]: ...
    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet: ...
    def plan_summary(self, ctx: RunContext) -> str: ...
    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        """Track-specific judge context.  Deliberately ``RunContext``-free so
        ``3dcv judge`` can rebuild the in-run context from stored artifacts."""
        ...


class RoundFailed(RuntimeError):
    """Every generation task of a round failed — nothing to build."""


def round_record_path(ctx: RunContext, index: int) -> Path:
    return ctx.ws.root / "rounds" / f"r{index:02d}.json"


def load_round_records(ctx: RunContext) -> list[RoundRecord]:
    """Rounds persisted so far (sorted by index, contiguous prefix only)."""
    d = ctx.ws.root / "rounds"
    if not d.is_dir():
        return []
    out: list[RoundRecord] = []
    for i, p in enumerate(sorted(d.glob("r*.json"))):
        rec = RoundRecord.model_validate(ctx.ws.read_json(p))
        if rec.index != i:
            break
        out.append(rec)
    return out


def run_generation_tasks(ctx: RunContext, tasks: Sequence[GenerationTask]) -> list[GenerationResult]:
    """Run tasks (parallel when > 1).  Raises ``RoundFailed`` when none succeeded."""
    if not tasks:
        return []

    def _one(task: GenerationTask) -> GenerationResult:
        return generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model,
                        settings=ctx.settings, budget=ctx.budget, events=ctx.events)

    # fan_out also for ONE task: a crashing generator (503 storm, parse error) becomes a failed
    # result → RoundFailed (refine rounds treat that as a plateau) instead of killing the run.
    results = fan_out(list(tasks), _one, max_workers=ctx.settings.limits.max_parallel_agents,
                      label="generate", item_name=lambda t: t.label)
    out: list[GenerationResult] = []
    for task, r in zip(tasks, results, strict=True):
        if isinstance(r, Exception):
            from codeverse.orchestrator.budget import BudgetExceeded

            if isinstance(r, BudgetExceeded):
                raise r
            ctx.events.emit("generate.failed", label=task.label, error=f"{type(r).__name__}: {r}")
            out.append(GenerationResult(ok=False, notes=f"{type(r).__name__}: {r}", label=task.label))
        else:
            out.append(r)
    if not any(r.ok for r in out):
        raise RoundFailed("; ".join(f"{r.label}: {r.notes}" for r in out) or "no generation task succeeded")
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
    (candidate generation) into the record."""
    t0 = time.time()
    ctx.events.emit("round.start", round=index, kind=kind, n_tasks=len(tasks))
    rec = RoundRecord(index=index, kind=kind, agent_backend=ctx.agent_id, instructions=list(instructions),
                      started_at=datetime.now(UTC))
    usage = extra_usage or Usage()
    notes: list[str] = list(extra_notes)

    gens = run_generation_tasks(ctx, tasks)
    for g in gens:
        usage = usage + g.usage
        if not g.ok:
            notes.append(f"{g.label}: {g.notes}")
    ctx.ws.commit(f"r{index:02d} {kind}: generated")

    outcome: RepairOutcome = build_with_repair(ctx, round_index=index, label=f"r{index:02d}_{kind}",
                                               files_hint=list(files_hint))
    usage = usage + outcome.usage
    rec.build = outcome.build
    gates: list[GateReport] = [outcome.lint]
    if outcome.attempts:
        notes.append(f"repair attempts: {len(outcome.attempts)} ({'fixed' if outcome.ok else 'still failing'})")

    if outcome.build.ok:
        rec.measurement = pipeline.measure(ctx, outcome.build)
        gates.extend(pipeline.gates(ctx, index, outcome.build, rec.measurement))
        rec.renders = pipeline.render(ctx, index, outcome.build, rec.measurement)
        post = getattr(pipeline, "post_render_gates", None)
        if callable(post) and rec.renders is not None:
            gates.extend(post(ctx, index, rec.renders))
        n_err = sum(len(g.errors) for g in gates)
        ctx.events.emit("gates.done", round=index, n_gates=len(gates), n_errors=n_err,
                        tri_count=rec.measurement.tri_count if rec.measurement else None)
        if ctx.judge is not None and rec.renders is not None and rec.renders.views and (n_err == 0 or ctx.policy.judge_on_gate_errors):
            rec.judgment = _judge(ctx, pipeline, index, outcome.build, gates, rec, previous)
            if rec.judgment is not None:
                usage = usage + rec.judgment.usage
                # add, never charge: the verdict exists and is paid for — raising here
                # would drop a fully judged round before it is committed/recorded
                # (the loop stops at its next budget_ok check instead, AFTER best promotion).
                ctx.budget.add(rec.judgment.usage)
        elif ctx.judge is None:
            notes.append("no judge configured")
        else:
            notes.append("judge skipped (gate errors)")
    else:
        notes.append(f"build failed: {outcome.build.error_type}: {outcome.build.error_message[:200]}")
    rec.gates = gates
    _write_gate_reports(ctx, index, gates)

    rec.commit = ctx.ws.commit(f"r{index:02d} {kind}")
    rec.usage = usage
    rec.duration_s = round(time.time() - t0, 2)
    rec.notes = "; ".join(notes)
    ctx.ws.write_json(round_record_path(ctx, index), rec)
    ctx.events.emit("round.done", round=index, kind=kind, commit=rec.commit[:10], score=rec.score,
                    build_ok=outcome.build.ok, duration_s=rec.duration_s, cost_usd=round(usage.cost_usd, 4))
    return rec


def _judge(ctx: RunContext, pipeline: RoundPipeline, index: int, build: BuildResult, gates: list[GateReport],
           rec: RoundRecord, previous: Judgment | None) -> Judgment | None:
    from codeverse.judges.base import JudgeInput

    renders = rec.renders
    jv = getattr(pipeline, "judge_views", None)
    if callable(jv) and renders is not None:
        try:
            renders = jv(ctx, renders) or renders
        except Exception as e:  # noqa: BLE001 — view selection is an optimisation, never a blocker
            log.warning("judge_views selection failed: %s", e)
            renders = rec.renders
    geometry = None
    gv = getattr(pipeline, "geometry_views", None)
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
    )
    t0 = time.time()
    try:
        judgment: Judgment = ctx.judge.judge(inp)
    except Exception as e:  # noqa: BLE001 — a judge outage must not destroy the run's code/commits
        log.exception("judge failed in round %d", index)
        ctx.events.emit("judge.failed", round=index, error=f"{type(e).__name__}: {e}")
        rec.notes = (rec.notes + "; " if rec.notes else "") + f"judge failed: {type(e).__name__}: {e}"
        return None
    ctx.ws.write_json(ctx.ws.judge_path(index), judgment)
    if _is_degraded(judgment):
        # a glitch, never a score: keep the raw verdict on disk, pay for it, but do not
        # let 0.0 poison plateau/best/refine (judges/scoring.degraded_judgment contract)
        ctx.budget.add(judgment.usage)
        ctx.events.emit("judge.degraded", round=index, error=judgment.summary[:300],
                        cost_usd=round(judgment.usage.cost_usd, 4))
        rec.notes = (rec.notes + "; " if rec.notes else "") + f"judge degraded: {judgment.summary[:200]}"
        return None
    ctx.events.emit("judge.done", round=index, overall=round(judgment.overall, 3), passed=judgment.passed,
                    n_issues=len(judgment.issues), n_plan=len(judgment.improvement_plan),
                    duration_s=round(time.time() - t0, 1), cost_usd=round(judgment.usage.cost_usd, 4))
    return judgment


def _is_degraded(judgment: Judgment) -> bool:
    try:
        from codeverse.judges.scoring import is_degraded
    except ImportError:  # pragma: no cover — judges package always ships with tracks
        return False
    return is_degraded(judgment)


def rejudge_round(ctx: RunContext, pipeline: RoundPipeline, rec: RoundRecord, previous: Judgment | None = None) -> bool:
    """Re-judge an already built+rendered round whose judgment failed or was degraded.

    No regeneration, no rebuild: the same commit is judged again.  On success the
    round record is updated in place and re-persisted.  Returns True when the
    round now has a usable judgment."""
    if rec.build is None or not rec.build.ok or rec.renders is None or not rec.renders.views:
        return False
    ctx.events.emit("judge.retry", round=rec.index)
    judgment = _judge(ctx, pipeline, rec.index, rec.build, list(rec.gates), rec, previous)
    if judgment is None:
        return False
    rec.judgment = judgment
    rec.usage = rec.usage + judgment.usage
    ctx.budget.add(judgment.usage)
    ctx.ws.write_json(round_record_path(ctx, rec.index), rec)
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


def sum_usage(rounds: Sequence[RoundRecord], *extra: Usage) -> Usage:
    total = Usage()
    for r in rounds:
        total = total + r.usage
    for u in extra:
        total = total + u
    return total


def describe_round(rec: RoundRecord) -> dict[str, Any]:
    return {"index": rec.index, "kind": rec.kind, "score": rec.score, "build_ok": bool(rec.build and rec.build.ok),
            "gate_errors": sum(len(g.errors) for g in rec.gates), "commit": rec.commit[:10]}
