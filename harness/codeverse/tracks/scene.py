"""SceneTrack: plan → skeleton → (assets ∥ env) → zones → assemble → rounds.

Language: scene_threejs.  Generation is staged (each stage cached by
``StageRunner`` for resume); the round loop then builds (probe + shaders),
renders authored cameras + orbit at t=0 and t=1.5, judges with ``scene_v1``
(console/shader errors become gate ERRORS) and dispatches refine tasks per
zone / asset / env / camera, in parallel when file-disjoint.

Cost/latency shaping (the baseline used to eat the whole budget, leaving the
refine rounds nothing):

* assets are single-shot by default (``scene_assets``);
* **small zones are batched** — a zone that places ≤ 3 assets is written
  together with its neighbour in ONE session that exclusively owns both files,
  while big zones keep the parallel fan-out;
* every stage session gets a timeout clipped to the wall-clock actually left;
* the baseline runs against a **soft sub-budget** (``soft_budget_fraction``);
  when it is spent the stages degrade (single-shot instead of an agent session,
  fewer assets, no asset judge) instead of dying at the hard cap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    Severity,
)
from codeverse.contracts.common import TRACK_INFO, Track
from codeverse.contracts.plan import Plan, ScenePlan, ZonePlan
from codeverse.contracts.run import RoundRecord
from codeverse.conventions import to_snake
from codeverse.orchestrator import StageRunner, TaskGroup, compact_instructions
from codeverse.proc import fan_out
from codeverse.prompts import render
from codeverse.spatial.frame_motion import motion_text_for
from codeverse.spatial.render_scene import JUDGE_MAX_VIEWS, perf_detail
from codeverse.tracks import skills_hook
from codeverse.tracks.common import RunContext, ServiceUnavailable
from codeverse.tracks.generation import GenerationResult, GenerationTask, generate
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.prompting import (
    SCENE_FILES,
    base_prompt_context,
    bbox_line,
    cookbook_sections,
    judge_digest,
    reference_images,
    refine_inline_files,
)
from codeverse.tracks.repair import format_error_report
from codeverse.tracks.scene_assets import (
    MAX_ASSETS,
    AssetResult,
    asset_api_summary,
    run_asset_stage,
    select_assets,
    single_shot_ctx,
)
from codeverse.tracks.zone_layout import layout_block, layout_zones
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

SCENE_TIMES: tuple[float, float] = (0.0, 1.5)
MAX_CONSOLE_ERRORS = 8
#: a zone placing at most this many assets is small enough to share a session
SMALL_ZONE_CONTENTS = 3
#: how many small zones may share one session (file ownership stays disjoint)
MAX_ZONES_PER_BATCH = 2
#: cookbook chapters inlined into the env / zone prompts (the sessions never call
#: read_cookbook on their own — measured on scenes_v1: 0 of 20 sessions did)
ENV_RECIPES: tuple[str, ...] = (
    "Ground that reads real", "Horizon: the world must not end", "Atmosphere: time-of-day triads with numbers",
    "Dusk / night lighting recipe",
)
ZONE_RECIPES: tuple[str, ...] = (
    "Vegetation that reads real", "Rocks, cliffs and boulders that read organic",
    "Set dressing: how many props a place needs", "Scene layering: foreground, midground, background",
    "Motion you can SEE between t = 0 and t = 1.5 s",
)
ENV_TIMEOUT_S = 420
ZONE_TIMEOUT_S = 600
#: a refine session that runs for half an hour (the desert-canyon round 1 did) spends the
#: wall clock the NEXT round needed; clip it to what the hard budget still allows
REFINE_TIMEOUT_S = 900


class ScenePipeline:
    """No GLB measurement; gates from the build census + runtime; render_scene; console caps."""

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None:
        return None

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        out: list[GateReport] = []
        census_gate = census_gate_report(build)
        if census_gate is not None:
            out.append(census_gate)
        # scene_placement (2026-08-26): floating / sunken / unsupported / interpenetrating assets from
        # the probe census's placement table — the first deterministic placement gate on this track
        # (before it, the scene_v1 floating_part cap could never fire).  Advisory instrumentation:
        # a failure is a WARN finding, never an exception, so it cannot kill a round.
        try:
            from codeverse.spatial.scene_placement import placement_gate_safe

            placement = placement_gate_safe(build.census, plan=ctx.plan)
        except Exception as e:  # noqa: BLE001
            log.warning("scene placement gate unavailable: %s", e)
            placement = GateReport(gate="scene_placement", passed=True, findings=[GateFinding(
                gate="scene_placement", severity=Severity.WARN, target="scene", message=f"placement probe failed: {e}"[:400])])
        if placement is not None:
            out.append(placement)
        return out

    def render(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> RenderSet:
        r = ctx.settings.render
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        return ctx.services.render_scene(ctx.ws, ctx.ws.renders_dir(round_index), cameras=list(plan.cameras), times=SCENE_TIMES,
                                         width=r.scene_width, height=r.scene_height)

    def post_render_gates(self, ctx: RunContext, round_index: int, renders: RenderSet) -> list[GateReport]:
        errs = [e for e in renders.console_errors if e.strip()]
        findings = [GateFinding(gate="render_console", severity=Severity.ERROR, target=_guess_target(e, ctx.plan), message=e[:300],
                                fix_hint="open the named module, fix the thrown error; run the build/probe tool until no console errors")
                    for e in errs[:MAX_CONSOLE_ERRORS]]
        if renders.fps is not None and renders.fps < 20:
            findings.append(GateFinding(gate="render_console", severity=Severity.WARN, target="overall",
                                        message=f"low frame rate {renders.fps:.0f} fps" + perf_detail(renders),
                                        fix_hint="the budget is <= 200 draw calls and <= 2 M triangles: merge static geometry "
                                                 "(BufferGeometryUtils.mergeGeometries) and put anything repeated > 5x in ONE "
                                                 "InstancedMesh per material — a per-object mesh loop is what costs the frame rate"))
        out = [GateReport(gate="render_console", passed=not errs, findings=findings)]
        try:
            # scene_frames: exposure / camera-in-geometry / coverage checks from metrics.json
            # (missing metrics → a passing empty report); its ERROR findings carry fix hints
            # that build_refine_instructions routes into the next round's tasks.
            out.append(ctx.services.frame_gate(renders))
        except Exception as e:  # noqa: BLE001 — the frame gate is advisory instrumentation
            log.warning("scene frame gate failed: %s", e)
            ctx.events.emit("gate.frames_failed", round=round_index, error=f"{type(e).__name__}: {e}")
        return out

    def judge_views(self, ctx: RunContext, renders: RenderSet) -> RenderSet:
        """The ≤ 10 views the judge sees (authored@t0 first); the full set stays on disk.
        Prefers the per-view ``judge`` flags stamped at render time; legacy render
        sets (no flags) fall back to ``select_judge_views``."""
        if any(v.judge is not None for v in renders.views):
            return renders.model_copy(update={"views": [v for v in renders.views if v.judge]})
        return ctx.services.select_judge_views(renders, max_n=JUDGE_MAX_VIEWS)

    def plan_summary(self, ctx: RunContext) -> str:
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        zones = ", ".join(z.name for z in plan.zones)
        assets = ", ".join(a.name for a in plan.assets)
        return f"{plan.title}: {plan.summary} Setting: {plan.setting}. Zones: {zones}. Assets: {assets}. Cameras: {', '.join(c.name for c in plan.cameras)}."

    def judge_context(self, ws: Workspace, plan: Plan | None, round_index: int, build: BuildResult, gates: list[GateReport]) -> str:
        if not isinstance(plan, ScenePlan):
            return ""
        # the measured motion goes FIRST: this block is clipped to ~2.5 k chars in the judge
        # prompt and a long environment plan used to push everything after it off the end
        lines = [t for t in (motion_text_for(ws.renders_dir(round_index)),) if t]
        lines.append(f"Environment plan: {plan.environment}"[:1200])
        lines.append("Animation plan: " + "; ".join(plan.animation))
        lines.append("Cameras: " + "; ".join(f"{c.name} ({c.purpose})" for c in plan.cameras))
        return "\n".join(lines)


class SceneTrack(BaseTrack):
    track = Track.SCENE
    rubric = TRACK_INFO[Track.SCENE].rubric
    plan_model = ScenePlan

    def make_pipeline(self) -> ScenePipeline:
        return ScenePipeline()

    #: the baseline (assets → env → zones → assemble → round 0) may use this share of
    #: the run budget; the rest belongs to the refine rounds.
    soft_budget_fraction = 0.55

    # ------------------------------------------------------------------ stages
    def prepare(self, ctx: RunContext, runner: StageRunner) -> None:
        self.stage_skeleton(ctx, runner)
        self.ensure_materialized(ctx)
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        # assets ∥ env (2026-08-30): the env brief is a pure function of the plan — the
        # template references no asset output (0 uses of asset_api) and says "No assets/
        # zones here" — yet the two stages ran back to back.  Measured on la_boulevard:
        # assets 15 min + env 12 min sequential = 27 min of a 75-min budget; the pair
        # runs in max(15, 12) and the ~12 saved minutes are a whole refine round
        # (each measured at ~+0.13).  Workspace.commit serialises under its own lock,
        # so the two stages' commits cannot race.  One cached stage keeps resume
        # atomic: both results or neither.
        def _layouts() -> dict[str, Any]:
            """L2 zone layouts (optional accelerator): planner-model calls, never fatal."""
            try:
                model = self._planner_model
                if model is None:
                    from codeverse.models import get_chat_model

                    model = get_chat_model(ctx.spec.backends.planner)
                layouts = layout_zones(plan, model, budget=ctx.budget, events=ctx.events)
                return {k: v.model_dump(mode="json") for k, v in layouts.items()}
            except Exception as e:  # noqa: BLE001 — layouts accelerate, they must never kill
                ctx.events.emit("layout.stage_failed", error=f"{type(e).__name__}: {e}"[:300])
                return {}

        def _assets_and_env() -> dict[str, Any]:
            thunks = {"assets": lambda: run_asset_stage(ctx), "env": lambda: self._env_stage(ctx),
                      "layouts": _layouts}
            results = fan_out(list(thunks.items()), lambda kv: kv[1](), max_workers=2,
                              label="assets+env", item_name=lambda kv: kv[0])
            out: dict[str, Any] = {}
            first_exc: Exception | None = None
            for (name, _), r in zip(thunks.items(), results, strict=True):
                if isinstance(r, Exception):
                    # the sibling's paid work is already committed by its own stage body;
                    # re-raise after both have finished so nothing done is lost
                    first_exc = first_exc or r
                    ctx.events.emit("stage.failed", stage=name, error=f"{type(r).__name__}: {r}")
                else:
                    out[name] = {k: v.model_dump(mode="json") for k, v in r.items()} if name == "assets" else r
            out.setdefault("layouts", {})
            if first_exc is not None:
                raise first_exc
            return out
        both = runner.stage("assets+env", _assets_and_env,
                            inputs={"assets": plan.assets, "plan_env": plan.environment,
                                    "setting": plan.setting, "zones": plan.zones, "agent": ctx.agent_id})
        ctx.extra["layouts"] = (both or {}).get("layouts") or {}
        assets = {k: AssetResult.model_validate(v) if isinstance(v, dict) else v
                  for k, v in ((both or {}).get("assets") or {}).items()}
        # the merge map is a pure function of the plan, so a RESUMED run (cached asset
        # stage) still tells the zones which builder+variant to call
        _, alias = select_assets(list(plan.assets), MAX_ASSETS)
        ctx.extra["assets"] = assets
        ctx.extra["asset_alias"] = alias
        ctx.extra["asset_api"] = asset_api_summary(plan, assets, alias)
        runner.stage("zones", lambda: self._zones_stage(ctx), inputs={"zones": plan.zones, "asset_api": ctx.extra["asset_api"], "agent": ctx.agent_id})
        runner.stage("assemble", lambda: self._assemble_stage(ctx), inputs={"cameras": plan.cameras, "zones": [z.name for z in plan.zones]})

    # ---- degradation ------------------------------------------------------
    def _strategy(self, ctx: RunContext, stage: str) -> RunContext:
        """The context a stage should generate with: the agent normally, single-shot
        once the baseline has spent its soft sub-budget (never dying at the hard cap)."""
        reason = ctx.budget.soft_exceeded()
        if not reason:
            return ctx
        sub = None if ctx.single_shot else single_shot_ctx(ctx)
        if sub is None:
            self.note_degraded(ctx, f"{stage}: soft budget spent ({reason}); already cheapest strategy")
            return ctx
        self.note_degraded(ctx, f"{stage}: soft budget spent ({reason}) → single-shot instead of an agent session")
        return sub

    def note_degraded(self, ctx: RunContext, note: str) -> None:
        notes: list[str] = ctx.extra.setdefault("degraded", [])
        if note not in notes:
            notes.append(note)
            ctx.events.emit("budget.degraded", note=note, spent_usd=round(ctx.budget.spent.cost_usd, 4),
                            elapsed_min=round(ctx.budget.elapsed_minutes(), 2))

    # ---- skills -----------------------------------------------------------
    def _deliver_skills(self, gen: RunContext, stage_kind: str,
                        tasks: Sequence[GenerationTask]) -> list[GenerationTask]:
        """Route this stage's bundles into the workspace and inline them if single-shot.

        WHY this is not just ``steps.run_round``'s job.  A scene builds its whole baseline
        in ``prepare()``: ``_env_stage`` writes the lighting, ``_zones_stage`` the contents,
        ``_assemble_stage`` the cameras — three real agent sessions that never passed
        through ``steps.run_round``, the ONE place that called ``attach_for_round``.  The
        router has always had ``kinds=("env", "zone", "compose")`` rows for the four scene
        bundles (registry R14-R21), so they were selected for these very stages and then
        delivered to nobody: measured 2026-08-25 at 0 opens out of 30 listings, while round
        0's ``baseline_tasks`` returned ``[]`` and so listed them to a session that did not
        exist.  That is the whole of the scene bundles' "unread", and no wording could
        have fixed it.

        ``index=0`` because these stages ARE round 0's generation; there is no earlier
        round, so ``_previous_findings`` correctly returns nothing and only the plan-signal
        and kind rows can fire.  Attaching once per stage (not once per task) keeps the
        parallel zone sessions from racing each other's AGENTS.md write.
        """
        skills_hook.attach_for_round(gen, index=0, kind=stage_kind)
        return skills_hook.with_inlined_skill(gen, tasks)

    def _record_skills(self, gen: RunContext, stage_kind: str) -> None:
        """Close the stage's telemetry row so the denominator counts sessions, not stages.

        Without this a scene run reports ``skills.attached`` three times and ``skills.read``
        never, which reads in ``3dcv skills report`` as "listed, unread" — the same false
        signal the delivery gap itself produced."""
        skills_hook.record_usage(gen, index=0, kind=stage_kind)

    def _env_stage(self, ctx: RunContext) -> dict[str, Any]:
        gen = self._strategy(ctx, "env")
        prompt = render("tracks/scene_env.j2", **self._ctx(gen, recipes=cookbook_sections(gen, ENV_RECIPES)))
        ctx.record_prompt("scene_env", prompt)
        task = GenerationTask(label="env", prompt=prompt, system=self.system_prompt(ctx), files_hint=["src/env.js"], round=0, kind="env",
                              temperature=0.5, timeout_s=ctx.budget.timeout_s(ENV_TIMEOUT_S, floor_s=120),
                              images=reference_images(ctx))
        task = self._deliver_skills(gen, "env", [task])[0]
        res = generate(ctx.ws, agent_id=gen.agent_id, task=task, agent=gen.agent, model=gen.model, settings=ctx.settings,
                       budget=ctx.budget, events=ctx.events)
        self._record_skills(gen, "env")
        ctx.ws.commit("env")
        return {"ok": res.ok, "files": [c.path for c in res.files_changed], "notes": res.notes}

    def _zones_stage(self, ctx: RunContext) -> dict[str, Any]:
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        t0 = time.time()
        batches = plan_zone_batches(list(plan.zones))
        if any(len(b) > 1 for b in batches):
            ctx.events.emit("zones.batched", batches=[[z.name for z in b] for b in batches])

        # once, before the fan-out: every zone session shares one workspace, so three
        # parallel attaches would race the same AGENTS.md.  The zone rows (R3 "zone",
        # R14/R18 "zone") are identical for every batch, so one route is the right route.
        zone_gen = self._strategy(ctx, "zones")
        skills_hook.attach_for_round(zone_gen, index=0, kind="zone")

        def _one(batch: list[ZonePlan]) -> GenerationResult:
            task = skills_hook.with_inlined_skill(zone_gen, [self._zone_task(zone_gen, batch)])[0]
            return generate(ctx.ws, agent_id=zone_gen.agent_id, task=task, agent=zone_gen.agent, model=zone_gen.model, settings=ctx.settings,
                            budget=ctx.budget, events=ctx.events)

        results = fan_out(batches, _one, max_workers=ctx.settings.limits.max_parallel_agents, label="zones",
                          item_name=lambda b: "+".join(z.name for z in b))
        out: dict[str, Any] = {}
        for batch, r in zip(batches, results, strict=True):
            if isinstance(r, Exception):
                # BudgetExceeded included: the sibling zones' paid modules are still
                # recorded + committed; the guard's boundary check below stops the run.
                for zone in batch:
                    out[zone.name] = {"ok": False, "notes": f"{type(r).__name__}: {r}"}
            else:
                written = {c.path for c in r.files_changed}
                for zone in batch:
                    rel = zone_file(zone)
                    # a batched session must have produced EVERY file it owns; the skeleton
                    # left a stub at every zone path, so existence proves nothing — the file
                    # must have been reported as changed or actually rewritten in this stage
                    ok = r.ok and (len(batch) == 1 or rel in written or _touched(ctx.ws.root / rel, t0))
                    out[zone.name] = {"ok": ok, "files": [rel] if ok else [], "notes": r.notes}
        self._record_skills(zone_gen, "zone")
        ctx.ws.commit("zones")
        ctx.events.emit("zones.done", ok=[k for k, v in out.items() if v["ok"]], failed=[k for k, v in out.items() if not v["ok"]])
        ctx.budget.check()  # stage boundary: stop only after the finished zones are committed
        return out

    def _zone_task(self, ctx: RunContext, batch: list[ZonePlan]) -> GenerationTask:
        """One task for one zone, or for a batch of small zones that shares a session.

        A batched task exclusively owns every file it lists, so ``files_hint``
        attribution and the refine fan-out stay file-disjoint."""
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        names = [z.name for z in batch]
        files = [zone_file(z) for z in batch]
        recipes = cookbook_sections(ctx, ZONE_RECIPES)
        briefs = []
        for i, zone in enumerate(batch):
            neighbours = [f"{z.name}: {bbox_line(z.bbox)}" for z in plan.zones if z.name != zone.name]
            # a batched session reads ONE copy of the recipes (they are identical per zone)
            briefs.append(render("tracks/scene_zone.j2", **self._ctx(ctx, recipes=recipes if i == 0 else "", zone_name=zone.name, zone_description=zone.description,
                                                                    zone_bbox=bbox_line(zone.bbox), zone_contents=zone.contents,
                                                                    zone_file=zone_file(zone), neighbours=neighbours,
                                                                    layout=layout_block(ctx.extra.get("layouts", {}).get(zone.name)))))
        ctx.record_prompt("scene_zone", briefs[0])
        if len(batch) == 1:
            prompt, label = briefs[0], f"zone_{to_snake(names[0])}"
        else:
            header = (f"# {len(batch)} zone modules in ONE session — write ALL of: {', '.join(files)}\n\n"
                      f"You own exactly these files and nothing else. {len(batch)} complete zone briefs follow, "
                      "separated by a horizontal rule; implement each one in its own file exactly as its brief says. "
                      "They are small neighbouring zones, so keep their styling consistent and do not build into each other. "
                      "The recipes printed in the first brief apply to every zone in this session.\n")
            prompt = header + "\n\n---\n\n".join(briefs)
            label = "zones_" + "_".join(to_snake(n) for n in names)
        # the batch exclusively owns its zone files; env/scene/asset files belong to other sessions
        return GenerationTask(label=label, prompt=prompt, system=self.system_prompt(ctx), files_hint=files,
                              round=0, kind="zone", temperature=0.5, edit_only=True,
                              timeout_s=ctx.budget.timeout_s(ZONE_TIMEOUT_S, floor_s=180))

    def _assemble_stage(self, ctx: RunContext) -> dict[str, Any]:
        try:
            result = ctx.services.assemble_scene(ctx.ws, ctx.plan)
            ctx.ws.commit("assemble")
            ctx.events.emit("assemble.done", deterministic=True)
            return {"ok": True, "deterministic": True, "result": result}  # StageRunner.stage jsonables it
        except ServiceUnavailable as e:
            ctx.events.emit("assemble.fallback", reason=str(e))
        prompt = render("tracks/scene_compose.j2", **self._ctx(ctx))
        ctx.record_prompt("scene_compose", prompt)
        task = GenerationTask(label="compose", prompt=prompt, system=self.system_prompt(ctx), files_hint=["src/scene.js"], round=0,
                              kind="compose", temperature=0.4, owns_entry=True, images=reference_images(ctx))
        task = self._deliver_skills(ctx, "compose", [task])[0]
        res = generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model, settings=ctx.settings,
                       budget=ctx.budget, events=ctx.events)
        self._record_skills(ctx, "compose")
        ctx.ws.commit("compose")
        return {"ok": res.ok, "deterministic": False, "files": [c.path for c in res.files_changed], "notes": res.notes}

    def prepare_salvage(self, ctx: RunContext) -> bool:
        """The budget stopped a stage before round 0: assemble whatever the stages DID
        write (the skeleton guarantees a stub per zone) so the run still delivers a
        built, rendered, judged scene instead of no score at all."""
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        zones = [z for z in plan.zones if (ctx.ws.root / zone_file(z)).is_file()]
        if not zones:
            return False
        ctx.extra.setdefault("asset_api", asset_api_summary(plan, ctx.extra.get("assets") or {}, ctx.extra.get("asset_alias")))
        self.note_degraded(ctx, f"salvage: assembled {len(zones)}/{len(plan.zones)} zones written before the budget stop")
        ctx.plan = plan.model_copy(update={"zones": zones})
        try:
            self._assemble_stage(ctx)
        finally:
            ctx.plan = plan
        return True

    # ------------------------------------------------------------------ rounds
    def baseline_tasks(self, ctx: RunContext) -> list[GenerationTask]:
        return []  # generation happened in the stages; round 0 = build → render → judge

    def round_files_hint(self, ctx: RunContext) -> list[str]:
        return list(SCENE_FILES)

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        files = group.files or list(SCENE_FILES)
        lines = compact_instructions(group.tasks, max_lines=ctx.policy.max_instructions_per_task)
        prompt = render("tracks/scene_refine.j2", **self._ctx(ctx, recipes=refine_recipes(ctx, files), round_index=index, tasks=lines,
                                                              targets=group.targets, files=files, edit_only_these=parallel,
                                                              judge_summary=judge_digest(last),
                                                              current_files=refine_inline_files(ctx, files, scoped=False)))
        ctx.record_prompt("scene_refine", prompt)
        # parallel groups are file-disjoint by plan_refine_groups: enforce the split they promised
        return GenerationTask(label=f"refine_{group.label}" if parallel else "refine", prompt=prompt, system=self.system_prompt(ctx),
                              files_hint=files, round=index, kind="refine", temperature=0.4, edit_only=parallel,
                              timeout_s=ctx.budget.timeout_s(REFINE_TIMEOUT_S, floor_s=180, soft=False))

    def _rebuild_task(self, ctx: RunContext, last: RoundRecord, index: int) -> GenerationTask:
        """One repair task carrying the structured error report (build + lint + census
        errors), targeted at the failing module when the build names one."""
        lint = next((g for g in last.gates if g.gate.startswith("lint")), GateReport(gate="lint", passed=True))
        report = format_error_report(last.build, lint, ctx.cookbook_text) if last.build else "build did not run"
        census = census_gate_report(last.build) if last.build else None
        lines = [report] + [f"- {f.as_line()}" for f in (census.errors if census else [])][:8]
        files: list[str] = []
        if last.build is not None and last.build.error_file:
            rel = last.build.error_file.removeprefix(str(ctx.ws.root)).lstrip("/")
            if (ctx.ws.root / rel).is_file():
                files = [rel]
        files = files or self.round_files_hint(ctx)
        prompt = render("tracks/scene_refine.j2", **self._ctx(ctx, recipes="", round_index=index, tasks=lines, targets=["build"],
                                                             files=files, edit_only_these=False,
                                                             judge_summary="(no judgment: the scene did not build — fix the errors above first)",
                                                             current_files=refine_inline_files(ctx, files, scoped=False)))
        ctx.record_prompt("scene_refine", prompt)
        return GenerationTask(label="rebuild", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="rebuild", temperature=0.7, thinking="high",
                              timeout_s=ctx.budget.timeout_s(REFINE_TIMEOUT_S, floor_s=180, soft=False))

    # ------------------------------------------------------------------ helpers
    def _ctx(self, ctx: RunContext, **extra: Any) -> dict[str, Any]:
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        zones_table = "\n".join(f"- {z.name}: {z.description} — {bbox_line(z.bbox)}; contents: {', '.join(z.contents) or '-'}" for z in plan.zones)
        cameras = "\n".join(f"- {c.name}: position ({c.position[0]:.1f}, {c.position[1]:.1f}, {c.position[2]:.1f}) look_at "
                            f"({c.look_at[0]:.1f}, {c.look_at[1]:.1f}, {c.look_at[2]:.1f}) fov {c.fov:.0f} — {c.purpose}" for c in plan.cameras)
        effects = "\n".join(f"- {e.name} ({e.kind}, on {e.target or 'scene'}): {e.description}" for e in plan.effects) or "(none)"
        return base_prompt_context(
            ctx, title=plan.title, setting=plan.setting, mood=plan.mood, bounds=bbox_line(plan.bounds), environment=plan.environment,
            zones_table=zones_table, cameras=cameras, effects=effects, animation="; ".join(plan.animation) or "(none)",
            asset_api=ctx.extra.get("asset_api", "(no assets)"), **extra)


# ----------------------------------------------------------------------------- helpers
def refine_recipes(ctx: RunContext, files: Sequence[str]) -> str:
    """The cookbook chapters that match the files this refine task owns (env / zones)."""
    names: list[str] = []
    if any(f.endswith("env.js") for f in files):
        names += list(ENV_RECIPES)
    if any("/zones/" in f for f in files) or not names:
        names += list(ZONE_RECIPES)
    return cookbook_sections(ctx, names)


def zone_file(zone: ZonePlan) -> str:
    return f"src/zones/{to_snake(zone.name)}.js"


def _touched(path: Any, since: float) -> bool:
    """Was ``path`` (re)written after ``since``?  (mtime beats existence: every zone
    path already holds a skeleton stub.)"""
    try:
        return path.is_file() and path.stat().st_mtime > since
    except OSError:
        return False


def plan_zone_batches(zones: list[ZonePlan], *, small_max: int = SMALL_ZONE_CONTENTS,
                      max_per_batch: int = MAX_ZONES_PER_BATCH) -> list[list[ZonePlan]]:
    """Group consecutive SMALL zones (≤ ``small_max`` asset placements) into shared
    sessions; big zones keep a session of their own.

    Plan order is preserved, so a batch is always a pair of neighbours and the
    batching is deterministic (stage-hash stable across resumes)."""
    batches: list[list[ZonePlan]] = []
    pending: list[ZonePlan] = []
    for z in zones:
        if len(z.contents) > small_max:
            if pending:
                batches.append(pending)
                pending = []
            batches.append([z])
            continue
        pending.append(z)
        if len(pending) >= max_per_batch:
            batches.append(pending)
            pending = []
    if pending:
        batches.append(pending)
    return batches


def census_gate_report(build: BuildResult) -> GateReport | None:
    """Turn scene census error lists (console/shader) into a gate, when present."""
    c = build.census or {}
    findings: list[GateFinding] = []
    for key, gate in (("console_errors", "scene_probe"), ("shader_errors", "shader_probe"), ("errors", "scene_probe")):
        for e in c.get(key, []) or []:
            text = e if isinstance(e, str) else str(e)
            findings.append(GateFinding(gate=gate, severity=Severity.ERROR, message=text[:300],
                                        fix_hint="fix the module named in the error; rerun the build tool until the probe is clean"))
    if not findings and not c:
        return None
    return GateReport(gate="scene_census", passed=not findings, findings=findings)


def _guess_target(error: str, plan: Any) -> str:
    low = error.lower()
    for attr in ("zones", "assets"):
        for item in getattr(plan, attr, None) or []:
            if to_snake(item.name) in low:
                return item.name
    if "env.js" in low:
        return "env"
    if "scene.js" in low:
        return "composition"
    return "overall"
