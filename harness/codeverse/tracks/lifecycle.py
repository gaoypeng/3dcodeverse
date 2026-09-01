"""BaseTrack: the resumable plan → prepare → rounds → finalise lifecycle.

Concrete tracks (static / articulated / scene / graphics) override the hooks
(``plan_model``, ``make_pipeline``, ``prepare``, ``baseline_tasks``, the planner
knobs ``plan_template``/``plan_example``/``finalise_plan`` and the refine
scaffold's ``_refine_task``).  All bookkeeping — workspace, events, budget, run
state, stage cache, best tracking, stop policy, record — lives here once.
"""

from __future__ import annotations

import logging
import os
import platform
import traceback
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import GateReport
from codeverse.contracts.common import Track
from codeverse.contracts.plan import Plan
from codeverse.contracts.run import RoundRecord, RunRecord, RunStatus
from codeverse.contracts.spec import Spec
from codeverse.cost.context import run_binding
from codeverse.cost.instrument import run_ledger
from codeverse.orchestrator import (
    KIND_FOR_STRATEGY,
    BestSelector,
    BudgetExceeded,
    BudgetGuard,
    BudgetSnapshot,
    RefineTask,
    RoundPolicy,
    RunState,
    StageRunner,
    StopPolicy,
    StopReason,
    Strategy,
    TaskGroup,
    best_score,
    build_refine_instructions,
    hash_inputs,
    plan_refine_groups,
)
from codeverse.proc import EventLog
from codeverse.prompts import render
from codeverse.tracks.candidates import choose_best_round, replay_best_round, run_best_of_n
from codeverse.tracks.common import (
    RunContext,
    Services,
    cookbook_rel_for,
    language_contract,
    load_prompt_or,
)
from codeverse.tracks.generation import GenerationTask, single_shot_model_id
from codeverse.tracks.planner import default_event_stats, ensure_acceptance, normalise_names
from codeverse.tracks.planner import plan as run_planner
from codeverse.tracks.planner import plan_example as default_plan_example
from codeverse.tracks.prompting import (
    base_prompt_context,
    expected_files,
    file_for_target_factory,
    language_system_prompt,
    refine_inline_files,
)
from codeverse.tracks.repair import format_error_report
from codeverse.tracks.steps import (
    RoundFailed,
    RoundPipeline,
    failed_acceptance,
    load_round_journal,
    looks_transport,
    rejudge_round,
    run_round,
    sum_usage,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

REFERENCE_RUBRIC = "reference_v1"

#: surface-detail rounds a track that implements one gets by default.  ``RoundPolicy.detail_rounds``
#: is None (= this default) unless a caller chose a number; ``CV3D_DETAIL_ROUNDS`` overrides both
#: (0 = off — the A/B switch for docs/EVAL.md).
DEFAULT_DETAIL_ROUNDS = 1


def detail_round_budget(policy: RoundPolicy, supported: bool) -> int:
    """How many detail rounds THIS run gets: env > a chosen ``policy.detail_rounds`` > the track default.

    Until 2026-08-29 the third tier was skipped whenever ANY policy object was injected, so the
    economy/quality profiles (a policy for ``judge_samples`` alone) silently lost the static
    track's detail round."""
    raw = os.environ.get("CV3D_DETAIL_ROUNDS", "").strip()
    if raw.isdigit():
        return int(raw) if supported else 0
    if not supported:
        return 0
    if policy.detail_rounds is not None:
        return policy.detail_rounds
    return DEFAULT_DETAIL_ROUNDS


def _reconcile_billed_from_ledger(budget: BudgetGuard, ws: Workspace, events: EventLog) -> None:
    """Adopt the run ledger's total when it exceeds the restored snapshot.

    The snapshot is saved at boundaries; the ledger is appended per CALL, so a crash
    between a round's spend and its save loses that money from the snapshot but never
    from the ledger.  Taking the larger of the two makes resume charge for everything
    the provider actually billed — silently under-counting is how a resumed run walks
    past its ceiling.  Attempt rows stay excluded (the winner is already on the logical
    row), and so do subscription-backend rows: their ``cost_usd`` is notional, not money
    (the same ``bills_usd`` predicate the live path uses — ``BudgetGuard.charge``), so a
    codex/claude/agy run resumed offline keeps billed at $0.  A missing or unreadable
    ledger simply leaves the snapshot alone.  ``load_ledger(root)`` reads the one ledger
    the metered models/agents write, ``telemetry/cost.jsonl``; the root-level
    ``cost_ledger.jsonl`` leg went on 2026-08-30 (an audit of every run under $HOME found
    677 symlinks to that name and zero real files — ``LEDGER_NAME`` is the alias only)."""
    try:
        from codeverse.cost.billing import bills_usd
        from codeverse.cost.ledger import load_ledger

        billed = sum(float(r.cost_usd or 0.0) for r in load_ledger(ws.root) if bills_usd(r.backend))
    except Exception as e:  # noqa: BLE001 — accounting repair must never block a resume
        log.debug("ledger reconcile skipped: %s", e)
        return
    if billed > budget.billed_usd + 1e-9:
        events.emit("resume.billed_reconciled", snapshot_usd=round(budget.billed_usd, 6),
                    ledger_usd=round(billed, 6))
        budget.billed_usd = billed


_STATUS: dict[str, RunStatus] = {
    "pass": RunStatus.PASSED, "plateau": RunStatus.PLATEAU, "max_rounds": RunStatus.PLATEAU,
    "budget": RunStatus.BUDGET,
    # the two money stops (orchestrator.StopPolicy): the code is intact and the best
    # round is delivered — the run stopped because another round was not worth buying.
    "regression": RunStatus.PLATEAU, "diminishing_returns": RunStatus.PLATEAU,
    # judge outage (degraded/crashed verdicts even after a retry): the code is intact
    # and the best built round is delivered — a stop, not a failure.
    "judge_unavailable": RunStatus.PLATEAU,
}


def plan_stage_inputs(spec: Spec) -> dict[str, Any]:
    """Only the spec fields the PLAN depends on: hashing the whole Spec meant that
    raising ``budget.max_minutes`` in spec.json (the only way to continue a BUDGET-stopped
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


def spec_fingerprint(spec: Spec) -> str:
    """Identity of the spec THE PLAN depends on (hash of :func:`plan_stage_inputs`).

    Stamped into ``state.extra["spec_fingerprint"]`` on first run and verified by
    ``reconcile_resume``.  Budget raises — the sanctioned way to continue a
    BUDGET-stopped run — are outside ``plan_stage_inputs`` and never change it."""
    return hash_inputs(plan_stage_inputs(spec))


class SpecChanged(RuntimeError):
    """spec.json's plan inputs changed under an existing run.

    Resuming would pair the recorded rounds with a new spec/plan in record.json —
    fake prompt→code provenance.  Fork a new run, or resume with ``--force`` to
    archive the old rounds under ``rounds/pre_force/`` and re-plan."""


class BaseTrack:
    """Shared lifecycle.  Subclasses set ``track``, ``rubric``, ``plan_model``
    and parameterise the ONE planner loop / refine scaffold via the hook
    attributes below (``plan_template``, ``plan_example``, ``_refine_task`` …)."""

    track: Track
    rubric: str
    plan_model: type[Plan]
    generate_template: str = ""
    refine_template: str = ""
    #: planner knobs (GraphicsTrack: own template/example, T=0.5, 16k tokens)
    plan_template: str | None = None  # None → tracks/plan_<track>.j2
    plan_temperature: float = 0.4
    plan_max_output_tokens: int = 24000
    #: refine fan-out (graphics is always ONE whole-program task)
    allow_refine_fanout: bool = True
    #: does this track implement ``detail_tasks``?  When False the round policy's detail
    #: budget is zeroed for the run, so the stop policy never offers a round the track
    #: cannot build (see ``orchestrator.detail_blocked``).
    supports_detail_round: bool = False
    #: share of the run budget the BASELINE may use (1.0 = no soft cap).  The scene
    #: track lowers it so the refine rounds always inherit money and minutes.
    soft_budget_fraction: float = 1.0

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
    def make_pipeline(self) -> RoundPipeline:
        raise NotImplementedError

    def prepare(self, ctx: RunContext, runner: StageRunner) -> None:
        """Stages between plan and baseline (skeleton; scene assets/env/zones/assemble)."""
        self.stage_skeleton(ctx, runner)

    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        raise NotImplementedError

    def system_prompt(self, ctx: RunContext) -> str:
        return language_system_prompt(ctx.language, tools=not ctx.single_shot)

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return []

    def round_extra_notes(self, ctx: RunContext) -> list[str]:
        """Notes folded into every round record (e.g. 'the baseline was degraded')."""
        return list(ctx.extra.get("degraded") or [])

    def prepare_salvage(self, ctx: RunContext) -> bool:
        """Make the workspace buildable after a stage tripped the budget before
        round 0.  Return False when the track has nothing to salvage.  The default
        says yes exactly when best-of-N adopted a winner (``rounds/candidates.json``
        is written immediately before adoption): the paid, buildable candidate must
        not be thrown away by a ceiling trip at the r00 boundary.  A bare skeleton
        still returns False."""
        return (ctx.ws.root / "rounds" / "candidates.json").is_file()

    # ---- planner hooks (tracks/planner.py runs the one loop)
    def plan_example(self, spec: Spec) -> dict[str, Any]:
        """Worked example JSON for the plan system prompt."""
        return default_plan_example(spec.track)

    def finalise_plan(self, plan_obj: Any, spec: Spec) -> Any:
        """Post-validation fixup (names + deterministic acceptance items)."""
        return ensure_acceptance(normalise_names(plan_obj), spec)

    def plan_event_stats(self, plan_obj: Any) -> dict[str, Any]:
        """Extra ``plan.done`` payload (n_parts/n_zones… per track)."""
        return default_event_stats(plan_obj)

    # ---- refine hooks (the scaffold below is shared; tracks fill in the task)
    def refine_file_for_target(self, ctx: RunContext) -> Any:
        """``target → [files]`` mapper for refine tasks (None = whole-object language)."""
        return file_for_target_factory(ctx)

    def extra_refine_tasks(self, ctx: RunContext, last: RoundRecord) -> Sequence[Any]:
        """Harness-derived tasks prepended to the judge's (e.g. reference IoU)."""
        return ()

    def generate_context(self, ctx: RunContext, **extra: Any) -> dict[str, Any]:
        """Template context for ``generate_template`` (baseline / rebuild prompts)."""
        extra.setdefault("expected_files", expected_files(ctx))
        return base_prompt_context(ctx, **extra)

    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        raise NotImplementedError

    def detail_tasks(self, ctx: RunContext, last: RoundRecord, index: int) -> tuple[list[GenerationTask], list[str]]:
        """The SURFACE-DETAIL round (``kind='detail'``): bevels, seams, fasteners, wear —
        no change to the silhouette, the placement or the part list.

        Offered by the stop policy only once the structure gates are clean
        (``orchestrator.detail_blocked``); tracks that do not implement it set
        ``supports_detail_round = False`` and are never offered the round."""
        return [], []

    # ------------------------------------------------------------------ refine scaffold
    def refine_tasks(self, ctx: RunContext, last: RoundRecord, history: Sequence[RoundRecord],
                     *, strategy: Strategy = "same") -> tuple[list[GenerationTask], list[str]]:
        """index → rebuild guard → instructions → groups → per-track ``_refine_task``.

        ``strategy="switch"`` is what the loop asks for after a regression: the same
        evidence, but ONE whole-artifact task instead of the per-part set that just
        made the score worse (docs/COST.md §5 — repeating the shape is what the 19
        regression rounds did)."""
        index = len(history)
        if last.build is None or not last.build.ok:
            return [self._rebuild_task(ctx, last, index)], ["rebuild: previous round did not build"]
        if strategy == "detail":
            gen_tasks, lines = self.detail_tasks(ctx, last, index)
            if gen_tasks:
                ctx.events.emit("refine.planned", round=index, n_tasks=len(lines), n_groups=len(gen_tasks),
                                parallel=len(gen_tasks) > 1, strategy="detail",
                                targets=[t.files_hint for t in gen_tasks])
                return gen_tasks, lines
            return [], []  # the track offered no detail work: let the loop stop as it meant to
        tasks = build_refine_instructions(last.judgment, last.gates, failed_acceptance(ctx, last.judgment), ctx.plan,
                                          file_for_target=self.refine_file_for_target(ctx),
                                          max_tasks=ctx.policy.max_refine_tasks,
                                          extra=self.extra_refine_tasks(ctx, last))
        if not tasks:
            return [], []
        if strategy == "switch":
            tasks = [rewrite_task(last, best_score(history))] + tasks
            groups, parallel = [TaskGroup(tasks=tasks, files=[])], False
        else:
            groups, parallel = plan_refine_groups(tasks, allow_fanout=self.allow_refine_fanout,
                                                  parallel_min_tasks=ctx.policy.parallel_min_tasks)
        gen_tasks = [self._refine_task(ctx, g, last, index, parallel=parallel) for g in groups]
        ctx.events.emit("refine.planned", round=index, n_tasks=len(tasks), n_groups=len(groups), parallel=parallel,
                        strategy=strategy, targets=[g.targets for g in groups])
        return gen_tasks, [t.line() for t in tasks]

    def _rebuild_task(self, ctx: RunContext, last: RoundRecord, index: int) -> GenerationTask:
        """Regenerate after a failed build: the generate prompt + the error report."""
        files = self.round_files_hint(ctx)
        lint = next((g for g in last.gates if g.gate.startswith("lint")), GateReport(gate="lint", passed=True))
        report = format_error_report(last.build, lint, ctx.cookbook_text) if last.build else "build did not run"
        prompt = render(self.generate_template, **self.generate_context(
            ctx, skeleton_files=refine_inline_files(ctx, files, scoped=False), previous_error=report))
        return GenerationTask(label="rebuild", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="rebuild", temperature=0.7, thinking="high")

    # ------------------------------------------------------------------ public API
    def run(self, spec: Spec, ws: Workspace, *, resume: bool = False, force: bool = False) -> RunRecord:
        ws.create()
        # the run's own ledger even when nobody opened one (a test, a script): the CLI /
        # bench open the same file first and run_ledger nests, restoring theirs on exit.
        # Keep THEIR name when they bound one — a compare_backends cell is "<prompt>:<arm>",
        # and rebinding it to the directory ("run") collapsed every cell into one bucket.
        with run_ledger(ws.root, run=run_binding().run or ws.root.name):
            if not ws.spec_path.is_file() or not resume:
                ws.write_json(ws.spec_path, spec)
            events = EventLog(ws.events_path)
            state = RunState.load_or_new(ws, resume=resume)
            # BEFORE build_context (which restores the budget snapshot from state.extra):
            # make the state agree with the durable round journal, verify spec identity,
            # and take the reconciled rounds as the loop's initial history — so the
            # FAILED/BUDGET handlers below serialize the real rounds, never [].
            rounds: list[RoundRecord] = self.reconcile_resume(spec, ws, events, state, resume=resume, force=force)
            ctx = self.build_context(spec, ws, events, state)
            runner = StageRunner(ws, events, state)
            events.emit("run.start", track=self.track.value, language=spec.language.value, resume=resume,
                        agent=ctx.agent_id, planner=spec.backends.planner, judge=spec.backends.judge)
            stop: StopReason | str = "failed"
            error = ""
            try:
                ctx.plan = runner.stage("plan", lambda: self._plan_stage(ctx), inputs={"spec": plan_stage_inputs(spec), "track": self.track.value},
                                        model=self.plan_model)
                self.after_plan(ctx)
                self.prepare(ctx, runner)
                stop = self._round_loop(ctx, rounds)
            except BudgetExceeded as e:
                events.emit("budget.exceeded", reason=e.reason, spent_usd=round(e.spent_usd, 4))
                stop, error = "budget", e.reason
                self._salvage_baseline(ctx, rounds)
            except Exception as e:  # noqa: BLE001 — persist a FAILED record, then fail loud
                error = f"{type(e).__name__}: {e}"
                events.emit("run.failed", error=error, traceback=traceback.format_exc()[-3000:])
                self._save_budget(ctx)  # mid-round charges must survive for resume
                state.status, state.error = RunStatus.FAILED, error
                state.save(ws)
                rec = self._record(ctx, rounds, RunStatus.FAILED, error=error, stop_reason="failed")
                self.services.finalize_record(ws, rec)
                raise
            status = _STATUS.get(stop, RunStatus.FAILED)
            rec = self.finalise(ctx, rounds, status, stop_reason=str(stop), error=error)
            return rec

    # ------------------------------------------------------------------ resume reconciliation
    def reconcile_resume(self, spec: Spec, ws: Workspace, events: EventLog, state: RunState,
                         *, resume: bool, force: bool) -> list[RoundRecord]:
        """Make ``run_state.json`` agree with the on-disk round journal before anything runs.

        The journal (``rounds/rNN.json`` + its git commit, written at the end of every
        round) is the durable record; the state file is a cache of it that a crash can
        leave stale — round persisted, ``mark_round_done``/best never saved.  On resume:

        1. **Spec identity.**  ``state.extra["spec_fingerprint"]`` (hash of
           :func:`plan_stage_inputs`, stamped on first run) must match the spec we are
           resuming with; budget raises are outside the fingerprint by construction.
           On a mismatch a plain resume raises :class:`SpecChanged`; ``--force``
           emits ``resume.spec_changed`` and archives the old journal + ``record.json``
           to ``rounds/pre_force/`` — old rounds are never paired with a new spec/plan.
        2. **Journal integrity.**  Trailing rounds whose commit git does not have are
           dropped (half-written journal), with a ``resume.dropped_rounds`` event.
        3. **Rebuild.**  ``completed_rounds``/``round_commits`` come from the journal;
           when the state disagrees with it (rounds it never promoted, a best pointing
           at the wrong commit) the best is recomputed with :func:`replay_best_round`,
           which re-walks the journal applying the pairwise verdicts the rounds stored
           — never buying a new one, never reversing one already paid for.
           A state that already agrees keeps its best untouched.

        Returns the loop's initial round history.  A fresh (non-resume) run only
        stamps the fingerprint and starts empty."""
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
                    f"{stored} != {fp}: prompt/track/language/constraints/references/planner/seed). "
                    f"Resuming would record those rounds against the new spec. Fork a new run "
                    f"(`3dcv make`), or resume with --force to archive the old rounds under "
                    f"rounds/pre_force/ and re-plan. Raising budget caps alone never trips this."
                )
            events.emit("resume.spec_changed", old_fingerprint=stored, new_fingerprint=fp,
                        archived_rounds=len(journal))
            archived = self._archive_pre_force(ws)
            events.emit("resume.archived", dest=str(archived))
            journal = []
            state.stages.pop("plan", None)  # the plan must be rebuilt from the edited spec
            state.completed_rounds, state.round_commits, state.current_round = [], {}, 0
            state.best_round, state.best_commit, state.best_score = None, "", None
        state.extra["spec_fingerprint"] = fp
        kept: list[RoundRecord] = []
        dropped: list[int] = []
        for rec in journal:
            if dropped or not rec.commit or not ws.has_commit(rec.commit):
                dropped.append(rec.index)  # half-written tail: rNN.json without its commit
            else:
                kept.append(rec)
        if dropped:
            events.emit("resume.dropped_rounds", rounds=dropped,
                        reason="round journal names commits git does not have")
        journal = kept
        journal_commits = {r.index: r.commit for r in journal}
        stale = (state.completed_rounds != [r.index for r in journal]
                 or {int(k): v for k, v in state.round_commits.items()} != journal_commits)
        if stale:
            state.completed_rounds = [r.index for r in journal]
            state.round_commits = dict(journal_commits)
            state.current_round = len(journal)
        best = state.best_round
        best_invalid = best is not None and (best >= len(journal) or journal[best].commit != state.best_commit)
        # a round the journal has but best selection never saw: the state can look
        # perfectly consistent (completed_rounds and commits agree) and still name a
        # stale best, which is then restored and DELIVERED over the newer paid round
        unranked = bool(journal) and state.best_considered_through < journal[-1].index
        best_changed = False
        # an empty journal used to have its own arm; it is subsumed — with journal == []
        # this condition is always true (best_invalid when a best is set, `best is None`
        # otherwise) and replay_best_round([]) is None, which clears the best the same
        # way.  The only divergence, blanking a stale best_commit while best_round is
        # None, is unobservable: no reader touches best_commit without best_round.
        if stale or best_invalid or unranked or best is None:
            pick = replay_best_round(journal)
            if pick is None:
                best_changed = best is not None
                state.best_round, state.best_commit, state.best_score = None, "", None
            elif pick != best or journal[pick].commit != state.best_commit:
                state.update_best(pick, journal[pick].commit, journal[pick].score)
                best_changed = True
        if journal:
            state.best_considered_through = journal[-1].index  # every journal round is now ranked
        state.save(ws)
        events.emit("resume.reconciled", rounds=len(journal), dropped=dropped, state_was_stale=stale,
                    unranked_rounds=unranked, best_round=state.best_round, best_score=state.best_score,
                    best_changed=best_changed, spec_fingerprint=fp)
        return journal


    @staticmethod
    def _archive_pre_force(ws: Workspace) -> Path:
        """Move the old journal + record.json under ``rounds/pre_force/`` (never delete
        evidence; a second --force lands in ``pre_force_2/`` and so on)."""
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
            # full guard state: money, calls, buckets AND active minutes keep counting,
            # so a raised cap (--max-minutes / --rounds) grants only the difference,
            # never a fresh full cap.
            budget.restore(BudgetSnapshot.model_validate(snap))
        _reconcile_billed_from_ledger(budget, ws, events)
        policy = self._policy or RoundPolicy(max_rounds=spec.budget.max_rounds)
        policy = replace(policy, n_candidates=self._resolve_candidates(spec, settings), judge_model=spec.backends.judge,
                         detail_rounds=detail_round_budget(policy, self.supports_detail_round))
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

    def _resolve_candidates(self, spec: Spec, settings: Settings) -> int:
        """Best-of-N width: constructor (CLI --candidates) > ``spec.options.candidates``
        (the persisted carrier — spec.json travels with the run) > settings default."""
        n = self._n_candidates
        if n is None:
            n = spec.options.candidates
        if n is None:
            n = getattr(settings, "default_candidates", 1)
        return max(1, int(n or 1))

    def make_judge(self, ctx: RunContext, *, n_samples: int | None = None) -> Any:
        """The main judge: injected → reference / likeness judge when the spec has images → rubric VLM judge."""
        if self._judge is not None:
            return self._judge
        n_samples = ctx.policy.judge_samples if n_samples is None else n_samples
        if ctx.spec.references:
            if ctx.track in (Track.GRAPHICS, Track.SCENE):  # nothing to silhouette-match: likeness only
                return self.services.likeness_judge(ctx.spec.backends.judge, n_samples=n_samples, rubric=ctx.rubric)
            return self.services.reference_judge(ctx.spec.backends.judge, n_samples=n_samples, rubric=ctx.rubric)
        return self.services.judge(ctx.rubric, ctx.spec.backends.judge, n_samples=n_samples)

    def after_plan(self, ctx: RunContext) -> None:
        """Bind judge / generator backends once the plan exists (rubric threshold → policy target)."""
        if ctx.judge is None:
            ctx.judge = self.make_judge(ctx)
        # provenance: the judge protocol (role prompt + rig rules + wire schema) is hashed
        # like the generator prompts, so a judge-prompt edit is visible in record.json
        judge_hash = getattr(ctx.judge, "prompt_hash", "")
        if isinstance(judge_hash, str) and judge_hash:
            ctx.prompt_hashes["judge"] = judge_hash
        # the rubric's pass threshold is the stop target whenever the rubric states one —
        # for an injected policy too (the CLI used to re-derive it; one binding now)
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
        try:
            plan = run_planner(ctx.spec, ctx.spec.backends.planner, self.plan_model, ctx.ws, model=self._planner_model,
                               events=ctx.events, budget=ctx.budget, runtime=ctx.runtime, **self._plan_kwargs(ctx.spec))
        finally:
            self._save_budget(ctx)  # charged on success AND PlanningError
        return plan

    def _plan_kwargs(self, spec: Spec) -> dict[str, Any]:
        return {"template": self.plan_template, "example": self.plan_example(spec),
                "temperature": self.plan_temperature, "max_output_tokens": self.plan_max_output_tokens,
                "finalise": lambda p: self.finalise_plan(p, spec), "event_stats": self.plan_event_stats}

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
        kind = ctx.agent_kind
        if ctx.state.materialized_for == kind:
            return
        self.services.materialize(ctx.ws, agent_kind=kind, contract_md=self.agent_contract_md(ctx), cookbook_rel=ctx.cookbook_rel,
                                  spatial_tools=True)
        ctx.state.materialized_for = kind
        ctx.state.save(ctx.ws)
        ctx.events.emit("workspace.materialized", agent_kind=kind)

    def agent_contract_md(self, ctx: RunContext) -> str:
        harness = load_prompt_or("system/harness_contract.md", "")
        return (harness + "\n\n" + ctx.contract_text).strip()

    # ------------------------------------------------------------------ round loop
    def _round_loop(self, ctx: RunContext, rounds: list[RoundRecord]) -> StopReason:
        self.ensure_materialized(ctx)
        pipeline = self.make_pipeline()
        stop_policy = StopPolicy(ctx.policy)
        rejudged: set[int] = set()
        transport_retried: set[int] = set()
        while True:
            decision = stop_policy.evaluate(rounds, budget_ok=ctx.budget.ok())
            if decision.stop:
                ctx.events.emit("stop", rounds=len(rounds), best=ctx.state.best_round, **decision.event())
                return decision.reason
            if decision.strategy == "switch":
                # the last round regressed by more than the judge's noise: one round of a
                # DIFFERENT shape (whole-artifact rewrite), never another of the same.
                ctx.events.emit("strategy.switch", round=len(rounds), reason="regression", detail=decision.detail)
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
                        self._promote_best(ctx, rounds, last.index)
                        continue  # decide() re-runs with the recovered score
                tasks, instructions = self.refine_tasks(ctx, last, rounds, strategy=decision.strategy)
                kind = KIND_FOR_STRATEGY.get(decision.strategy, "refine")
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
                                    previous=previous, files_hint=self.round_files_hint(ctx),
                                    extra_notes=self.round_extra_notes(ctx), previous_best=best_score(rounds))
            except RoundFailed as e:
                if index not in transport_retried and looks_transport(str(e)):
                    # A vendor-CLI crash / 503 storm / dropped socket is not an agent
                    # verdict — the session never really happened.  Re-run the SAME
                    # round once, baseline included (rc=247 alone killed three whole
                    # runs on 2026-08-29), before letting the failure mean anything.
                    # ``continue`` re-enters through StopPolicy, so the budget and
                    # round ceilings still guard the retry.
                    transport_retried.add(index)
                    ctx.events.emit("round.transport_retry", round=index, detail=str(e)[:500])
                    continue
                if index == 0:
                    raise
                # A refine round in which no task changed any file is not a crash: the
                # agents judged the requested edits unnecessary (or bailed).  Treat it as
                # a plateau signal so the best round so far is delivered.
                ctx.events.emit("round.no_change", round=index, detail=str(e)[:500])
                ctx.events.emit("stop", reason="no_change", rounds=len(rounds), best=ctx.state.best_round)
                return "plateau"
            rounds.append(rec)
            # ONE save, AFTER the best is chosen — never a durable "round done" with a
            # not-yet-updated best.  Saving the round first (an earlier attempt at
            # shrinking the paid-pairwise crash window) made a crash in that window look
            # SELF-CONSISTENT to reconcile_resume — journal and completed_rounds agreed,
            # so it kept the stale best and delivered the worse round (r0=0.5 kept over
            # r1=0.7).  Crashing before this save leaves state behind the journal, which
            # reconcile detects (stale) and repairs by re-ranking; that is the safe side.
            ctx.state.mark_round_done(index, rec.commit)
            self._promote_best(ctx, rounds, index)

    def _promote_best(self, ctx: RunContext, rounds: list[RoundRecord], index: int) -> None:
        """Rank round ``index`` in (pairwise tie-break included), promote the best, save."""
        best = choose_best_round(ctx, rounds, BestSelector(), index)
        ctx.state.best_considered_through = index  # this round HAS been ranked
        if best is not None and ctx.state.update_best(best, rounds[best].commit, rounds[best].score):
            ctx.events.emit("best.updated", round=best, score=rounds[best].score)
        self._save_budget(ctx)

    def _salvage_baseline(self, ctx: RunContext, rounds: list[RoundRecord]) -> None:
        """A stage tripped the budget BEFORE round 0 ever ran (the greenhouse scene:
        the zone fan-out crossed the ceiling, so the run finished with no build, no
        render, no judge and no score at all).

        The workspace still holds buildable code, and build → render → judge is a
        fraction of a generation session, so grant an explicit one-off grace and
        deliver ONE no-generation round.  Any failure here is swallowed: the run is
        already stopping on budget."""
        if rounds or ctx.state.completed_rounds or ctx.plan is None:
            return  # nothing was built yet (the PLAN itself blew the budget) → nothing to salvage
        grace_min = max(5.0, ctx.spec.budget.max_minutes * 0.15)
        ctx.budget.grant_grace(minutes=grace_min)
        ctx.events.emit("budget.salvage", reason="no round completed before the clock stop",
                        grace_minutes=round(grace_min, 1))
        try:
            if not self.prepare_salvage(ctx):
                ctx.events.emit("budget.salvage_skipped", reason="nothing buildable to salvage")
                return
            rec = run_round(ctx, index=0, kind="baseline", tasks=[], pipeline=self.make_pipeline(), instructions=[],
                            previous=None, files_hint=self.round_files_hint(ctx),
                            extra_notes=["salvaged: the budget stopped the run before round 0", *self.round_extra_notes(ctx)])
        except Exception as e:  # noqa: BLE001 — the run is already stopping; never mask the budget stop
            log.warning("salvage round failed: %s", e)
            ctx.events.emit("budget.salvage_failed", error=f"{type(e).__name__}: {e}")
            return
        rounds.append(rec)
        ctx.state.mark_round_done(0, rec.commit)
        self._promote_best(ctx, rounds, 0)

    def _save_budget(self, ctx: RunContext) -> None:
        snap = ctx.budget.snapshot()
        ctx.state.extra["budget_snapshot"] = snap.model_dump(mode="json")
        ctx.state.save(ctx.ws)

    # ------------------------------------------------------------------ finalise
    def finalise(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, stop_reason: str, error: str = "") -> RunRecord:
        self._save_budget(ctx)  # aborted-round / stage charges must survive for resume
        best = ctx.state.best_round
        rebuild_err = ""
        rebuild_ok = False
        if best is not None and best < len(rounds) and rounds[best].commit and self._needs_restore(ctx, rounds[best].commit):
            ctx.ws.restore(rounds[best].commit)
            ctx.ws.commit(f"restore best round r{best:02d}")
            try:
                build = ctx.runtime.build(ctx.ws, timeout_s=ctx.settings.limits.build_timeout_s)
                ctx.events.emit("finalise.rebuild", round=best, ok=build.ok)
                if not build.ok:  # the runtime invalidated the canonical artifact FIRST — it is gone
                    rebuild_err = f"{build.error_type or 'BuildFailed'}: {build.error_message}"[:300]
                else:
                    rebuild_ok = True
            except Exception as e:  # noqa: BLE001 — the best round already built once; report, don't fail
                rebuild_err = f"{type(e).__name__}: {e}"[:300]
                ctx.events.emit("finalise.rebuild_failed", error=rebuild_err)
        if rebuild_err:
            # keep the earned status + scores; the record says the artifact is gone
            ctx.extra["finalise_rebuild_failed"] = rebuild_err
            error = error or f"finalise rebuild failed: {rebuild_err}"
        elif rebuild_ok:
            # a rebuild that RAN and succeeded clears the stale flag a prior failed finalise
            # left (the prior-record merge would otherwise carry it forward); no-rebuild
            # resumes keep the prior flag — the artifact may still be the missing one
            ctx.extra["finalise_rebuild_failed"] = ""
        if rounds and not rebuild_err and self._texture_wanted(ctx):
            self._texture_pass(ctx)
        ctx.state.status, ctx.state.stop_reason, ctx.state.error = status, stop_reason, error
        self._save_budget(ctx)  # saves state too — AFTER the texture pass charged
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

    @staticmethod
    def _texture_wanted(ctx: RunContext) -> bool:
        """``codeverse.texturing.run.texture_requested`` is the single owner of this
        decision (spec.options.texture / the legacy tag) — the same predicate the
        ``texture_pass`` spatial tool asks, so a ``texture: false`` run cannot be
        textured from inside an agent session either."""
        try:
            from codeverse.texturing.run import texture_requested

            return texture_requested(ctx.spec)
        except Exception as e:  # noqa: BLE001 — texturing is optional; never block finalise
            log.debug("texture_requested unavailable: %s", e)
            return bool(ctx.spec.options.texture)

    def _texture_pass(self, ctx: RunContext) -> None:
        """Optional post-hoc texture pass (``Spec.options.texture``).  Additive: any
        failure is logged + emitted, never fatal.

        Its usage goes through the SAME guard as everything else (stage ``texture``,
        role ``image``) so it lands in ``record.total_usage`` and in the ledger — four
        post-hoc passes on ``furn_hard_rolltop_desk`` ($0.656) were invisible to both
        (docs/COST.md §6).  It is charged without enforcement: the run is finalising,
        the tiles are already paid for, and raising here would throw away the pack."""
        if self._already_textured(ctx):
            # An agent can call the `texture_pass` spatial tool during a round; the
            # finalise hook must not buy the same pack a second time (a texture pass
            # is $0.10-0.30 and the input GLB is unchanged).
            ctx.events.emit("texture.skipped", reason="already_textured_this_artifact")
            return
        try:
            from codeverse.texturing.run import texture_pass

            trep = texture_pass(ctx.ws, ctx.spec, ctx.plan, model_id=ctx.spec.backends.planner, judge=True,
                                judge_model_id=ctx.spec.backends.judge, rubric=self.rubric, events=ctx.events,
                                update_record=False)
        except Exception as e:  # noqa: BLE001 — texturing is a derived asset pack, never run-fatal
            log.warning("texture pass failed: %s", e)
            ctx.events.emit("texture.failed", error=f"{type(e).__name__}: {e}")
            return
        ctx.budget.add(trep.usage, stage="texture")
        ctx.extra["texturing"] = trep.summary()
        if not ctx.budget.ok():
            # visible, but NOT `budget.exceeded`: that event means "the loop stopped",
            # and a texture pass runs after the loop has already finished.
            ctx.events.emit("budget.overrun", stage="texture",
                            reason=f"the texture pass took the run past its wall-clock ceiling "
                                   f"(spent ${ctx.budget.spent.cost_usd:.3f})",
                            spent_usd=round(ctx.budget.spent.cost_usd, 4))

    @staticmethod
    def _already_textured(ctx: RunContext) -> bool:
        """True when a texture pass already ran against the current ``object.glb``.

        The report (``artifacts/texturing.json``) records the GLB it consumed; the
        hook compares content hashes so a texture pass from an EARLIER round (whose
        GLB has since been rebuilt) does not suppress the final one."""
        import hashlib
        import json as _json

        report = ctx.ws.artifacts / "texturing.json"
        glb = ctx.ws.artifacts / "object.glb"
        if not report.is_file() or not glb.is_file():
            return False
        try:
            data = _json.loads(report.read_text())
            prev = Path(str(data.get("glb_in") or ""))
            if not prev.is_file():
                return False
            digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
            return digest(prev) == digest(glb)
        except Exception:  # noqa: BLE001 — a malformed report must not block texturing
            return False

    def _record(self, ctx: RunContext, rounds: list[RoundRecord], status: RunStatus, *, error: str, stop_reason: str) -> RunRecord:
        best = ctx.state.best_round
        baseline = rounds[0].score if rounds else None
        final = rounds[best].score if best is not None and best < len(rounds) else None
        total = sum_usage(rounds)
        # planner, aborted rounds, retried sessions, the texture pass: all in the guard,
        # none of them in ``rounds`` — the guard is the honest total (docs/COST.md §6).
        if ctx.budget.spent.cost_usd > total.cost_usd:
            total = ctx.budget.spent
        cands = ctx.ws.root / "rounds" / "candidates.json"  # best-of-N summary: the file is the one copy
        extra: dict[str, Any] = {"stop_reason": stop_reason, "rubric": ctx.rubric, "budget": ctx.budget.summary(),
                                 "n_candidates": ctx.policy.n_candidates,
                                 "candidates": ctx.ws.read_json(cands) if cands.is_file() else None,
                                 "cost_by_stage": ctx.budget.stage_summary()}
        if ctx.extra.get("texturing"):
            extra["texturing"] = ctx.extra["texturing"]
        if ctx.extra.get("finalise_rebuild_failed"):
            extra["finalise_rebuild_failed"] = ctx.extra["finalise_rebuild_failed"]
        if ctx.extra.get("aborted_rounds"):
            # rounds the budget (or a crash) cut after the money was spent: they are not
            # in ``rounds``, so name them here and keep their dollars in total_usage.
            extra["aborted_rounds"] = ctx.extra["aborted_rounds"]
        # A resume rewrite must not drop what earlier sessions / other packages put on
        # the record post-hoc (flywheel captions, texturing/run.record_texturing, prior
        # sessions' aborted_rounds, prompt hashes of stages not re-executed): seed from
        # the prior record — unknown keys preserved, this session's values win.
        prior_extra, prior_hashes = self._prior_record_fields(ctx.ws)
        if ctx.extra.get("finalise_rebuild_failed", None) == "":  # rebuilt successfully this session
            prior_extra.pop("finalise_rebuild_failed", None)
        prior_aborted = prior_extra.get("aborted_rounds")
        for k, v in prior_extra.items():
            if k not in extra or extra[k] is None:
                extra[k] = v
        if isinstance(prior_aborted, list) and ctx.extra.get("aborted_rounds"):
            extra["aborted_rounds"] = prior_aborted + [a for a in ctx.extra["aborted_rounds"]
                                                       if a not in prior_aborted]
        return RunRecord(
            spec=ctx.spec, plan=ctx.plan, workspace=str(ctx.ws.root), status=status, rounds=rounds, best_round=best,
            baseline_score=baseline, final_score=final, total_usage=total,
            environment={"python": platform.python_version(), "host": platform.node(), "track": self.track.value,
                         "language": ctx.language.value, "generator": ctx.agent_id},
            prompt_hashes={**prior_hashes, **dict(ctx.prompt_hashes)}, started_at=ctx.state.started_at,
            finished_at=datetime.now(UTC), error=error,
            extra=extra,
        )

    @staticmethod
    def _prior_record_fields(ws: Workspace) -> tuple[dict[str, Any], dict[str, str]]:
        """``(extra, prompt_hashes)`` of the record.json a previous session wrote
        (empty when there is none / it cannot be read — never run-fatal)."""
        if not ws.record_path.is_file():
            return {}, {}
        try:
            prior = ws.read_json(ws.record_path)
        except Exception as e:  # noqa: BLE001 — a corrupt old record must not block finalise
            log.warning("prior record.json unreadable; post-hoc extra keys not carried over: %s", e)
            return {}, {}
        extra = prior.get("extra")
        hashes = prior.get("prompt_hashes")
        return (dict(extra) if isinstance(extra, dict) else {},
                {str(k): str(v) for k, v in hashes.items()} if isinstance(hashes, dict) else {})


def rewrite_task(last: RoundRecord, best: float | None) -> RefineTask:
    """The instruction that turns a repeat into a change of approach."""
    was = f"{last.score:.3f}" if last.score is not None else "no score"
    peak = f"{best:.3f}" if best is not None else "the earlier best"
    return RefineTask(
        target="overall", kind="rewrite", priority=0, source="judge",
        instruction=(f"The previous round made the artifact WORSE ({was} against {peak}) — its edits are not the "
                     "way forward. Do NOT repeat them part by part: re-read the plan and the failures below, then "
                     "rewrite the whole artifact coherently, keeping only what the evidence shows was already right."),
    )
