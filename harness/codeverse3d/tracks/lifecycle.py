"""BaseTrack: the resumable plan → pre-round stages → rounds → finalise lifecycle."""

from __future__ import annotations

import logging
import platform
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import GateReport
from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import Plan
from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus
from codeverse3d.contracts.spec import Spec
from c3d_flywheel.cost.context import run_binding
from c3d_flywheel.cost.instrument import metered_agent, metered_chat_model, run_ledger
from c3d_flywheel.cost.ledger import ledger_usage, load_ledger
from c3d_flywheel.cost.tally import timed
from codeverse3d.judges.base import run_rubric
from codeverse3d.orchestrator import (
    BudgetExceeded,
    BudgetGuard,
    BudgetSnapshot,
    RoundPolicy,
    RunState,
    StageRunner,
    TaskGroup,
    build_refine_instructions,
    hash_inputs,
    plan_refine_groups,
)
from codeverse3d.proc import EventLog, fan_out, read_json_or_none
from codeverse3d.prompts import load_text, render
from codeverse3d.prompts.catalog import language_text
from c3d_flywheel.record.record import previous_verdict
from codeverse3d.tracks.common import RunContext, Services
from codeverse3d.tracks.generation import ALLOWED_ROOTS, GenerationTask, single_shot_model_id
from codeverse3d.tracks.planner import plan as run_planner
from codeverse3d.tracks.planner import plan_temperature
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    language_system_prompt,
    reference_images,
    refine_inline_files,
    skeleton_files,
)
from codeverse3d.tracks.repair import format_error_report
from codeverse3d.tracks.steps import (
    RoundFailed,
    RoundPipeline,
    failed_acceptance,
    load_round_journal,
    rejudge_round,
    run_round,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


def plan_stage_inputs(spec: Spec) -> dict[str, Any]:
    """Only the spec fields the PLAN depends on: hashing the whole Spec meant that"""
    inputs: dict[str, Any] = {
        "prompt": spec.prompt,
        "track": spec.track.value,
        "language": spec.language.value,
        "constraints": spec.constraints,
        "references": spec.references,
        "planner": spec.backends.planner,
    }
    if spec.fixed_parts:  # only when set, so every existing run's plan key stays put
        inputs["fixed_parts"] = spec.fixed_parts
    return inputs


def plan_stage_key(spec: Spec, track: Track) -> dict[str, Any]:
    return {"spec": plan_stage_inputs(spec), "track": track.value}


def spec_fingerprint(spec: Spec) -> str:
    """Identity of the spec THE PLAN depends on (hash of :func:`plan_stage_inputs`)."""
    return hash_inputs(plan_stage_inputs(spec))


class SpecChanged(RuntimeError):
    """spec.json's plan inputs changed under an existing run."""


# ============================================================================ the pre-round graph
@dataclass(frozen=True)
class StageNode:
    """One pre-round stage of a track's declared graph (``BaseTrack.stages``)."""

    name: str
    run: Callable[[Any, RunContext], Any]
    key: Callable[[RunContext], dict[str, Any]] | None = None
    when: Callable[[Settings], bool] | None = None
    seeds_src: bool = False


Stages = tuple[StageNode | tuple[StageNode, ...], ...]


def run_stages(track: BaseTrack, ctx: RunContext, runner: StageRunner, stages: Stages) -> None:
    """Walk ``stages``.  Siblings run side by side as ONE timed step of the run's minutes, each"""
    def one(node: StageNode, *, timed_step: bool = True) -> Any:
        if node.key is None:
            return node.run(track, ctx)
        key = node.key(ctx)
        if node.seeds_src and runner.frozen and not runner.is_done(node.name, key):
            # a drifted key or a lost state entry under existing rounds: re-running the writer
            # would overwrite agent-authored src/ — never do that
            ctx.events.emit("stage.skipped", stage=node.name, reason="rounds_exist")
            return None
        return runner.stage(node.name, lambda: node.run(track, ctx), inputs=key, timed_step=timed_step)

    for item in stages:
        live = [n for n in (item if isinstance(item, tuple) else (item,)) if n.when is None or n.when(get_settings())]
        if not live:
            continue
        if len(live) == 1:
            results = [one(live[0])]
        else:
            group = "+".join(n.name for n in live)
            with timed(group, ctx.state.steps):
                results = fan_out(live, lambda n: one(n, timed_step=False), max_workers=len(live), label=group,
                                  item_name=lambda n: n.name)
            if (exc := next((r for r in results if isinstance(r, Exception)), None)) is not None:
                raise exc
        ctx.extra.update(zip((n.name for n in live), results, strict=True))


def _skeleton(track: BaseTrack, ctx: RunContext) -> list[str]:
    # the project's fixed parts (Spec.fixed_parts) were seeded before the run: a language whose skeleton
    # rewrites src/ must not replace them, and a run whose seed is missing must stop here, not later
    fixed = {f.path: ctx.ws.root / f.path for f in ctx.spec.fixed_parts}
    missing = [p for p, f in fixed.items() if not f.is_file()]
    if missing:
        raise FileNotFoundError(f"fixed parts not seeded in the workspace: {missing}")
    kept = {p: f.read_bytes() for p, f in fixed.items()}
    paths = ctx.runtime.skeleton(ctx.ws, ctx.plan)
    for p, data in kept.items():
        if fixed[p].read_bytes() != data:
            fixed[p].write_bytes(data)
    ctx.ws.commit("skeleton")
    return [str(Path(p).relative_to(ctx.ws.root)) if Path(p).is_absolute() else str(p) for p in paths]


def _skeleton_key(ctx: RunContext) -> dict[str, Any]:
    """The plan and language."""
    return {"plan": ctx.plan, "language": ctx.language.value}


SKELETON = StageNode("skeleton", _skeleton, key=_skeleton_key, seeds_src=True)
MATERIALIZE = StageNode("materialize", lambda t, c: t.ensure_materialized(c))
CLOCK = StageNode("clock", lambda t, c: c.budget.check())


class BaseTrack:
    """Shared lifecycle.  Subclasses set ``track``, ``rubric``, ``plan_model``, their pre-round"""

    track: Track
    rubric: str
    plan_model: type[Plan]
    stages: Stages = (SKELETON, MATERIALIZE)
    generate_template: str = ""
    refine_template: str = ""
    allow_refine_fanout: bool = True
    soft_budget_fraction: float = 1.0
    baseline_temperature: float = 0.5

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
    def make_pipeline(self) -> RoundPipeline:
        raise NotImplementedError

    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        files = ctx.runtime.expected_files(ctx.plan)
        prompt = render(self.generate_template, **self.generate_context(
            ctx, skeleton_files=skeleton_files(ctx) if ctx.single_shot else {}, previous_error=""))
        ctx.record_prompt("generate", prompt)
        return [GenerationTask(label="baseline", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=0,
                               kind="baseline", temperature=self.baseline_temperature, owns_entry=True,
                               images=reference_images(ctx))]

    def system_prompt(self, ctx: RunContext) -> str:
        return language_system_prompt(ctx.language, tools=not ctx.single_shot)

    def round_extra_notes(self, ctx: RunContext) -> list[str]:
        return list(ctx.extra.get("degraded") or [])

    def prepare_salvage(self, ctx: RunContext) -> bool:
        """Make the workspace buildable after a stage tripped the budget before"""
        prepared = ctx.extra.get("prepared_commit")
        return bool(prepared) and any(c.path.startswith(ALLOWED_ROOTS) for c in ctx.ws.changed_files(since=prepared))

    # ---- refine hooks (the scaffold below is shared; tracks fill in the task)
    def refine_file_for_target(self, ctx: RunContext) -> Callable[[str], list[str]]:
        return lambda target: ctx.runtime.files_for(ctx.plan, target)

    def extra_refine_tasks(self, ctx: RunContext, last: RoundRecord) -> Sequence[Any]:
        """Harness-derived tasks prepended to the judge's (e.g. reference IoU)."""
        return ()

    def generate_context(self, ctx: RunContext, **extra: Any) -> dict[str, Any]:
        """Template context for ``generate_template`` (baseline / rebuild prompts)."""
        extra.setdefault("expected_files", ctx.runtime.expected_files(ctx.plan))
        return base_prompt_context(ctx, **extra)

    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        raise NotImplementedError

    # ------------------------------------------------------------------ refine scaffold
    def refine_tasks(self, ctx: RunContext, last: RoundRecord) -> tuple[list[GenerationTask], list[str]]:
        """The round after ``last``: rebuild guard → instructions → groups → per-track ``_refine_task``."""
        index = last.index + 1
        if last.build is None or not last.build.ok:
            rebuild = self._rebuild_task(ctx, last, index).model_copy(update={"reserve_s": ctx.policy.judge_reserve_s})
            return [rebuild], ["rebuild: previous round did not build"]
        tasks = build_refine_instructions(last.judgment, last.gates, failed_acceptance(ctx, last.judgment), ctx.plan,
                                          file_for_target=self.refine_file_for_target(ctx),
                                          max_tasks=ctx.policy.max_refine_tasks,
                                          extra=self.extra_refine_tasks(ctx, last))
        if not tasks:
            return [], []
        groups, parallel = plan_refine_groups(tasks, allow_fanout=self.allow_refine_fanout,
                                              parallel_min_tasks=ctx.policy.parallel_min_tasks)
        gen_tasks = [self._refine_task(ctx, g, last, index, parallel=parallel).model_copy(
            update={"reserve_s": ctx.policy.judge_reserve_s}) for g in groups]
        ctx.events.emit("refine.planned", round=index, n_tasks=len(tasks), n_groups=len(groups), parallel=parallel,
                        targets=[g.targets for g in groups])
        return gen_tasks, [t.line() for t in tasks]

    def _rebuild_task(self, ctx: RunContext, last: RoundRecord, index: int) -> GenerationTask:
        """Regenerate after a failed build: the generate prompt + the error report."""
        files = ctx.runtime.expected_files(ctx.plan)
        lint = next((g for g in last.gates if g.gate.startswith("lint")), GateReport(gate="lint", passed=True))
        report = format_error_report(last.build, lint) if last.build else "build did not run"
        prompt = render(self.generate_template, **self.generate_context(
            ctx, skeleton_files=refine_inline_files(ctx, files), previous_error=report))
        return GenerationTask(label="rebuild", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="rebuild", temperature=0.7, thinking="high")

    # ------------------------------------------------------------------ public API
    def run(self, spec: Spec, ws: Workspace, *, resume: bool = False, force: bool = False) -> RunRecord:
        ws.create()
        with run_ledger(ws.root, run=run_binding().run or ws.root.name):
            if not ws.spec_path.is_file() or not resume:
                ws.write_json(ws.spec_path, spec)
            events = EventLog(ws.events_path)
            state = RunState.load_or_new(ws, resume=resume)
            rounds: list[RoundRecord] = self.reconcile_resume(spec, ws, events, state, resume=resume, force=force)
            ctx = self.build_context(spec, ws, events, state)
            runner = StageRunner(ws, events, state, frozen=bool(rounds))
            events.emit("run.start", track=self.track.value, language=spec.language.value, resume=resume,
                        agent=ctx.agent_id, planner=spec.backends.planner, judge=spec.backends.judge)
            stop = RunStatus.FAILED
            error = ""
            try:
                ctx.plan = runner.stage("plan", lambda: self._plan_stage(ctx), inputs=plan_stage_key(spec, self.track),
                                        model=self.plan_model)
                self.after_plan(ctx)
                run_stages(self, ctx, runner, self.stages)
                ctx.extra["prepared_commit"] = ws.head()
                stop = self._round_loop(ctx, rounds)
            except BudgetExceeded as e:
                events.emit("budget.exceeded", reason=e.reason)
                stop, error = RunStatus.BUDGET, e.reason
                self._salvage(ctx, rounds)
            except Exception as e:  # noqa: BLE001 — persist a FAILED record, then fail loud
                error = f"{type(e).__name__}: {e}"
                events.emit("run.failed", error=error, traceback=traceback.format_exc()[-3000:])
                self._save_budget(ctx)
                state.status, state.error = RunStatus.FAILED, error
                state.save(ws)
                rec = self._record(ctx, rounds, RunStatus.FAILED, error=error)
                self.services.finalize_record(ws, rec)
                raise
            return self.finalise(ctx, rounds, stop, error=error)

    # ------------------------------------------------------------------ resume reconciliation
    def reconcile_resume(self, spec: Spec, ws: Workspace, events: EventLog, state: RunState,
                         *, resume: bool, force: bool) -> list[RoundRecord]:
        fp = spec_fingerprint(spec)
        stored = str(state.extra.get("spec_fingerprint") or "")
        if not resume:
            state.extra["spec_fingerprint"] = fp
            return []
        journal = load_round_journal(ws)
        if stored and stored != fp:
            if not force:
                raise SpecChanged(
                    f"spec.json for run {ws.root.name} no longer matches the spec its "
                    f"{len(journal)} recorded round(s) were built from (plan-input fingerprint "
                    f"{stored} != {fp}: prompt/track/language/constraints/references/planner). "
                    f"Resuming would record those rounds against the new spec. Fork a new run "
                    f"(`3dcodeverse make`), or resume with --force to archive the old rounds under "
                    f"rounds/pre_force/ and re-plan. Raising budget caps alone never trips this."
                )
            events.emit("resume.spec_changed", old_fingerprint=stored, new_fingerprint=fp,
                        archived_rounds=len(journal))
            state.stages.pop("plan", None)
        if force and (journal or ws.record_path.is_file()):
            archived = self._archive_pre_force(ws)
            events.emit("resume.archived", dest=str(archived), rounds=len(journal))
            journal = []
        state.extra["spec_fingerprint"] = fp
        kept: list[RoundRecord] = []
        dropped: list[int] = []
        for rec in journal:
            if dropped or not rec.commit or not ws.has_commit(rec.commit):
                dropped.append(rec.index)
            else:
                kept.append(rec)
        if dropped:
            events.emit("resume.dropped_rounds", rounds=dropped,
                        reason="round journal names commits git does not have")
        journal = kept
        restored = bool(journal) and self._needs_restore(ws, journal[-1].commit)
        if restored:
            ws.restore(journal[-1].commit)
            ws.commit(f"resume from the last round r{journal[-1].index:02d}")
        state.save(ws)
        events.emit("resume.reconciled", rounds=len(journal), dropped=dropped,
                    restored_last_round=journal[-1].index if restored else None, spec_fingerprint=fp)
        return journal


    @staticmethod
    def _archive_pre_force(ws: Workspace) -> Path:
        rounds_dir = ws.root / "rounds"
        dest, n = rounds_dir / "pre_force", 1
        while dest.exists():
            n += 1
            dest = rounds_dir / f"pre_force_{n}"
        dest.mkdir(parents=True, exist_ok=True)
        for p in sorted(rounds_dir.iterdir()):
            if p.is_file():
                p.rename(dest / p.name)
        if ws.record_path.is_file():
            ws.record_path.rename(dest / ws.record_path.name)
        return dest

    # ------------------------------------------------------------------ context
    def build_context(self, spec: Spec, ws: Workspace, events: EventLog, state: RunState) -> RunContext:
        settings = self._settings or get_settings()
        runtime = self._runtime or self.services.runtime(spec.language)
        budget = BudgetGuard(spec.budget, soft_fraction=self.soft_budget_fraction)
        snap = state.extra.get("budget_snapshot")
        if snap:
            # the active minutes keep counting, so a raised cap (--max-minutes / --rounds)
            # grants only the difference, never a fresh full cap
            budget.restore(BudgetSnapshot.model_validate(snap))
        policy = self._policy or RoundPolicy(max_rounds=spec.budget.max_rounds,
                                             judge_samples=max(1, int(settings.judge.samples or 1)))
        rubric = run_rubric(spec, self.rubric)
        ctx = RunContext(spec=spec, ws=ws, events=events, settings=settings, budget=budget, runtime=runtime,
                         services=self.services, state=state, policy=policy, track=self.track, rubric=rubric,
                         agent_id=spec.backends.generator,
                         agent=metered_agent(self._agent), model=metered_chat_model(self._model))
        ctx.contract_text = language_text(spec.language, "contract.md")
        ctx.tool_cards = self.services.tool_cards(self.track.value, spec.language.value)
        ctx.record_prompt("contract", ctx.contract_text)
        return ctx

    def make_judge(self, ctx: RunContext, *, n_samples: int | None = None) -> Any:
        """The main judge: the injected one, else an instance of the class ``judge_for`` picks for this track."""
        if self._judge is not None:
            return self._judge
        return self.services.judge(ctx.track, ctx.rubric, ctx.spec.backends.judge,
                                   ctx.policy.judge_samples if n_samples is None else n_samples,
                                   references=bool(ctx.spec.references))

    def after_plan(self, ctx: RunContext) -> None:
        """Bind judge / generator backends once the plan exists."""
        if ctx.judge is None:
            ctx.judge = self.make_judge(ctx)
        judge_hash = getattr(ctx.judge, "prompt_hash", "")
        if isinstance(judge_hash, str) and judge_hash:
            ctx.prompt_hashes["judge"] = judge_hash
        if ctx.single_shot:
            if ctx.model is None:
                ctx.model = self.services.chat_model(single_shot_model_id(ctx.agent_id))
        elif ctx.agent is None:
            ctx.agent = self.services.coding_agent(ctx.agent_id)

    def _plan_stage(self, ctx: RunContext) -> Plan:
        ctx.state.status = RunStatus.PLANNING
        ctx.state.save(ctx.ws)
        try:
            plan = run_planner(ctx.spec, ctx.spec.backends.planner, self.plan_model, ctx.ws,
                               model=metered_chat_model(self._planner_model), events=ctx.events, budget=ctx.budget)
        finally:
            self._save_budget(ctx)
        return plan

    # ------------------------------------------------------------------ stages
    def ensure_materialized(self, ctx: RunContext) -> None:
        """Materialise AGENTS.md/MCP config once per run for agent generators."""
        if ctx.single_shot:
            return
        kind = ctx.agent_kind
        if ctx.state.materialized_for == kind:
            return
        self.services.materialize(ctx.ws, agent_kind=kind, contract_md=self.agent_contract_md(ctx))
        ctx.state.materialized_for = kind
        ctx.state.save(ctx.ws)
        ctx.events.emit("workspace.materialized", agent_kind=kind)

    def agent_contract_md(self, ctx: RunContext) -> str:
        harness = load_text("system/harness_contract.md")
        return (harness + "\n\n" + ctx.contract_text).strip()

    # ------------------------------------------------------------------ round loop
    def _round_loop(self, ctx: RunContext, rounds: list[RoundRecord]) -> RunStatus:
        pipeline = self.make_pipeline()
        rejudged: set[int] = set()
        transport_retried: set[int] = set()

        def _stop(reason: RunStatus) -> RunStatus:
            ctx.events.emit("stop", reason=reason.value, rounds=len(rounds))
            return reason

        def _unjudged(rec: RoundRecord | None) -> bool:
            return rec is not None and rec.judgment is None and rec.build is not None and rec.build.ok

        while True:
            index = len(rounds)
            last = rounds[-1] if rounds else None
            if _unjudged(last) and last.index not in rejudged and ctx.budget.ok():
                rejudged.add(last.index)
                if rejudge_round(ctx, pipeline, last, previous=previous_verdict(rounds, last.index)):
                    self._save_budget(ctx)
            if index > ctx.policy.max_rounds:
                return _stop(RunStatus.JUDGE_UNAVAILABLE if _unjudged(last) else RunStatus.MAX_ROUNDS)
            if not ctx.budget.ok():
                if not rounds:
                    ctx.budget.check()
                return _stop(RunStatus.BUDGET)
            previous = previous_verdict(rounds, index)
            if last is None:
                ctx.state.status = RunStatus.GENERATING
                tasks, instructions, kind = self.baseline_tasks(ctx), [], "baseline"
            else:
                ctx.state.status = RunStatus.REFINING
                tasks, instructions = self.refine_tasks(ctx, last)
                kind = "refine"
                if not tasks:
                    # no gate error, no failed must-item, no judge plan: nothing to ask for.  When
                    # that is only because the judge never scored the round, say so.
                    return _stop(RunStatus.JUDGE_UNAVAILABLE if _unjudged(last) else RunStatus.NO_REFINE_TASKS)
            if last is not None and not ctx.policy.can_open(ctx.budget, single_shot=ctx.single_shot):
                ctx.events.emit("round.skipped_budget", round=index, left_s=round(ctx.budget.left_s(), 1),
                                need_s=ctx.policy.need_s(single_shot=ctx.single_shot))
                return _stop(RunStatus.BUDGET)
            ctx.state.save(ctx.ws)
            try:
                rec = run_round(ctx, index=index, kind=kind, tasks=tasks, pipeline=pipeline, instructions=instructions,
                                previous=previous, findings=rounds[-1].gates if rounds else (),
                                files_hint=ctx.runtime.expected_files(ctx.plan),
                                extra_notes=self.round_extra_notes(ctx))
            except RoundFailed as e:
                if e.quota:
                    ctx.events.emit("round.agent_quota", round=index, detail=str(e)[:500])
                    if index == 0:
                        raise
                    return _stop(RunStatus.AGENT_QUOTA)
                if index not in transport_retried and (e.transient or e.stalled):
                    transport_retried.add(index)
                    ctx.events.emit("round.transport_retry", round=index, stalled=e.stalled and not e.transient,
                                    detail=str(e)[:500])
                    continue
                if index == 0:
                    raise
                ctx.events.emit("round.no_change", round=index, detail=str(e)[:500])
                return _stop(RunStatus.NO_CHANGE)
            rounds.append(rec)
            self._save_budget(ctx)

    def _salvage(self, ctx: RunContext, rounds: list[RoundRecord]) -> None:
        """The budget stopped the run with work nobody has judged: give ONE bounded grace and run"""
        if ctx.plan is None:
            return
        last = rounds[-1] if rounds else None
        edits: list[str] = []
        if last is not None:
            if not last.commit:
                return
            edits = [f.path for f in ctx.ws.changed_files(last.commit) if f.path.split("/", 1)[0] in ("src", "public")]
            if not edits:
                return
        grace_min = max(5.0, ctx.spec.budget.max_minutes * 0.15)
        ctx.budget.grant_grace(minutes=grace_min)
        reason = ("no round completed before the clock stop" if last is None
                  else f"the clock stopped a refine round after it changed {len(edits)} file(s)")
        ctx.events.emit("budget.salvage", reason=reason, grace_minutes=round(grace_min, 1), files=edits[:20])
        index = len(rounds)
        try:
            if last is None and not self.prepare_salvage(ctx):
                ctx.events.emit("budget.salvage_skipped", reason="nothing buildable to salvage")
                return
            note = ("salvaged: the budget stopped the run before round 0" if last is None
                    else "salvaged: the budget stopped this round's sessions; their edits are judged as they stand")
            rec = run_round(ctx, index=index, kind="baseline" if last is None else "refine", tasks=[],
                            pipeline=self.make_pipeline(), instructions=[], previous=previous_verdict(rounds, index),
                            findings=last.gates if last is not None else (), files_hint=ctx.runtime.expected_files(ctx.plan),
                            extra_notes=[note, *self.round_extra_notes(ctx)])
        except Exception as e:  # noqa: BLE001 — the run is already stopping; never mask the budget stop
            log.warning("salvage round failed: %s", e)
            ctx.events.emit("budget.salvage_failed", error=f"{type(e).__name__}: {e}")
            return
        rounds.append(rec)
        self._save_budget(ctx)

    def _save_budget(self, ctx: RunContext) -> None:
        snap = ctx.budget.snapshot()
        ctx.state.extra["budget_snapshot"] = snap.model_dump(mode="json")
        ctx.state.save(ctx.ws)

    # ------------------------------------------------------------------ finalise
    def finalise(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, error: str = "") -> RunRecord:
        """Write the record.  The workspace ENDS AT THE LAST ROUND: a round the budget or a crash"""
        self._save_budget(ctx)
        last = rounds[-1] if rounds else None
        rebuild_err = ""
        rebuild_ok = False
        if last is not None and last.commit and self._needs_restore(ctx.ws, last.commit):
            ctx.ws.restore(last.commit)
            ctx.ws.commit(f"back to the last round r{last.index:02d}")
            try:
                build = ctx.runtime.build(ctx.ws, timeout_s=ctx.settings.limits.build_timeout_s)
                ctx.events.emit("finalise.rebuild", round=last.index, ok=build.ok)
                if not build.ok:
                    rebuild_err = f"{build.error_type or 'BuildFailed'}: {build.error_message}"[:300]
                else:
                    rebuild_ok = True
            except Exception as e:  # noqa: BLE001 — the round already built once; report, don't fail
                rebuild_err = f"{type(e).__name__}: {e}"[:300]
                ctx.events.emit("finalise.rebuild_failed", error=rebuild_err)
        if rebuild_err:
            ctx.extra["finalise_rebuild_failed"] = rebuild_err
            error = error or f"finalise rebuild failed: {rebuild_err}"
        elif rebuild_ok:
            ctx.extra["finalise_rebuild_failed"] = ""
        ctx.state.status, ctx.state.error = status, error
        self._save_budget(ctx)
        rec = self._record(ctx, rounds, status, error=error)
        self.services.finalize_record(ctx.ws, rec)
        ctx.events.emit("run.done", status=status.value, rounds=len(rounds),
                        last_score=last.score if last is not None else None,
                        cost_usd=round(rec.total_usage.cost_usd, 4), minutes=rec.minutes)
        return rec

    @staticmethod
    def _needs_restore(ws: Workspace, commit: str) -> bool:
        """src/ != ``commit``: HEAD moved on, OR an aborted round dirtied the tree"""
        if ws.head() != commit:
            return True
        try:
            return bool(ws.changed_files())
        except Exception as e:  # noqa: BLE001 — a git hiccup must not block finalise
            log.warning("changed_files failed in finalise: %s", e)
            return False

    def _record(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, error: str) -> RunRecord:
        extra: dict[str, Any] = {"rubric": ctx.rubric, "sampling": self.sampling(ctx.judge)}
        if ctx.extra.get("finalise_rebuild_failed"):
            extra["finalise_rebuild_failed"] = ctx.extra["finalise_rebuild_failed"]
        if ctx.extra.get("aborted_rounds"):
            extra["aborted_rounds"] = ctx.extra["aborted_rounds"]
        prior_extra, prior_hashes = self._prior_record_fields(ctx.ws)
        if ctx.extra.get("finalise_rebuild_failed", None) == "":
            prior_extra.pop("finalise_rebuild_failed", None)
        prior_aborted = prior_extra.get("aborted_rounds")
        for k, v in prior_extra.items():
            if k not in extra or extra[k] is None:
                extra[k] = v
        if isinstance(prior_aborted, list) and ctx.extra.get("aborted_rounds"):
            extra["aborted_rounds"] = prior_aborted + [a for a in ctx.extra["aborted_rounds"]
                                                       if a not in prior_aborted]
        hashes = {**prior_hashes, **dict(ctx.prompt_hashes)}
        return RunRecord(
            spec=ctx.spec, plan=ctx.plan, workspace=str(ctx.ws.root), status=status, rounds=rounds,
            total_usage=ledger_usage(load_ledger(ctx.ws.root)), steps=list(ctx.state.steps),
            environment={"python": platform.python_version(), "host": platform.node(), "track": self.track.value,
                         "language": ctx.language.value, "generator": ctx.agent_id},
            prompt_hashes=hashes, started_at=ctx.state.started_at,
            finished_at=datetime.now(UTC), error=error,
            extra=extra,
        )

    def sampling(self, judge: Any) -> dict[str, dict[str, Any]]:
        """``record.extra["sampling"]``: the knobs this run's API roles sampled with."""
        knobs = {k: getattr(judge, k) for k in ("temperature", "thinking", "n_samples")
                 if isinstance(getattr(judge, k, None), (int, float, str))}
        return {"planner": {"temperature": plan_temperature(self.track)}, **({"judge": knobs} if knobs else {})}

    @staticmethod
    def _prior_record_fields(ws: Workspace) -> tuple[dict[str, Any], dict[str, str]]:
        """``(extra, prompt_hashes)`` of the record.json a previous session wrote"""
        prior = read_json_or_none(ws.record_path)
        if prior is None:
            if ws.record_path.is_file():
                log.warning("prior record.json unreadable; post-hoc extra keys not carried over")
            return {}, {}
        extra = prior.get("extra")
        hashes = prior.get("prompt_hashes")
        return (dict(extra) if isinstance(extra, dict) else {},
                {str(k): str(v) for k, v in hashes.items()} if isinstance(hashes, dict) else {})
