"""BaseTrack: the resumable plan → prepare → rounds → finalise lifecycle.

Concrete tracks (static / articulated / scene) override the hooks
(``plan_model``, ``make_pipeline``, ``prepare``, ``baseline_tasks``,
``refine_tasks``).  All bookkeeping — workspace, events, budget, run state,
stage cache, best tracking, stop policy, record — lives here once.
"""

from __future__ import annotations

import logging
import platform
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codeverse.config import Settings, get_settings
from codeverse.contracts.common import Track, Usage
from codeverse.contracts.plan import Plan
from codeverse.contracts.run import RoundRecord, RunRecord, RunStatus
from codeverse.contracts.spec import Spec
from codeverse.events import EventLog
from codeverse.orchestrator.budget import BudgetExceeded, BudgetGuard
from codeverse.orchestrator.rounds import BestSelector, RoundPolicy, StopPolicy, StopReason
from codeverse.orchestrator.runner import StageRunner
from codeverse.orchestrator.state import RunState
from codeverse.tracks.common import (
    RunContext,
    Services,
    cookbook_rel_for,
    language_contract,
    load_prompt_or,
)
from codeverse.tracks.generation import GenerationTask, is_single_shot, single_shot_model_id
from codeverse.tracks.planner import plan as run_planner
from codeverse.tracks.steps import RoundFailed, RoundPipeline, load_round_records, run_round, sum_usage
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

_STATUS: dict[str, RunStatus] = {
    "pass": RunStatus.PASSED, "plateau": RunStatus.PLATEAU, "max_rounds": RunStatus.PLATEAU,
    "budget": RunStatus.BUDGET,
}


