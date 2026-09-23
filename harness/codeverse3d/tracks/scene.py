"""SceneTrack: the plan, the pre-round stages (``SceneTrack.stages``: the whole baseline), rounds.

Language: scene_threejs.  Generation is staged (each stage cached by
``StageRunner`` for resume); the round loop then builds (probe + shaders — the
build's two gate reports join the round's gates),
renders authored cameras + orbit at t=0 and t=1.5, judges with ``scene_v1``
(console/shader errors become gate ERRORS) and dispatches refine tasks per
zone / asset / env / camera, in parallel when file-disjoint.

Cost/latency shaping (the baseline used to eat the whole budget, leaving the
refine rounds nothing):

* assets are single-shot by default (``scene_assets``);
* **one session writes every zone** (D70) and exclusively owns their files;
  ``scene.js`` is assembled deterministically, without a session;
* every stage session gets a timeout clipped to the wall-clock actually left;
* the baseline runs against a **soft sub-budget** (``soft_budget_fraction``);
  when it is spent the stages degrade (single-shot instead of an agent session,
  fewer assets, no asset judge) instead of dying at the hard cap.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    RenderSet,
    Severity,
)
from codeverse3d.contracts.common import TRACK_INFO, Track
from codeverse3d.contracts.plan import Plan, ScenePlan, ZonePlan
from codeverse3d.contracts.run import RoundRecord
from codeverse3d.conventions import to_snake
from codeverse3d.judges.base import judged_subset
from codeverse3d.languages.scene_threejs import zone_file
from codeverse3d.models import get_chat_model
from codeverse3d.orchestrator import BudgetExceeded, TaskGroup, compact_instructions
from codeverse3d.prompts import render
from codeverse3d.spatial.frame_motion import motion_text_for
from codeverse3d.spatial.render_scene import perf_detail
from codeverse3d.spatial.scene_placement import placement_gate
from codeverse3d.texturing.plan import texture_pack_prompt
from codeverse3d.tracks import skills_hook
from codeverse3d.tracks.common import RunContext, generate_for, single_shot_ctx
from codeverse3d.tracks.generation import GenerationResult, GenerationTask
from codeverse3d.tracks.lifecycle import CLOCK, MATERIALIZE, SKELETON, BaseTrack, StageNode
from codeverse3d.tracks.prompting import (
    base_prompt_context,
    bbox_line,
    cookbook_sections,
    judge_digest,
    reference_images,
    refine_inline_files,
)
from codeverse3d.tracks.repair import format_error_report
from codeverse3d.tracks.scene_assets import (
    MAX_ASSETS,
    AssetResult,
    asset_api_summary,
    read_dedupe_note,
    run_asset_stage,
    select_assets,
)
from codeverse3d.tracks.zone_layout import layout_block, layout_zones
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)

SCENE_TIMES: tuple[float, float] = (0.0, 1.5)
MAX_CONSOLE_ERRORS = 8
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
    """No GLB measurement; the placement gate from the build's census (the build's own probe and
    preflight reports join the round through ``BuildResult.gates``); render_scene; console caps."""

    def measure(self, ctx: RunContext, build: BuildResult) -> Measurement | None:
        return None

    def gates(self, ctx: RunContext, round_index: int, build: BuildResult, measurement: Measurement | None) -> list[GateReport]:
        # scene_placement (2026-08-26): floating / sunken / unsupported / interpenetrating assets from
        # the probe census's placement table — the first deterministic placement gate on this track
        # (before it, the scene_v1 floating_part cap could never fire) — plus the plan checks.  The
        # check_placement tool returns the same verdict.  Advisory instrumentation: a failure is a
        # WARN finding, never an exception, so it cannot kill a round.
        placement = placement_gate(ctx.ws, build.census, ctx.plan)
        return [placement] if placement is not None else []

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
        # `hardware_fps`, not `fps`: a number measured on SwiftShader is the box's, and
        # telling the agent to merge geometry because the CPU rasteriser is slow sends it
        # optimising a scene that was never the problem (2026-09-05, three cells of
        # scenes_v1 measured 2-7 fps on SwiftShader while a fourth measured 11.5 on an
        # RTX 6000 — four different renderers, one gate threshold).
        if renders.hardware_fps is not None and renders.hardware_fps < 20:
            findings.append(GateFinding(gate="render_console", severity=Severity.WARN, target="overall",
                                        message=f"low frame rate {renders.hardware_fps:.0f} fps" + perf_detail(renders),
                                        fix_hint="merge static geometry "
                                                 "(BufferGeometryUtils.mergeGeometries) and put anything repeated > 5x in ONE "
                                                 "InstancedMesh per material — a per-object mesh loop is what costs the frame rate"))
        out = [GateReport.of("render_console", findings)]
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
        """The ≤ 10 views the judge sees (authored@t0 first); the full set stays on disk:
        the per-view ``judge`` flags ``render_scene`` stamps on every set it returns."""
        return judged_subset(renders)  # type: ignore[return-value]

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


def _plan_agent(ctx: RunContext) -> dict[str, Any]:
    # the WHOLE plan (+ agent), not enumerated fields (2026-08-31): a mood/title/bounds/camera-only
    # re-plan used to hit a stale cached stage
    return {"plan": ctx.plan, "agent": ctx.agent_id}


def _pack(ctx: RunContext) -> dict[str, Any]:
    # the texture pack is part of a key only when it has files: a run without one keeps its keys
    return {"textures": ctx.extra["textures"]} if ctx.extra.get("textures") else {}


class SceneTrack(BaseTrack):
    track = Track.SCENE
    rubric = TRACK_INFO[Track.SCENE].rubric
    plan_model = ScenePlan
    #: the pre-round graph writes the whole baseline; round 0 builds, renders and judges it
    stages = (
        SKELETON, MATERIALIZE,
        # first and alone: env and zones name its files (Settings.scene_textures: off by default, and why)
        StageNode("textures", lambda t, c: t._textures_stage(c), key=lambda c: {"plan": c.plan}, when=lambda s: s.scene_textures),
        # side by side (2026-08-30): the env brief reads no asset output — la_boulevard's 15 + 12 min ran
        # in max(15, 12), a whole refine round saved.  Each is its OWN cached stage (2026-08-31): a failing
        # sibling never costs a succeeded one its paid result.  Layouts: L2 zone layouts, an accelerator
        # (C3D_ZONE_LAYOUTS, default on), riding along at no wall clock (beb6605)
        (StageNode("assets", lambda t, c: run_asset_stage(c), key=_plan_agent),
         StageNode("env", lambda t, c: t._env_stage(c), key=lambda c: {**_plan_agent(c), **_pack(c)}),
         StageNode("layouts", lambda t, c: t._layouts_stage(c), key=_plan_agent, when=lambda s: s.zone_layouts)),
        StageNode("asset_api", lambda t, c: t._asset_api(c)),
        # ONE session writes every zone (D70)
        StageNode("zones", lambda t, c: t._zones_stage(c), key=lambda c: {
            "plan": c.plan, "asset_api": c.extra["asset_api"], "layouts": c.extra.get("layouts", {}), "agent": c.agent_id,
            **_pack(c)}),
        # after the zones are recorded: a run the clock stops here keeps them (salvage assembles
        # them; a resume serves them instead of paying the session again)
        CLOCK,
        # scene.js, deterministic: no session
        StageNode("assemble", lambda t, c: t._assemble_stage(c), key=lambda c: {"plan": c.plan}),
    )

    def make_pipeline(self) -> ScenePipeline:
        return ScenePipeline()

    #: the baseline (the pre-round stages → round 0) may use this share of the run budget; the
    #: rest belongs to the refine rounds.
    soft_budget_fraction = 0.55

    # ------------------------------------------------------------------ stages
    def _layouts_stage(self, ctx: RunContext) -> dict[str, Any]:
        """L2 zone layouts (optional accelerator): planner-model calls, never fatal."""
        try:
            model = self._planner_model
            if model is None:
                model = get_chat_model(ctx.spec.backends.planner)
            layouts = layout_zones(ctx.plan, model, budget=ctx.budget, events=ctx.events)
            return {k: v.model_dump(mode="json") for k, v in layouts.items()}
        except Exception as e:  # noqa: BLE001 — layouts accelerate, they must never kill
            ctx.events.emit("layout.stage_failed", error=f"{type(e).__name__}: {e}"[:300])
            return {}

    def _asset_api(self, ctx: RunContext) -> str:
        """What the zones are told about the assets.  The asset results come back revived (a cached
        stage is JSON), with the merge map the STAGE used: its cap depends on the soft budget at the
        time (a degraded run folded more), so it is read back for a resumed run and the zones to
        agree; the plan-only recomputation is the fallback for a workspace without the note."""
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        assets = {k: AssetResult.model_validate(v) if isinstance(v, dict) else v for k, v in (ctx.extra.get("assets") or {}).items()}
        alias = read_dedupe_note(ctx.ws)
        if alias is None:
            _, alias = select_assets(list(plan.assets), MAX_ASSETS)
        ctx.extra["assets"], ctx.extra["asset_alias"] = assets, alias
        return asset_api_summary(plan, assets, alias)

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
            ctx.events.emit("budget.degraded", note=note, elapsed_min=round(ctx.budget.elapsed_minutes(), 2))

    # ---- the stage session ------------------------------------------------
    def _stage_session(self, ctx: RunContext, stage: str, kind: str,
                       task_for: Callable[[RunContext], GenerationTask]) -> GenerationResult:
        """The one way a baseline stage (env, zones) runs its session: the strategy the soft budget
        leaves (``_strategy``), the task built for it, this stage's skills routed into the workspace
        and — single-shot — inlined, the session, its skills telemetry and the stage's commit.  A
        session that raises still leaves its telemetry and its commit before the exception goes on.

        The skills are this hook's job, not ``steps.run_round``'s: a scene builds its whole baseline
        in its pre-round ``stages``, in sessions that never pass through ``run_round`` (``scene.js`` is
        assembled, not written), so the router's ``kinds=("env", "zone")`` rows were selected and
        delivered to nobody — 0 opens out of 30 listings, measured 2026-08-25.  ``index=0``: these
        stages ARE round 0's generation.  One attach per stage keeps it to one AGENTS.md write, and
        closing the telemetry row per session keeps ``3dcode skills report`` from reading a stage's
        listing as "listed, unread"."""
        gen = self._strategy(ctx, stage)
        task = task_for(gen)
        skills_hook.attach_for_round(gen, index=0, kind=kind)
        try:
            return generate_for(gen, skills_hook.with_inlined_skill(gen, [task])[0])
        finally:
            skills_hook.record_usage(gen, index=0, kind=kind)
            ctx.ws.commit(stage)

    def _textures_stage(self, ctx: RunContext) -> dict[str, Any]:
        """Generate the scene's tileable texture pack into ``public/textures/``.

        Runs BEFORE env / zones, because its whole point is that those prompts can name
        the files.  Advisory: a pack that cannot be generated leaves the run exactly as it
        was before this stage existed (an empty manifest renders no prompt block)."""
        from codeverse3d.reference import get_image_model
        from codeverse3d.texturing.plan import scene_texture_pack

        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        try:
            pack = scene_texture_pack(plan, ctx.ws.public / "textures", get_image_model(),
                                      ctx.spec.backends.planner, cache_dir=ctx.settings.cache_dir / "textures")
        except Exception as e:  # noqa: BLE001 — textures accelerate, they must never kill a run
            ctx.events.emit("textures.stage_failed", error=f"{type(e).__name__}: {e}"[:300])
            return {}
        manifest = pack.manifest()
        ctx.ws.commit("textures")
        ctx.events.emit("textures.done", n=len(manifest), cost_usd=round(pack.usage.cost_usd, 4), source=pack.source)
        return manifest

    def _env_stage(self, ctx: RunContext) -> dict[str, Any]:
        res = self._stage_session(ctx, "env", "env", self._env_task)
        return {"ok": res.ok, "files": [c.path for c in res.files_changed], "notes": res.notes}

    def _env_task(self, ctx: RunContext) -> GenerationTask:
        prompt = render("tracks/scene_env.j2", **self._ctx(ctx, recipes=cookbook_sections(ctx, ENV_RECIPES)))
        ctx.record_prompt("scene_env", prompt)
        return GenerationTask(label="env", prompt=prompt, system=self.system_prompt(ctx), files_hint=["src/env.js"], round=0, kind="env",
                              temperature=0.5, timeout_s=ctx.budget.timeout_s(ENV_TIMEOUT_S, floor_s=120),
                              images=reference_images(ctx))

    def _zones_stage(self, ctx: RunContext) -> dict[str, Any]:
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        t0 = time.time()
        # one author for the whole world (D70; the fan-out control arm was removed 2026-09-21)
        zones = list(plan.zones)
        if len(zones) > 1:
            ctx.events.emit("zones.batched", batches=[[z.name for z in zones]])
        try:
            r = self._stage_session(ctx, "zones", "zone", lambda gen: self._zone_task(gen, zones))
        except Exception as e:  # noqa: BLE001 — BudgetExceeded included: the stage is still
            # recorded + committed; the boundary check after the stage stops the run
            log.warning("zones session failed: %s: %s", type(e).__name__, e)
            # a clock that tripped as a FINISHED session was charged: what it wrote is recorded as
            # written (salvage assembles it, a resume serves it), the stop as a note beside it
            stopped = isinstance(e, BudgetExceeded)
            out = {}
            for zone in zones:
                done = stopped and _touched(ctx.ws.root / zone_file(zone.name), t0)
                out[zone.name] = {"ok": done, "files": [zone_file(zone.name)] if done else [],
                                  "notes": f"{type(e).__name__}: {e}"}
        else:
            written = {c.path for c in r.files_changed}
            out = {}
            for zone in zones:
                rel = zone_file(zone.name)
                # a session that owns several zones must have produced EVERY file; the skeleton
                # left a stub at every zone path, so existence proves nothing — the file
                # must have been reported as changed or actually rewritten in this stage
                ok = r.ok and (len(zones) == 1 or rel in written or _touched(ctx.ws.root / rel, t0))
                out[zone.name] = {"ok": ok, "files": [rel] if ok else [], "notes": r.notes}
        ctx.events.emit("zones.done", ok=[k for k, v in out.items() if v["ok"]], failed=[k for k, v in out.items() if not v["ok"]])
        return out

    def _zone_task(self, ctx: RunContext, batch: list[ZonePlan]) -> GenerationTask:
        """One task for one zone, or for every zone of the scene in one session (D70).

        A batched task exclusively owns every file it lists, so ``files_hint``
        attribution and the refine fan-out stay file-disjoint."""
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        names = [z.name for z in batch]
        files = [zone_file(z.name) for z in batch]
        recipes = cookbook_sections(ctx, ZONE_RECIPES)
        briefs = []
        for i, zone in enumerate(batch):
            neighbours = [f"{z.name}: {bbox_line(z.bbox)}" for z in plan.zones if z.name != zone.name]
            # a batched session reads ONE copy of the recipes (they are identical per zone)
            briefs.append(render("tracks/scene_zone.j2", **self._ctx(ctx, recipes=recipes if i == 0 else "", zone_name=zone.name, zone_description=zone.description,
                                                                    zone_bbox=bbox_line(zone.bbox), zone_contents=zone.contents,
                                                                    zone_file=zone_file(zone.name), neighbours=neighbours,
                                                                    layout=layout_block(ctx.extra.get("layouts", {}).get(zone.name)))))
        if len(batch) == 1:
            prompt, label = briefs[0], f"zone_{to_snake(names[0])}"
        else:
            header = (f"# {len(batch)} zone modules in ONE session — write ALL of: {', '.join(files)}\n\n"
                      f"You own exactly these files and nothing else. {len(batch)} complete zone briefs follow, "
                      "separated by a horizontal rule; implement each one in its own file exactly as its brief says. "
                      + "These are EVERY zone of the scene and you are its one author: keep scale, materials and "
                        "placement coherent across them — nothing floats, nothing interpenetrates a neighbour, every "
                        "content at the plan's size, and the zones meet at their shared edges as one place. "
                        f"This session covers {len(batch)} zones: build EACH zone to "
                        "its brief's full density counts and dressing before you finish — one author measured "
                        "2026-09-08 finished four zones in 5 minutes as a block-out (\"missing stove\", \"shelves "
                        "missing\", \"primitive tools\") and scored 0.32 where the brief-by-brief fan-out reached 0.60. "
                      + "The recipes printed in the first brief apply to every zone in this session.\n")
            prompt = header + "\n\n---\n\n".join(briefs)
            label = "zones_" + "_".join(to_snake(n) for n in names)
        ctx.record_prompt("scene_zone", prompt)   # the whole prompt the session read, not its first brief
        # the batch exclusively owns its zone files; env/scene/asset files belong to other sessions
        # the window grows with the batch: one author writing four zones is four zones of work
        return GenerationTask(label=label, prompt=prompt, system=self.system_prompt(ctx), files_hint=files,
                              round=0, kind="zone", temperature=0.5, edit_only=True,
                              timeout_s=ctx.budget.timeout_s(ZONE_TIMEOUT_S * max(1, len(batch)), floor_s=180))

    def _assemble_stage(self, ctx: RunContext) -> dict[str, Any]:
        result = ctx.services.assemble_scene(ctx.ws, ctx.plan)
        ctx.ws.commit("assemble")
        ctx.events.emit("assemble.done", deterministic=True)
        return {"ok": True, "deterministic": True, "result": result}  # StageRunner.stage jsonables it

    def prepare_salvage(self, ctx: RunContext) -> bool:
        """The budget stopped a stage before round 0: assemble whatever the stages DID
        write (the skeleton guarantees a stub per zone) so the run still delivers a
        built, rendered, judged scene instead of no score at all."""
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        zones = [z for z in plan.zones if (ctx.ws.root / zone_file(z.name)).is_file()]
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

    # ------------------------------------------------------------------ refine (scaffold hooks)
    def refine_file_for_target(self, ctx: RunContext) -> Callable[[str], list[str]]:
        """The runtime's layout, with the dedupe note: a merged asset is fixed in its survivor's factory."""
        return lambda target: ctx.runtime.files_for(ctx.plan, target, alias=ctx.extra.get("asset_alias"))

    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        files = group.files or ctx.runtime.expected_files(ctx.plan)
        lines = compact_instructions(group.tasks, max_lines=ctx.policy.max_instructions_per_task)
        prompt = render("tracks/scene_refine.j2", **self._ctx(ctx, recipes=refine_recipes(ctx, files), round_index=index, tasks=lines,
                                                              targets=group.targets, files=files, edit_only_these=parallel,
                                                              judge_summary=judge_digest(last),
                                                              current_files=refine_inline_files(ctx, files)))
        ctx.record_prompt("scene_refine", prompt)
        # parallel groups are file-disjoint by plan_refine_groups: enforce the split they promised
        return GenerationTask(label=f"refine_{group.label}" if parallel else "refine", prompt=prompt, system=self.system_prompt(ctx),
                              files_hint=files, round=index, kind="refine", temperature=0.4, edit_only=parallel,
                              timeout_s=ctx.budget.timeout_s(REFINE_TIMEOUT_S, floor_s=180, soft=False))

    def _rebuild_task(self, ctx: RunContext, last: RoundRecord, index: int) -> GenerationTask:
        """One repair task carrying the structured error report (build + lint; the probe's
        and the preflight's findings are the build's log tail), targeted at the failing
        module when the build names one."""
        lint = next((g for g in last.gates if g.gate.startswith("lint")), GateReport(gate="lint", passed=True))
        report = format_error_report(last.build, lint, ctx.cookbook_text) if last.build else "build did not run"
        files: list[str] = []
        if last.build is not None and last.build.error_file:
            rel = last.build.error_file.removeprefix(str(ctx.ws.root)).lstrip("/")
            if (ctx.ws.root / rel).is_file():
                files = [rel]
        files = files or ctx.runtime.expected_files(ctx.plan)
        prompt = render("tracks/scene_refine.j2", **self._ctx(ctx, recipes="", round_index=index, tasks=[report], targets=["build"],
                                                             files=files, edit_only_these=False,
                                                             judge_summary="(no judgment: the scene did not build — fix the errors above first)",
                                                             current_files=refine_inline_files(ctx, files)))
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
            interior=plan.interior,
            zones_table=zones_table, cameras=cameras, effects=effects, animation="; ".join(plan.animation) or "(none)",
            asset_api=ctx.extra.get("asset_api", "(no assets)"),
            textures=texture_pack_prompt(ctx.extra.get("textures") or {}), **extra)


# ----------------------------------------------------------------------------- helpers
def refine_recipes(ctx: RunContext, files: Sequence[str]) -> str:
    """The cookbook chapters that match the files this refine task owns (env / zones)."""
    names: list[str] = []
    if any(f.endswith("env.js") for f in files):
        names += list(ENV_RECIPES)
    if any("/zones/" in f for f in files) or not names:
        names += list(ZONE_RECIPES)
    return cookbook_sections(ctx, names)


def _touched(path: Any, since: float) -> bool:
    """Was ``path`` (re)written after ``since``?  (mtime beats existence: every zone
    path already holds a skeleton stub.)"""
    try:
        return path.is_file() and path.stat().st_mtime > since
    except OSError:
        return False


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
