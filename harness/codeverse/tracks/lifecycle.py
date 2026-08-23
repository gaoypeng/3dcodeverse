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
from dataclasses import replace
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
from codeverse.tracks.candidates import choose_best_round, run_best_of_n
from codeverse.tracks.common import (
    RunContext,
    Services,
    cookbook_rel_for,
    language_contract,
    load_prompt_or,
)
from codeverse.tracks.generation import GenerationTask, is_single_shot, single_shot_model_id
from codeverse.tracks.planner import plan as run_planner
from codeverse.tracks.steps import (
    RoundFailed,
    RoundPipeline,
    load_round_records,
    rejudge_round,
    run_round,
    sum_usage,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

REFERENCE_RUBRIC = "reference_v1"

_STATUS: dict[str, RunStatus] = {
    "pass": RunStatus.PASSED, "plateau": RunStatus.PLATEAU, "max_rounds": RunStatus.PLATEAU,
    "budget": RunStatus.BUDGET,
    # judge outage (degraded/crashed verdicts even after a retry): the code is intact
    # and the best built round is delivered — a stop, not a failure.
    "judge_unavailable": RunStatus.PLATEAU,
}


def plan_stage_inputs(spec: Spec) -> dict[str, Any]:
    """Only the spec fields the PLAN depends on: hashing the whole Spec meant that
    raising ``budget.max_usd`` in spec.json (the only way to continue a BUDGET-stopped
    run) re-planned + re-skeletoned on resume, overwriting src/ under existing rounds."""
    return {
        "prompt": spec.prompt,
        "track": spec.track.value,
        "language": spec.language.value,
        "constraints": spec.constraints,
        "references": spec.references,
        "planner": spec.backends.planner,
        "seed": spec.seed,
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
        n_candidates: int | None = None,
    ):
        self.services = services or Services()
        self._judge = judge
        self._agent = agent
        self._model = model
        self._runtime = runtime
        self._policy = policy
        self._settings = settings
        self._planner_model = planner_model
        self._n_candidates = n_candidates

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
            ctx.plan = runner.stage("plan", lambda: self._plan_stage(ctx), inputs={"spec": plan_stage_inputs(spec), "track": self.track.value},
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
            self._save_spent(ctx)  # mid-round charges must survive for resume
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
        n_cand = self._resolve_candidates(state, settings)
        policy = (self._policy or RoundPolicy(max_rounds=spec.budget.max_rounds)).with_candidates(n_cand)
        # reference images: static objects are scored with reference_v1 (adds the measured silhouette
        # criterion); other tracks keep their rubric but the judge still sees the references.
        rubric = REFERENCE_RUBRIC if spec.references and self.track is Track.STATIC_OBJECT else self.rubric
        ctx = RunContext(spec=spec, ws=ws, events=events, settings=settings, budget=budget, runtime=runtime,
                         services=self.services, state=state, policy=policy, track=self.track, rubric=rubric,
                         agent_id=spec.backends.generator, agent=self._agent, model=self._model)
        ctx.contract_text = language_contract(spec.language, runtime)
        ctx.cookbook_rel = cookbook_rel_for(spec.language)
        ctx.cookbook_text = load_prompt_or(ctx.cookbook_rel, "")
        ctx.tool_cards = self.services.tool_cards(self.track.value, spec.language.value)
        for name, text in (("contract", ctx.contract_text), ("cookbook", ctx.cookbook_text)):
            ctx.record_prompt(name, text)
        return ctx

    def _resolve_candidates(self, state: RunState, settings: Settings) -> int:
        """Best-of-N width: constructor (CLI --candidates) > persisted run state (resume) > settings default."""
        n = self._n_candidates
        if n is None:
            n = state.extra.get("n_candidates")
        if n is None:
            n = getattr(settings, "default_candidates", 1)
        n = max(1, int(n or 1))
        state.extra["n_candidates"] = n
        return n

    def make_judge(self, ctx: RunContext, *, n_samples: int | None = None) -> Any:
        """The main judge: injected → reference judge when the spec has images → rubric VLM judge."""
        if self._judge is not None:
            return self._judge
        n_samples = ctx.policy.judge_samples if n_samples is None else n_samples
        if ctx.spec.references:
            return self.services.reference_judge(ctx.spec.backends.judge, n_samples=n_samples, rubric=ctx.rubric)
        return self.services.judge(ctx.rubric, ctx.spec.backends.judge, n_samples=n_samples)

    def after_plan(self, ctx: RunContext) -> None:
        """Bind judge / generator backends once the plan exists (rubric threshold → policy target)."""
        if ctx.judge is None:
            ctx.judge = self.make_judge(ctx)
        if self._policy is None:
            thr = self.services.rubric_threshold(ctx.rubric)
            if thr is not None:
                ctx.policy = replace(ctx.policy, target=float(thr))
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

        inputs = {"plan": ctx.plan, "language": ctx.language.value}
        if ctx.state.completed_rounds and not runner.is_done("skeleton", inputs):
            # stale skeleton hash on resume: rounds exist, so re-running the skeleton
            # writer would overwrite agent-authored src/ — never do that.
            ctx.events.emit("skeleton.skipped", reason="rounds_exist", rounds=len(ctx.state.completed_rounds))
            return []
        return runner.stage("skeleton", _do, inputs=inputs)

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
        rejudged: set[int] = set()
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
                last = rounds[-1]
                if last.judgment is None and last.build is not None and last.build.ok and last.index not in rejudged:
                    # judge outage/degraded verdict on a clean build: re-judge the SAME
                    # commit once (no regeneration) before planning refinements from nothing.
                    rejudged.add(last.index)
                    prev_j = rounds[-2].judgment if len(rounds) > 1 else None
                    if rejudge_round(ctx, pipeline, last, previous=prev_j):
                        best = choose_best_round(ctx, rounds, selector, last.index)
                        if best is not None and ctx.state.update_best(best, rounds[best].commit, rounds[best].score):
                            ctx.events.emit("best.updated", round=best, score=rounds[best].score)
                        self._save_spent(ctx)
                        continue  # decide() re-runs with the recovered score
                tasks, instructions = self.refine_tasks(ctx, last, rounds)
                kind = "refine"
                if not tasks:
                    if last.judgment is None and last.build is not None and last.build.ok:
                        # no tasks only because the judge never scored the round: stopping
                        # as 'plateau' would blame the code for a judge glitch
                        ctx.events.emit("stop", reason="judge_unavailable", rounds=len(rounds), best=ctx.state.best_round)
                        return "judge_unavailable"
                    ctx.events.emit("stop", reason="no_refine_tasks", rounds=len(rounds))
                    return "plateau"
            ctx.state.current_round = index
            ctx.state.save(ctx.ws)
            try:
                if index == 0 and tasks and ctx.policy.n_candidates > 1:
                    rec = run_best_of_n(self, ctx, tasks, pipeline, files_hint=self.round_files_hint(ctx))
                else:
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
            best = choose_best_round(ctx, rounds, selector, index)
            if best is not None and ctx.state.update_best(best, rounds[best].commit, rounds[best].score):
                ctx.events.emit("best.updated", round=best, score=rounds[best].score)
            self._save_spent(ctx)

    def _save_spent(self, ctx: RunContext) -> None:
        ctx.state.extra["spent_usage"] = ctx.budget.spent.model_dump(mode="json")
        ctx.state.save(ctx.ws)

    # ------------------------------------------------------------------ finalise
    def finalise(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, stop_reason: str, error: str = "") -> RunRecord:
        self._save_spent(ctx)  # aborted-round / stage charges must survive for resume
        best = ctx.state.best_round
        if best is not None and best < len(rounds) and rounds[best].commit and self._needs_restore(ctx, rounds[best].commit):
            ctx.ws.restore(rounds[best].commit)
            ctx.ws.commit(f"restore best round r{best:02d}")
            try:
                build = ctx.runtime.build(ctx.ws, timeout_s=ctx.settings.limits.build_timeout_s)
                ctx.events.emit("finalise.rebuild", round=best, ok=build.ok)
            except Exception as e:  # noqa: BLE001 — the best round already built once; report, don't fail
                ctx.events.emit("finalise.rebuild_failed", error=f"{type(e).__name__}: {e}")
        if rounds and ("texture" in ctx.spec.tags or ctx.extra.get("texture")):
            self._texture_pass(ctx)
        ctx.state.status, ctx.state.stop_reason, ctx.state.error = status, stop_reason, error
        ctx.state.save(ctx.ws)
        rec = self._record(ctx, rounds, status, error=error, stop_reason=stop_reason)
        self.services.finalize_record(ctx.ws, rec)
        ctx.events.emit("run.done", status=status.value, stop=stop_reason, best_round=rec.best_round,
                        final_score=rec.final_score, cost_usd=round(rec.total_usage.cost_usd, 4))
        return rec

    @staticmethod
    def _needs_restore(ctx: RunContext, best_commit: str) -> bool:
        """src/ != best commit: HEAD moved on, OR an aborted round dirtied the tree
        without committing (budget stop mid-generation) — HEAD alone cannot see that."""
        if ctx.ws.head() != best_commit:
            return True
        try:
            return bool(ctx.ws.changed_files())
        except Exception as e:  # noqa: BLE001 — a git hiccup must not block finalise
            log.warning("changed_files failed in finalise: %s", e)
            return False

    def _texture_pass(self, ctx: RunContext) -> None:
        """Optional post-hoc texture pass (spec tag 'texture').  Additive: any failure
        is logged + emitted, never fatal; usage is added to the budget totals."""
        try:
            from codeverse.texturing.run import texture_pass

            trep = texture_pass(ctx.ws, ctx.spec, ctx.plan, model_id=ctx.spec.backends.planner, judge=True,
                                judge_model_id=ctx.spec.backends.judge, rubric=self.rubric, events=ctx.events,
                                update_record=False)
        except Exception as e:  # noqa: BLE001 — texturing is a derived asset pack, never run-fatal
            log.warning("texture pass failed: %s", e)
            ctx.events.emit("texture.failed", error=f"{type(e).__name__}: {e}")
            return
        ctx.budget.add(trep.usage)
        ctx.extra["texturing"] = trep.summary()

    def _record(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, error: str, stop_reason: str) -> RunRecord:
        best = ctx.state.best_round
        baseline = rounds[0].score if rounds else None
        final = rounds[best].score if best is not None and best < len(rounds) else None
        total = sum_usage(rounds)
        # planner + other non-round calls are in the budget guard but not in rounds
        if ctx.budget.spent.cost_usd > total.cost_usd:
            total = ctx.budget.spent
        extra: dict[str, Any] = {"stop_reason": stop_reason, "rubric": ctx.rubric, "budget": ctx.budget.summary(),
                                 "n_candidates": ctx.policy.n_candidates, "candidates": ctx.state.extra.get("candidates")}
        if ctx.extra.get("texturing"):
            extra["texturing"] = ctx.extra["texturing"]
        return RunRecord(
            spec=ctx.spec, plan=ctx.plan, workspace=str(ctx.ws.root), status=status, rounds=rounds, best_round=best,
            baseline_score=baseline, final_score=final, total_usage=total,
            environment={"python": platform.python_version(), "host": platform.node(), "track": self.track.value,
                         "language": ctx.language.value, "generator": ctx.agent_id},
            prompt_hashes=dict(ctx.prompt_hashes), started_at=ctx.state.started_at,
            finished_at=datetime.now(UTC), error=error,
            extra=extra,
        )


def generator_label(agent_id: str) -> str:
    return "single-shot" if is_single_shot(agent_id) else agent_id.split(":", 1)[0]
