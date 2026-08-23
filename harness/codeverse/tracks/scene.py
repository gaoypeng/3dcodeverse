"""SceneTrack: plan → skeleton → assets → env → zones → assemble → rounds.

Language: scene_threejs.  Generation is staged (each stage cached by
``StageRunner`` for resume); the round loop then builds (probe + shaders),
renders authored cameras + orbit at t=0 and t=1.5, judges with ``scene_v1``
(console/shader errors become gate ERRORS) and dispatches refine tasks per
zone / asset / env / camera, in parallel when file-disjoint.

Cost/latency shaping (the baseline used to eat the whole budget, leaving the
refine rounds nothing):

* assets are single-shot by default (``scene_assets`` / ``scene_asset_gen``);
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
from codeverse.orchestrator.fanout import fan_out
from codeverse.orchestrator.rounds import TaskGroup, compact_instructions
from codeverse.orchestrator.runner import StageRunner
from codeverse.prompts import render
from codeverse.spatial.render_scene import JUDGE_MAX_VIEWS
from codeverse.tracks.common import RunContext, ServiceUnavailable
from codeverse.tracks.generation import GenerationResult, GenerationTask, generate
from codeverse.tracks.lifecycle import BaseTrack
from codeverse.tracks.prompting import (
    SCENE_FILES,
    base_prompt_context,
    bbox_line,
    current_files,
    file_for_target_factory,
    judge_digest,
)
from codeverse.tracks.repair import format_error_report
from codeverse.tracks.scene_asset_gen import select_assets, single_shot_ctx
from codeverse.tracks.scene_assets import (
    MAX_ASSETS,
    AssetResult,
    asset_api_summary,
    run_asset_stage,
)
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

SCENE_TIMES: tuple[float, float] = (0.0, 1.5)
MAX_CONSOLE_ERRORS = 8
#: a zone placing at most this many assets is small enough to share a session
SMALL_ZONE_CONTENTS = 3
#: how many small zones may share one session (file ownership stays disjoint)
MAX_ZONES_PER_BATCH = 2
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
        extra = getattr(ctx.runtime, "extra_gates", None)
        if callable(extra):
            out.extend(extra(ctx.ws, build))
        census_gate = census_gate_report(build)
        if census_gate is not None:
            out.append(census_gate)
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
                                        message=f"low frame rate {renders.fps:.0f} fps", fix_hint="reduce triangle/draw counts: merge geometries, use InstancedMesh"))
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
        lines = [f"Environment plan: {plan.environment}", "Animation plan: " + "; ".join(plan.animation)]
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
        assets = runner.stage("assets", lambda: run_asset_stage(ctx), inputs={"assets": plan.assets, "agent": ctx.agent_id})
        assets = {k: AssetResult.model_validate(v) if isinstance(v, dict) else v for k, v in (assets or {}).items()}
        # the merge map is a pure function of the plan, so a RESUMED run (cached asset
        # stage) still tells the zones which builder+variant to call
        _, alias = select_assets(list(plan.assets), MAX_ASSETS)
        ctx.extra["assets"] = assets
        ctx.extra["asset_alias"] = alias
        ctx.extra["asset_api"] = asset_api_summary(plan, assets, alias)
        runner.stage("env", lambda: self._env_stage(ctx), inputs={"plan_env": plan.environment, "setting": plan.setting, "agent": ctx.agent_id})
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

    def _env_stage(self, ctx: RunContext) -> dict[str, Any]:
        gen = self._strategy(ctx, "env")
        prompt = render("tracks/scene_env.j2", **self._ctx(gen))
        ctx.record_prompt("scene_env", prompt)
        task = GenerationTask(label="env", prompt=prompt, system=self.system_prompt(ctx), files_hint=["src/env.js"], round=0, kind="env",
                              temperature=0.5, timeout_s=ctx.budget.timeout_s(ENV_TIMEOUT_S, floor_s=120))
        res = generate(ctx.ws, agent_id=gen.agent_id, task=task, agent=gen.agent, model=gen.model, settings=ctx.settings,
                       budget=ctx.budget, events=ctx.events)
        ctx.ws.commit("env")
        return {"ok": res.ok, "files": [c.path for c in res.files_changed], "notes": res.notes}

    def _zones_stage(self, ctx: RunContext) -> dict[str, Any]:
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        t0 = time.time()
        batches = plan_zone_batches(list(plan.zones))
        if any(len(b) > 1 for b in batches):
            ctx.events.emit("zones.batched", batches=[[z.name for z in b] for b in batches])

        def _one(batch: list[ZonePlan]) -> GenerationResult:
            gen = self._strategy(ctx, "zones")
            task = self._zone_task(gen, batch)
            return generate(ctx.ws, agent_id=gen.agent_id, task=task, agent=gen.agent, model=gen.model, settings=ctx.settings,
                            budget=ctx.budget, events=ctx.events)

        results = fan_out(batches, _one, max_workers=ctx.settings.limits.max_parallel_agents, label="zones",
                          item_name=lambda b: "+".join(z.name for z in b))
        out: dict[str, Any] = {}
        for batch, r in zip(batches, results, strict=True):
            if isinstance(r, Exception):
                from codeverse.orchestrator.budget import BudgetExceeded

                if isinstance(r, BudgetExceeded):
                    raise r
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
        ctx.ws.commit("zones")
        ctx.events.emit("zones.done", ok=[k for k, v in out.items() if v["ok"]], failed=[k for k, v in out.items() if not v["ok"]])
        return out

    def _zone_task(self, ctx: RunContext, batch: list[ZonePlan]) -> GenerationTask:
        """One task for one zone, or for a batch of small zones that shares a session.

        A batched task exclusively owns every file it lists, so ``files_hint``
        attribution and the refine fan-out stay file-disjoint."""
        plan: ScenePlan = ctx.plan  # type: ignore[assignment]
        names = [z.name for z in batch]
        files = [zone_file(z) for z in batch]
        briefs = []
        for zone in batch:
            neighbours = [f"{z.name}: {bbox_line(z.bbox)}" for z in plan.zones if z.name != zone.name]
            briefs.append(render("tracks/scene_zone.j2", **self._ctx(ctx, zone_name=zone.name, zone_description=zone.description,
                                                                    zone_bbox=bbox_line(zone.bbox), zone_contents=zone.contents,
                                                                    zone_file=zone_file(zone), neighbours=neighbours)))
        ctx.record_prompt("scene_zone", briefs[0])
        if len(batch) == 1:
            prompt, label = briefs[0], f"zone_{to_snake(names[0])}"
        else:
            header = (f"# {len(batch)} zone modules in ONE session — write ALL of: {', '.join(files)}\n\n"
                      f"You own exactly these files and nothing else. {len(batch)} complete zone briefs follow, "
                      "separated by a horizontal rule; implement each one in its own file exactly as its brief says. "
                      "They are small neighbouring zones, so keep their styling consistent and do not build into each other.\n")
            prompt = header + "\n\n---\n\n".join(briefs)
            label = "zones_" + "_".join(to_snake(n) for n in names)
        return GenerationTask(label=label, prompt=prompt, system=self.system_prompt(ctx), files_hint=files,
                              round=0, kind="zone", temperature=0.5, timeout_s=ctx.budget.timeout_s(ZONE_TIMEOUT_S, floor_s=180))

    def _assemble_stage(self, ctx: RunContext) -> dict[str, Any]:
        try:
            result = ctx.services.assemble_scene(ctx.ws, ctx.plan)
            ctx.ws.commit("assemble")
            ctx.events.emit("assemble.done", deterministic=True)
            return {"ok": True, "deterministic": True, "result": _jsonable(result)}
        except ServiceUnavailable as e:
            ctx.events.emit("assemble.fallback", reason=str(e))
        prompt = render("tracks/scene_compose.j2", **self._ctx(ctx))
        ctx.record_prompt("scene_compose", prompt)
        task = GenerationTask(label="compose", prompt=prompt, system=self.system_prompt(ctx), files_hint=["src/scene.js"], round=0,
                              kind="compose", temperature=0.4)
        res = generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model, settings=ctx.settings,
                       budget=ctx.budget, events=ctx.events)
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
    def refine_file_for_target(self, ctx: RunContext) -> Any:
        return file_for_target_factory(ctx)

    def _refine_task(self, ctx: RunContext, group: TaskGroup, last: RoundRecord, index: int, *, parallel: bool) -> GenerationTask:
        files = group.files or list(SCENE_FILES)
        lines = compact_instructions(group.tasks, max_lines=ctx.policy.max_instructions_per_task)
        prompt = render("tracks/scene_refine.j2", **self._ctx(ctx, round_index=index, tasks=lines,
                                                              targets=group.targets, files=files, edit_only_these=parallel,
                                                              judge_summary=judge_digest(last),
                                                              current_files=current_files(ctx, files) if ctx.single_shot else {}))
        ctx.record_prompt("scene_refine", prompt)
        return GenerationTask(label=f"refine_{group.label}" if parallel else "refine", prompt=prompt, system=self.system_prompt(ctx),
                              files_hint=files, round=index, kind="refine", temperature=0.4,
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
        prompt = render("tracks/scene_refine.j2", **self._ctx(ctx, round_index=index, tasks=lines, targets=["build"],
                                                             files=files, edit_only_these=False,
                                                             judge_summary="(no judgment: the scene did not build — fix the errors above first)",
                                                             current_files=current_files(ctx, files) if ctx.single_shot else {}))
        ctx.record_prompt("scene_refine", prompt)
        return GenerationTask(label="rebuild", prompt=prompt, system=self.system_prompt(ctx), files_hint=files, round=index,
                              kind="rebuild", temperature=0.7, thinking="high",
                              timeout_s=ctx.budget.timeout_s(REFINE_TIMEOUT_S, floor_s=180, soft=False))

    # ------------------------------------------------------------------ helpers
    def system_prompt(self, ctx: RunContext) -> str:
        return ("You are an expert three.js + GLSL graphics programmer writing RAW ESM modules for a multi-file scene. "
                "No SDKs, no DOM, no fetch, no CDN imports: `import * as THREE from 'three'` only. Exact numbers beat adjectives.")

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


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, (list, tuple)):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    return str(obj)