class BaseTrack:
    """Shared lifecycle.  Subclasses set ``track``, ``rubric``, ``plan_model``."""

    track: Track
    rubric: str
    plan_model: type[Plan]

    def __init__(
        self,
        *,
        services: Services | None = None,
        judge: Any | None = None,
        agent: Any | None = None,
        model: Any | None = None,
        runtime: Any | None = None,
        policy: RoundPolicy | None = None,
        settings: Settings | None = None,
        planner_model: Any | None = None,
    ):
        self.services = services or Services()
        self._judge = judge
        self._agent = agent
        self._model = model
        self._runtime = runtime
        self._policy = policy
        self._settings = settings
        self._planner_model = planner_model

    # ------------------------------------------------------------------ hooks
    def make_pipeline(self, ctx: RunContext) -> RoundPipeline:
        raise NotImplementedError

    def prepare(self, ctx: RunContext, runner: StageRunner) -> None:
        """Stages between plan and baseline (skeleton; scene assets/env/zones/assemble)."""
        self.stage_skeleton(ctx, runner)

    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        raise NotImplementedError

    def refine_tasks(self, ctx: RunContext, last: RoundRecord, history: Sequence[RoundRecord]) -> tuple[list[GenerationTask], list[str]]:
        raise NotImplementedError

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return []

    # ------------------------------------------------------------------ public API
    def plan(self, spec: Spec, ws: Workspace) -> Plan:
        ws.create()
        events = EventLog(ws.events_path)
        runtime = self._runtime or self.services.runtime(spec.language)
        return run_planner(spec, spec.backends.planner, self.plan_model, ws, model=self._planner_model,
                           events=events, runtime=runtime)

    def run(self, spec: Spec, ws: Workspace, *, resume: bool = False) -> RunRecord:
        ws.create()
        if not ws.spec_path.is_file() or not resume:
            ws.write_json(ws.spec_path, spec)
        events = EventLog(ws.events_path)
        state = RunState.load_or_new(ws, resume=resume)
        ctx = self.build_context(spec, ws, events, state)
        runner = StageRunner(ws, events, state)
        events.emit("run.start", track=self.track.value, language=spec.language.value, resume=resume,
                    agent=ctx.agent_id, planner=spec.backends.planner, judge=spec.backends.judge)
        rounds: list[RoundRecord] = []
        stop: StopReason | str = "failed"
        error = ""
        try:
            ctx.plan = runner.stage("plan", lambda: self._plan_stage(ctx), inputs={"spec": spec, "track": self.track.value},
                                    model=self.plan_model)
            self.after_plan(ctx)
            self.prepare(ctx, runner)
            rounds = load_round_records(ctx) if resume else []
            stop = self._round_loop(ctx, rounds)
        except BudgetExceeded as e:
            events.emit("budget.exceeded", reason=e.reason, spent_usd=round(e.spent_usd, 4))
            stop, error = "budget", e.reason
        except Exception as e:  # noqa: BLE001 — persist a FAILED record, then fail loud
            error = f"{type(e).__name__}: {e}"
            events.emit("run.failed", error=error, traceback=traceback.format_exc()[-3000:])
            state.status, state.error = RunStatus.FAILED, error
            state.save(ws)
            rec = self._record(ctx, rounds, RunStatus.FAILED, error=error, stop_reason="failed")
            self.services.finalize_record(ws, rec)
            raise
        status = _STATUS.get(stop, RunStatus.FAILED)
        rec = self.finalise(ctx, rounds, status, stop_reason=str(stop), error=error)
        return rec

    # ------------------------------------------------------------------ context
    def build_context(self, spec: Spec, ws: Workspace, events: EventLog, state: RunState) -> RunContext:
        settings = self._settings or get_settings()
        runtime = self._runtime or self.services.runtime(spec.language)
        budget = BudgetGuard(spec.budget)
        spent = state.extra.get("spent_usage")
        if spent:
            budget.spent = Usage.model_validate(spent)
        policy = self._policy or RoundPolicy(max_rounds=spec.budget.max_rounds)
        ctx = RunContext(spec=spec, ws=ws, events=events, settings=settings, budget=budget, runtime=runtime,
                         services=self.services, state=state, policy=policy, track=self.track, rubric=self.rubric,
                         agent_id=spec.backends.generator, agent=self._agent, model=self._model)
        ctx.contract_text = language_contract(spec.language, runtime)
        ctx.cookbook_rel = cookbook_rel_for(spec.language)
        ctx.cookbook_text = load_prompt_or(ctx.cookbook_rel, "")
        ctx.tool_cards = self.services.tool_cards(self.track.value, spec.language.value)
        for name, text in (("contract", ctx.contract_text), ("cookbook", ctx.cookbook_text)):
            ctx.record_prompt(name, text)
        return ctx

    def after_plan(self, ctx: RunContext) -> None:
        """Bind judge / generator backends once the plan exists (rubric threshold → policy target)."""
        if ctx.judge is None:
            ctx.judge = self._judge if self._judge is not None else self.services.judge(self.rubric, ctx.spec.backends.judge, n_samples=2)
        if self._policy is None:
            thr = self.services.rubric_threshold(self.rubric)
            if thr is not None:
                ctx.policy = RoundPolicy(max_rounds=ctx.spec.budget.max_rounds, target=float(thr))
        if ctx.single_shot:
            if ctx.model is None:
                ctx.model = self.services.chat_model(single_shot_model_id(ctx.agent_id))
        elif ctx.agent is None:
            ctx.agent = self.services.coding_agent(ctx.agent_id)

    def _plan_stage(self, ctx: RunContext) -> Plan:
        ctx.state.status = RunStatus.PLANNING
        ctx.state.save(ctx.ws)
        plan = run_planner(ctx.spec, ctx.spec.backends.planner, self.plan_model, ctx.ws, model=self._planner_model,
                           events=ctx.events, budget=ctx.budget, runtime=ctx.runtime)
        self._save_spent(ctx)
        return plan

    # ------------------------------------------------------------------ stages
    def stage_skeleton(self, ctx: RunContext, runner: StageRunner) -> list[str]:
        def _do() -> list[str]:
            paths = ctx.runtime.skeleton(ctx.ws, ctx.plan)
            ctx.ws.commit("skeleton")
            return [str(Path(p).relative_to(ctx.ws.root)) if Path(p).is_absolute() else str(p) for p in paths]

        return runner.stage("skeleton", _do, inputs={"plan": ctx.plan, "language": ctx.language.value})

    def ensure_materialized(self, ctx: RunContext) -> None:
        """Materialise AGENTS.md/MCP config once per run for agent generators."""
        if ctx.single_shot:
            return
        kind = ctx.agent_id.split(":", 1)[0]
        if ctx.state.materialized_for == kind:
            return
        mcp = ["python", "-m", "codeverse.spatial.mcp_server", "--workspace", str(ctx.ws.root)]
        self.services.materialize(ctx.ws, agent_kind=kind, contract_md=self.agent_contract_md(ctx), cookbook_rel=ctx.cookbook_rel,
                                  spatial_tools=True, mcp_command=mcp)
        ctx.state.materialized_for = kind
        ctx.state.save(ctx.ws)
        ctx.events.emit("workspace.materialized", agent_kind=kind)

    def agent_contract_md(self, ctx: RunContext) -> str:
        harness = load_prompt_or("system/harness_contract.md", "")
        return (harness + "\n\n" + ctx.contract_text).strip()

    # ------------------------------------------------------------------ round loop
    def _round_loop(self, ctx: RunContext, rounds: list[RoundRecord]) -> StopReason:
        self.ensure_materialized(ctx)
        pipeline = self.make_pipeline(ctx)
        stop_policy = StopPolicy(ctx.policy)
        selector = BestSelector()
        while True:
            decision = stop_policy.decide(rounds, budget_ok=ctx.budget.ok()) if rounds else "continue"
            if decision != "continue":
                ctx.events.emit("stop", reason=decision, rounds=len(rounds), best=ctx.state.best_round)
                return decision
            index = len(rounds)
            previous = rounds[-1].judgment if rounds else None
            if index == 0:
                ctx.state.status = RunStatus.GENERATING
                tasks, instructions, kind = self.baseline_tasks(ctx), [], "baseline"
            else:
                ctx.state.status = RunStatus.REFINING
                tasks, instructions = self.refine_tasks(ctx, rounds[-1], rounds)
                kind = "refine"
                if not tasks:
                    ctx.events.emit("stop", reason="no_refine_tasks", rounds=len(rounds))
                    return "plateau"
            ctx.state.current_round = index
            ctx.state.save(ctx.ws)
            try:
                rec = run_round(ctx, index=index, kind=kind, tasks=tasks, pipeline=pipeline, instructions=instructions,
                                previous=previous, files_hint=self.round_files_hint(ctx))
            except RoundFailed as e:
                if index == 0:
                    raise
                # A refine round in which no task changed any file is not a crash: the
                # agents judged the requested edits unnecessary (or bailed).  Treat it as
                # a plateau signal so the best round so far is delivered.
                ctx.events.emit("round.no_change", round=index, detail=str(e)[:500])
                ctx.events.emit("stop", reason="no_change", rounds=len(rounds), best=ctx.state.best_round)
                return "plateau"
            rounds.append(rec)
            ctx.state.mark_round_done(index, rec.commit)
            best = selector.pick(rounds)
            if best is not None and ctx.state.update_best(best, rounds[best].commit, rounds[best].score):
                ctx.events.emit("best.updated", round=best, score=rounds[best].score)
            self._save_spent(ctx)

    def _save_spent(self, ctx: RunContext) -> None:
        ctx.state.extra["spent_usage"] = ctx.budget.spent.model_dump(mode="json")
        ctx.state.save(ctx.ws)

    # ------------------------------------------------------------------ finalise
    def finalise(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, stop_reason: str, error: str = "") -> RunRecord:
        best = ctx.state.best_round
        if best is not None and rounds and rounds[best].commit and ctx.ws.head() != rounds[best].commit:
            ctx.ws.restore(rounds[best].commit)
            ctx.ws.commit(f"restore best round r{best:02d}")
            try:
                build = ctx.runtime.build(ctx.ws, timeout_s=ctx.settings.limits.build_timeout_s)
                ctx.events.emit("finalise.rebuild", round=best, ok=build.ok)
            except Exception as e:  # noqa: BLE001 — the best round already built once; report, don't fail
                ctx.events.emit("finalise.rebuild_failed", error=f"{type(e).__name__}: {e}")
        ctx.state.status, ctx.state.stop_reason, ctx.state.error = status, stop_reason, error
        ctx.state.save(ctx.ws)
        rec = self._record(ctx, rounds, status, error=error, stop_reason=stop_reason)
        self.services.finalize_record(ctx.ws, rec)
        ctx.events.emit("run.done", status=status.value, stop=stop_reason, best_round=rec.best_round,
                        final_score=rec.final_score, cost_usd=round(rec.total_usage.cost_usd, 4))
        return rec

    def _record(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, error: str, stop_reason: str) -> RunRecord:
        best = ctx.state.best_round
        baseline = rounds[0].score if rounds else None
        final = rounds[best].score if best is not None and best < len(rounds) else None
        total = sum_usage(rounds)
        # planner + other non-round calls are in the budget guard but not in rounds
        if ctx.budget.spent.cost_usd > total.cost_usd:
            total = ctx.budget.spent
        return RunRecord(
            spec=ctx.spec, plan=ctx.plan, workspace=str(ctx.ws.root), status=status, rounds=rounds, best_round=best,
            baseline_score=baseline, final_score=final, total_usage=total,
            environment={"python": platform.python_version(), "host": platform.node(), "track": self.track.value,
                         "language": ctx.language.value, "generator": ctx.agent_id},
            prompt_hashes=dict(ctx.prompt_hashes), started_at=ctx.state.started_at,
            finished_at=datetime.now(UTC), error=error,
            extra={"stop_reason": stop_reason, "rubric": self.rubric, "budget": ctx.budget.summary()},
        )


def generator_label(agent_id: str) -> str:
    return "single-shot" if is_single_shot(agent_id) else agent_id.split(":", 1)[0]
